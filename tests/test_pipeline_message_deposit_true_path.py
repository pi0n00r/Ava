# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava

import asyncio
import contextlib
import json
import logging
import time
import uuid
from types import SimpleNamespace

import aiohttp
import pytest

from src.audio.audiosocket_server import AudioSocketServer, TYPE_AUDIO, TYPE_TERMINATE, TYPE_UUID
from src.config import AppConfig
from src.core.models import CallSession
from src.core.pipeline_message_deposit import PipelineMessageDepositGuard
from src.engine import Engine
from src.pipelines.base import LLMComponent, LLMResponse, STTComponent, TTSComponent
from src.tools.http.in_call_lookup import create_in_call_http_tool
from src.tools.registry import ToolRegistry


class _ResultSTT(STTComponent):
    supports_streaming = True

    def __init__(self):
        self.results = asyncio.Queue()
        self.started = asyncio.Event()

    async def open_call(self, call_id, options):
        return None

    async def transcribe(self, call_id, audio_pcm16, sample_rate_hz, options):
        raise AssertionError("streaming path must not use buffered transcription")

    async def start_stream(self, call_id, options, *, sample_rate_hz, fmt):
        self.started.set()

    async def send_audio(self, call_id, audio, *, fmt="pcm16_16k"):
        return None

    async def iter_results(self, call_id):
        while True:
            value = await self.results.get()
            if value is None:
                return
            yield value

    async def stop_stream(self, call_id):
        self.results.put_nowait(None)


class _DepositLLM(LLMComponent):
    def __init__(self, *, via_followup):
        self.calls = 0
        self.via_followup = via_followup

    @staticmethod
    def _deposit_call():
        return LLMResponse(
            text="",
            tool_calls=[{
                "id": "deposit-once",
                "name": "pbx_message_deposit",
                "parameters": {
                    "target": "Geary",
                    "message": "The sky is green.",
                },
            }],
        )

    async def generate(self, call_id, transcript, context, options):
        self.calls += 1
        if self.calls == 1 and self.via_followup:
            assert transcript == "Yes."
            return LLMResponse(
                text="",
                tool_calls=[{
                    "id": "preflight-once",
                    "name": "deposit_preflight",
                    "parameters": {},
                }],
            )
        if self.via_followup:
            assert self.calls == 2
            assert transcript == ""
        else:
            assert self.calls == 1
            assert transcript == "Yes."
        return self._deposit_call()


class _NoConfirmationInference(LLMComponent):
    supports_streaming = True

    def __init__(self):
        self.calls = 0
        self.stream_calls = 0

    async def generate(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("confirmed deposit must not start another inference")

    async def generate_stream(self, *args, **kwargs):
        self.stream_calls += 1
        raise AssertionError("confirmed deposit must not start streaming inference")
        yield ""

class _PendingBargeInLLM(LLMComponent):
    """Native cognition can select PBX, never replace the pending envelope."""

    supports_streaming = False

    def __init__(self):
        self.calls = 0
        self.stream_calls = 0

    async def generate(self, call_id, transcript, context, options):
        self.calls += 1
        assert transcript in ("Leave a message for Gary.", "Did you get that?")
        return _DepositLLM._deposit_call()


class _RecordingTTS(TTSComponent):
    downstream_mode_override = "stream"

    def __init__(self):
        self.texts = []
        self.started_at = []
        self.ambience_active_at_start = []
        self.send_count_at_start = []
        self.on_start = None
        self.send_counter = None

    async def synthesize(self, call_id, text, options):
        self.texts.append(text)
        self.started_at.append(time.monotonic())
        self.ambience_active_at_start.append(
            bool(self.on_start and self.on_start())
        )
        self.send_count_at_start.append(
            int(self.send_counter()) if self.send_counter else 0
        )
        yield b"\x00\x04\x00\xfc" * 80


class _Resolution:
    def __init__(self, stt, llm, tts):
        self.pipeline_name = "true_path"
        self.stt_key = "stub_stt"
        self.stt_adapter = stt
        self.llm_adapter = llm
        self.tts_adapter = tts
        self.stt_options = {"streaming": True, "chunk_ms": 80}
        self.llm_options = {}
        self.tts_options = {
            "format": {"encoding": "slin", "sample_rate_hz": 8000}
        }
        self.prepared = True

    def component_summary(self):
        return {"stt": "stub", "llm": "stub", "tts": "stub"}


class _Watchdog:
    def __init__(self):
        self.suspensions = []
        self.stopped = []

    async def set_suspended(self, call_id, value):
        self.suspensions.append(bool(value))

    async def note_activity(self, *args):
        return None

    async def note_processing(self, *args):
        return None

    async def note_input_state(self, *args):
        return None

    async def mark_ready(self, *args):
        return None

    def has_call(self, *args):
        return False

    async def stop(self, call_id):
        self.stopped.append(call_id)


async def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("condition not reached")
        await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    "via_followup,direct_confirmation,outcome",
    [
        (False, False, "success"),
        (True, False, "success"),
        (False, True, "success"),
        (False, True, "cancel"),
        (False, True, "http-failure"),
        (False, True, "missing-spoken-response"),
        (False, True, "barge-in-pending"),
        (False, True, "terminal-tts-correction"),
        (False, True, "terminal-tts-unchanged-retry"),
        (False, True, "barge-in-terminal-absence"),
    ],
    ids=[
        "legacy-primary", "legacy-followup", "main-direct-confirmation",
        "main-pending-cancel", "main-http-failure", "main-invalid-direct-result",
        "main-barge-in-pending",
        "main-terminal-tts-correction",
        "main-terminal-tts-unchanged-retry",
        "main-barge-in-terminal-absence",
    ],
)
@pytest.mark.asyncio
async def test_exact_pipeline_worker_uses_local_http_and_real_audiosocket(
    via_followup,
    direct_confirmation,
    outcome,
    caplog,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CALL_HISTORY_DB_PATH", str(tmp_path / "call_history.db"))
    monkeypatch.delenv("AAVA_AUDIO_DIAGNOSTICS", raising=False)
    requests = []
    reconcile_requests = []
    native_receipts = []
    preflight_requests = []
    request_seen = asyncio.Event()
    response_at = [0.0]
    guard_time = [100.0]
    release_cancelled_http = asyncio.Event()
    cancelled_http_done = asyncio.Event()
    original_finished = asyncio.Event()
    terminal_absent = [False]
    failure_phrase = "I'm sorry, I couldn't deposit your message."
    http_timeout_totals = []
    if direct_confirmation:
        native_client_timeout = aiohttp.ClientTimeout

        def recording_client_timeout(*args, **kwargs):
            value = native_client_timeout(*args, **kwargs)
            http_timeout_totals.append(value.total)
            return value

        monkeypatch.setattr(aiohttp, "ClientTimeout", recording_client_timeout)

    async def http_handler(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        request_path = headers.split(b"\r\n", 1)[0].split()[1].decode("ascii")
        content_length = 0
        for line in headers.decode("latin1").split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
        body = await reader.readexactly(content_length)
        decoded = json.loads(body)
        status_line = b"HTTP/1.1 200 OK\r\n"
        if request_path == "/preflight":
            preflight_requests.append(decoded)
            payload = json.dumps({"message": "preflight complete"}).encode()
        elif request_path == "/v1/message-deposit/reconcile":
            reconcile_requests.append(decoded)
            if terminal_absent[0]:
                data = {"ok": False, "status": "definitively_not_found",
                        "artifact_verified": False, "replay": False}
                status_line = b"HTTP/1.1 404 Not Found\r\n"
            elif original_finished.is_set():
                data = {
                    "ok": True, "status": "verified_saved",
                    "artifact_verified": True, "replay": True,
                    "native_id": "fixture-native-artifact",
                }
            else:
                data = {
                    "ok": False, "status": "pending_or_ambiguous",
                    "artifact_verified": False, "replay": False, "error": "helper_timeout",
                }
                status_line = b"HTTP/1.1 202 Pending\r\n"
            payload = json.dumps(data).encode()
        else:
            requests.append(decoded)
            if direct_confirmation:
                assert engine._pipeline_message_deposit_guard().snapshot(call_id)["phase"] == "executing"
                state = engine._pipeline_message_deposit_guard()._states[call_id]
                assert json.loads(state.dispatched_envelope) == decoded
                guard_time[0] += 54.0
            request_seen.set()
            if outcome == "cancel":
                await release_cancelled_http.wait()
                writer.close()
                await writer.wait_closed()
                cancelled_http_done.set()
                return
            if outcome in {"barge-in-pending", "barge-in-terminal-absence"}:
                await release_cancelled_http.wait()
            await asyncio.sleep(0.36)
            response_at[0] = time.monotonic()
            data = {"spoken_response": "I'll make sure Gary gets it."}
            if direct_confirmation:
                data.update({
                    "ok": True, "status": "saved", "artifact_verified": True,
                    "replay": False, "native_id": "fixture-native-artifact",
                })
            if outcome == "missing-spoken-response":
                data.pop("spoken_response", None)
                data["untrusted_result"] = "Never speak this as success."
            if outcome == "http-failure":
                data = {"ok": False, "error": "helper_timeout"}
                status_line = b"HTTP/1.1 502 Failed\r\n"
            if outcome in {"terminal-tts-correction", "terminal-tts-unchanged-retry", "barge-in-terminal-absence"} and len(requests) == 1:
                data = {"ok": False, "error": "tts_request_failed"}
                status_line = b"HTTP/1.1 502 Failed\r\n"
                terminal_absent[0] = True
            elif data.get("artifact_verified") is True:
                terminal_absent[0] = False
            if outcome == "terminal-tts-unchanged-retry" and len(requests) > 1:
                data["replay"] = len(requests) > 2
                native_receipts.append(dict(data))
            payload = json.dumps(data).encode()
        writer.write(
            status_line + b"Content-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + payload
        )
        with contextlib.suppress(ConnectionError):
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        if request_path == "/v1/message-deposit" and outcome in {"barge-in-pending", "barge-in-terminal-absence"}:
            original_finished.set()

    http_server = await asyncio.start_server(http_handler, "127.0.0.1", 0)
    http_port = http_server.sockets[0].getsockname()[1]

    config = AppConfig(**{
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "test"},
        "pipelines": {"true_path": {}},
        "active_pipeline": "true_path",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
        "streaming": {"pipeline_streaming_overlap": True, "jitter_buffer_ms": 50},
    })
    engine = Engine(config)
    native_direct = engine._maybe_speak_direct_pipeline_tool_result
    direct_contexts = []

    async def recording_direct(*args, **kwargs):
        if kwargs.get("tool_name") == "pbx_message_deposit":
            context = kwargs.get("execution_context")
            assert context is not None
            assert context.native_deposit_attempt is not None
            direct_contexts.append(context)
        return await native_direct(*args, **kwargs)

    engine._maybe_speak_direct_pipeline_tool_result = recording_direct
    engine.pipeline_orchestrator._started = True
    assert engine.streaming_playback_manager.diag_enable_taps is False
    engine.no_input_watchdog = _Watchdog()
    stt, tts = _ResultSTT(), _RecordingTTS()
    llm = _NoConfirmationInference() if direct_confirmation else _DepositLLM(via_followup=via_followup)
    if outcome in {"barge-in-pending", "barge-in-terminal-absence"}:
        llm = _PendingBargeInLLM()
    resolution = _Resolution(stt, llm, tts)
    if direct_confirmation:
        resolution.llm_options = {
            "session_user_from_call_id": True,
            "call_id_header_enabled": True,
        }
        engine._pipeline_message_deposit_guard_state = PipelineMessageDepositGuard(clock=lambda: guard_time[0])
        engine.config.streaming.pipeline_filler_enabled = True
        engine.config.streaming.pipeline_filler_phrases = ["Filler must not precede dispatch."]
    engine.pipeline_orchestrator.get_pipeline = lambda *args, **kwargs: resolution
    async def set_channel_var(*args, **kwargs):
        return True
    engine.ari_client.set_channel_var = set_channel_var
    hangup_channels = []
    async def hangup_channel(channel_id, *args, **kwargs):
        hangup_channels.append(channel_id)
        return True
    engine.ari_client.hangup_channel = hangup_channel
    tts.on_start = lambda: bool(
        engine.streaming_playback_manager._caller_wait_ambience_tasks
        or engine.streaming_playback_manager._caller_wait_ambience_stops
    )

    call_id = str(uuid.uuid4())
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "true_path"
    session.context_name = "aimee_main" if direct_confirmation else "machine"
    session.allowed_tools = ["deposit_preflight", "pbx_message_deposit"]
    session.audio_capture_enabled = True
    session.caller_name = "Synthetic caller"
    session.caller_number = "fixture-callback"

    body_template = '{"target":"{target}","message":"{message}"}'
    if direct_confirmation:
        body_template = (
            '{"target":"{target}","message":"{message}",'
            '"call_id":"{call_id}","request_id":"{call_id}",'
            '"caller_name":"{caller_name}","callback_number":"{caller_number}",'
            '"urgency":"normal","confirmed":true}'
        )
    tool = create_in_call_http_tool(
        "pbx_message_deposit",
        {
            "enabled": True,
            "is_global": False,
            "url": (f"http://127.0.0.1:{http_port}/v1/message-deposit"
                    if direct_confirmation else f"http://127.0.0.1:{http_port}/deposit"),
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body_template": body_template,
            "parameters": [
                {"name": "target", "type": "string", "required": True},
                {"name": "message", "type": "string", "required": True},
            ],
            "direct_response_json_path": "spoken_response",
            "direct_failure_message": failure_phrase,
            "caller_wait_ambience": True,
            "timeout_ms": 180000 if direct_confirmation else 2000,
        },
    )
    registry = ToolRegistry.isolated()
    registry.register_instance(create_in_call_http_tool(
        "deposit_preflight",
        {
            "enabled": True,
            "is_global": False,
            "url": f"http://127.0.0.1:{http_port}/preflight",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body_template": '{}',
            "parameters": [],
            "timeout_ms": 2000,
        },
    ))
    registry.register_instance(tool)
    session.tool_runtime_registry = registry
    engine.transport_orchestrator.get_context_config = lambda *args, **kwargs: SimpleNamespace(
        prompt=None,
        greeting=None,
        tools=["deposit_preflight", "pbx_message_deposit"],
        in_call_http_tools={},
        disable_global_in_call_tools=[],
    )
    await engine.session_store.upsert_call(session)

    bound = asyncio.Event()
    inbound_handler_seen = asyncio.Event()

    async def on_uuid(conn_id, received_uuid):
        assert received_uuid == call_id
        engine.conn_to_channel[conn_id] = call_id
        session.audiosocket_conn_id = conn_id
        await engine.session_store.upsert_call(session)
        bound.set()
        return True

    async def on_audio(conn_id, frame):
        await engine._audiosocket_handle_audio(conn_id, frame)
        inbound_handler_seen.set()

    audio_server = AudioSocketServer(
        "127.0.0.1",
        0,
        on_uuid=on_uuid,
        on_audio=on_audio,
    )
    await audio_server.start()
    send_calls = []
    original_send_audio = audio_server.send_audio

    async def recording_send_audio(
        conn_id,
        audio_payload,
        *,
        encoding="slin",
        sample_rate=8000,
    ):
        sent_at = time.monotonic()
        accepted = await original_send_audio(
            conn_id,
            audio_payload,
            encoding=encoding,
            sample_rate=sample_rate,
        )
        send_calls.append(
            (sent_at, bytes(audio_payload), encoding, sample_rate, accepted)
        )
        return accepted

    audio_server.send_audio = recording_send_audio
    engine.audio_socket_server = audio_server
    engine.streaming_playback_manager.audiosocket_server = audio_server
    # AudioSocketServer's real wire contract is signed-linear PCM. Exercise the
    # negotiated production-compatible output path rather than an unsupported
    # synthetic uLaw wire mode.
    engine.streaming_playback_manager.audiosocket_format = "slin"
    tts.send_counter = lambda: len(send_calls)

    reader, writer = await asyncio.open_connection("127.0.0.1", audio_server.port)
    writer.write(bytes([TYPE_UUID]) + (16).to_bytes(2, "big") + uuid.UUID(call_id).bytes)
    await writer.drain()
    await asyncio.wait_for(bound.wait(), timeout=2)

    outbound_frames = []

    async def capture_outbound():
        try:
            while True:
                header = await reader.readexactly(3)
                size = int.from_bytes(header[1:], "big")
                payload = await reader.readexactly(size)
                if header[0] == TYPE_AUDIO:
                    outbound_frames.append((time.monotonic(), payload))
        except asyncio.IncompleteReadError:
            return

    capture_task = asyncio.create_task(capture_outbound())

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)

    # Exercise the real inbound AudioSocket callback once before driving exact
    # recognized finals through the already-open streaming STT result channel.
    silence = b"\x00\x00" * 160
    writer.write(bytes([TYPE_AUDIO]) + len(silence).to_bytes(2, "big") + silence)
    await writer.drain()
    await asyncio.wait_for(inbound_handler_seen.wait(), timeout=2)

    request_text = (
        "I'd like to leave a message."
        if direct_confirmation
        else "I'd like to leave a message for Gary, please."
    )
    await stt.results.put(request_text)
    if direct_confirmation:
        target_question = "Of course. Who would you like me to leave the message for?"
        await _wait_until(lambda: target_question in tts.texts)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        await stt.results.put("Gary.")
    content_question = (
        "What would you like me to tell Gary?"
        if direct_confirmation
        else "Of course. What would you like me to tell Gary?"
    )
    await _wait_until(lambda: content_question in tts.texts)
    if direct_confirmation:
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        await stt.results.put("Okay, thanks.")
        await _wait_until(lambda: tts.texts.count("What would you like me to tell Gary?") >= 2)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert not request_seen.is_set()
        assert requests == []
        assert not any("Okay, thanks" in text for text in tts.texts)
    await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
    await stt.results.put("The sky is blue.")
    exact_readback = "I have: “The sky is blue.” Is that right?"
    await _wait_until(lambda: exact_readback in tts.texts)
    await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)

    start_index = len(outbound_frames)
    await stt.results.put("Yes.")
    await asyncio.wait_for(request_seen.wait(), timeout=5)
    tts_before_first_dispatch = list(tts.texts)
    if outcome == "terminal-tts-correction":
        guard = engine._pipeline_message_deposit_guard()
        generation = guard._states[call_id].dispatch_generation
        await _wait_until(lambda: failure_phrase in tts.texts)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert guard.snapshot(call_id)["phase"] == "awaiting_confirmation"
        assert requests[0]["message"] == "The sky is blue."
        await stt.results.put("Change it to the sky is green.")
        await _wait_until(lambda: "I have: “the sky is green.” Is that right?" in tts.texts)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert len(requests) == 1
        with pytest.raises(ValueError):
            guard.consume_confirmed_tool_parameters(call_id, {})
        await stt.results.put("Yes.")
        await _wait_until(lambda: len(requests) == 2)
        assert guard._states[call_id].dispatch_generation > generation
    if outcome == "terminal-tts-unchanged-retry":
        guard = engine._pipeline_message_deposit_guard()
        original_generation = guard._states[call_id].dispatch_generation
        await _wait_until(lambda: failure_phrase in tts.texts)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert guard.snapshot(call_id)["phase"] == "awaiting_confirmation"
        assert len(requests) == 1
        with pytest.raises(ValueError):
            guard.consume_confirmed_tool_parameters(call_id, {})
        await stt.results.put("Yes.")
        await _wait_until(lambda: "I'll make sure Gary gets it." in tts.texts)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert len(requests) == 2
        assert requests[0] == requests[1]
        assert guard._states[call_id].dispatch_generation > original_generation
        with pytest.raises(ValueError):
            guard.consume_confirmed_tool_parameters(call_id, {})
        # A separate, explicitly dictated and confirmed request may be a saved
        # replay. The real worker must retain the existing request identity.
        await stt.results.put("Leave a message for Gary.")
        await _wait_until(lambda: guard.snapshot(call_id)["phase"] == "awaiting_message")
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        await stt.results.put("The sky is blue.")
        await _wait_until(lambda: tts.texts.count(exact_readback) == 2)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert len(requests) == 2
        with pytest.raises(ValueError):
            guard.consume_confirmed_tool_parameters(call_id, {})
        await stt.results.put("Yes.")
        await _wait_until(lambda: len(requests) == 3)
    if outcome in {"barge-in-pending", "barge-in-terminal-absence"}:
        guard = engine._pipeline_message_deposit_guard()
        original_state = guard._states[call_id]
        original_envelope = original_state.dispatched_envelope
        original_generation = original_state.dispatch_generation
        assert guard.snapshot(call_id)["phase"] == "executing"
        assert original_envelope is not None
        # The one-word Yes runs in the real aggregation flush task. This new
        # final cancels its blocked HTTP read while the native server stays queued.
        await stt.results.put("Leave a message for Gary.")
        await _wait_until(lambda: len(reconcile_requests) == 1)
        await _wait_until(lambda: failure_phrase in tts.texts)
        await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
        assert not original_finished.is_set()
        assert guard._states[call_id] is original_state
        assert guard.snapshot(call_id)["phase"] == "reconciling"
        assert guard.reconciliation_ticket(call_id).envelope_json == original_envelope
        assert guard.reconciliation_ticket(call_id).generation == original_generation
        assert requests == reconcile_requests
        assert requests[0]["message"] == "The sky is blue."
        with pytest.raises(ValueError):
            guard.consume_confirmed_tool_parameters(call_id, {})
        assert llm.calls == 1
        release_cancelled_http.set()
        await asyncio.wait_for(original_finished.wait(), timeout=2)
        await stt.results.put("Did you get that?")
        if outcome == "barge-in-terminal-absence":
            await _wait_until(lambda: len(reconcile_requests) == 2)
            await _wait_until(lambda: guard.snapshot(call_id)["phase"] == "awaiting_confirmation")
            await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
            assert len(requests) == 1
            assert requests[0] == reconcile_requests[0] == reconcile_requests[1]
            with pytest.raises(ValueError):
                guard.consume_confirmed_tool_parameters(call_id, {})
            await stt.results.put("Change it to the sky is green.")
            await _wait_until(lambda: "I have: “the sky is green.” Is that right?" in tts.texts)
            await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
            assert len(requests) == 1
            await stt.results.put("Yes.")
            await _wait_until(lambda: len(requests) == 2)
            assert guard._states[call_id].dispatch_generation > original_generation
    if outcome == "cancel":
        await _wait_until(lambda: len(outbound_frames) >= start_index + 3)
        assert engine._pipeline_message_deposit_guard().snapshot(call_id)["phase"] == "executing"
        assert not engine.streaming_playback_manager.active_streams
        assert session.audio_capture_enabled is True
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            await engine._cleanup_call(call_id)
        assert llm.calls == llm.stream_calls == 0
        assert http_timeout_totals[0] == 180.0
        assert all(0 < total <= 180.0 for total in http_timeout_totals)
        assert len(requests) == 1
        assert engine._pipeline_message_deposit_guard().snapshot(call_id) is None
        assert not engine.streaming_playback_manager._caller_wait_ambience_tasks
        assert not engine.streaming_playback_manager._caller_wait_ambience_stops
        assert not engine._pipeline_tasks
        assert "I'll make sure Gary gets it." not in tts.texts
        assert failure_phrase not in tts.texts
        assert not [record for record in caplog.records if record.exc_info]
        assert engine.no_input_watchdog.stopped == [call_id]
        assert hangup_channels == [call_id]
        release_cancelled_http.set()
        await asyncio.wait_for(cancelled_http_done.wait(), timeout=2)
        writer.write(bytes([TYPE_TERMINATE, 0, 0]))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await audio_server.stop()
        capture_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await capture_task
        http_server.close()
        await http_server.wait_closed()
        return
    terminal_phrase = (
        "Thanks. I'll make sure they get it." if outcome == "barge-in-pending"
        else "I'll make sure Gary gets it." if outcome in {"success", "terminal-tts-correction", "terminal-tts-unchanged-retry", "barge-in-terminal-absence"}
        else failure_phrase
    )
    expected_suspensions = [True, False] * (
        4 if outcome == "barge-in-terminal-absence"
        else 3 if outcome in {"barge-in-pending", "terminal-tts-unchanged-retry"}
        else 2 if outcome == "terminal-tts-correction" else 1)
    await _wait_until(lambda: terminal_phrase in tts.texts)
    await _wait_until(lambda: not engine.streaming_playback_manager.active_streams)
    await _wait_until(lambda: engine.no_input_watchdog.suspensions == expected_suspensions)
    await _wait_until(
        lambda: any(
            stamp >= tts.started_at[-1] and any(payload)
            for stamp, payload in outbound_frames[start_index:]
        )
    )

    expected_body = {"target": "Gary", "message": "The sky is blue."}
    if direct_confirmation:
        expected_body.update({
            "call_id": call_id, "request_id": call_id,
            "caller_name": "Synthetic caller", "callback_number": "fixture-callback",
            "urgency": "normal", "confirmed": True,
        })
        assert tool.config.timeout_ms == 180000
        assert http_timeout_totals[0] == 180.0
        assert all(0 < total <= 180.0 for total in http_timeout_totals)
        assert engine._pipeline_message_deposit_guard()._confirmed_execution_window_sec == 5.0
        assert llm.stream_calls == 0
        assert "Filler must not precede dispatch." not in tts_before_first_dispatch
    corrected = dict(expected_body, message="the sky is green.")
    assert requests == ([expected_body, corrected] if outcome in {"terminal-tts-correction", "barge-in-terminal-absence"}
                        else [expected_body] * 3 if outcome == "terminal-tts-unchanged-retry"
                        else [expected_body])
    assert reconcile_requests == [expected_body] * (
        3 if outcome == "barge-in-terminal-absence"
        else 2 if outcome in {"barge-in-pending", "terminal-tts-correction", "terminal-tts-unchanged-retry"}
        else 1 if outcome == "http-failure" else 0)
    assert preflight_requests == ([{}] if via_followup else [])
    assert len(direct_contexts) == (
        3 if outcome in {"terminal-tts-unchanged-retry", "barge-in-terminal-absence"}
        else 2 if outcome in {"barge-in-pending", "terminal-tts-correction"} else 1)
    if outcome == "terminal-tts-correction":
        assert requests[0]["request_id"] == requests[1]["request_id"] == call_id
        assert direct_contexts[0].native_deposit_attempt < direct_contexts[1].native_deposit_attempt
    if outcome == "terminal-tts-unchanged-retry":
        assert len(native_receipts) == 2
        assert [receipt["replay"] for receipt in native_receipts] == [False, True]
        assert {receipt["native_id"] for receipt in native_receipts} == {"fixture-native-artifact"}
        assert requests == [requests[0]] * 3
        generations = [context.native_deposit_attempt for context in direct_contexts]
        assert generations == sorted(set(generations))
    expected_inferences = (
        2 if outcome in {"barge-in-pending", "barge-in-terminal-absence"} else 0 if direct_confirmation
        else 2 if via_followup else 1
    )
    assert llm.calls == expected_inferences
    if outcome == "barge-in-pending":
        assert all(context.native_deposit_attempt == original_generation
                   for context in direct_contexts)
        assert engine._pipeline_message_deposit_guard().snapshot(call_id)["phase"] == "acknowledged"
    assert tts.texts.count(terminal_phrase) == (2 if outcome == "terminal-tts-unchanged-retry" else 1)
    assert "Never speak this as success." not in tts.texts
    if outcome not in {"success", "barge-in-pending", "terminal-tts-correction", "terminal-tts-unchanged-retry", "barge-in-terminal-absence"}:
        assert "I'll make sure Gary gets it." not in tts.texts
        expected_phase = "reconciling" if outcome == "http-failure" else "acknowledged"
        assert engine._pipeline_message_deposit_guard().snapshot(call_id)["phase"] == expected_phase
    assert engine.no_input_watchdog.suspensions == expected_suspensions
    wait_frames = [
        (stamp, payload)
        for stamp, payload in outbound_frames[start_index:]
        if stamp < tts.started_at[-1]
    ]
    assert wait_frames
    assert any(any(payload) for _, payload in wait_frames)
    assert wait_frames[-1][0] <= tts.started_at[-1]
    assert response_at[0] <= tts.started_at[-1]
    response_frames = [
        (stamp, payload)
        for stamp, payload in outbound_frames[start_index:]
        if stamp >= tts.started_at[-1]
    ]
    assert response_frames
    assert any(any(payload) for _, payload in response_frames)
    assert tts.ambience_active_at_start[-1] is False
    response_send_calls = send_calls[tts.send_count_at_start[-1]:]
    assert response_send_calls
    assert any(accepted and any(payload) for _, payload, _, _, accepted in response_send_calls)
    assert any(
        accepted
        and any(payload)
        and any(
            reader_stamp >= send_stamp and reader_payload == payload
            for reader_stamp, reader_payload in response_frames
        )
        for send_stamp, payload, _, _, accepted in response_send_calls
    )
    assert wait_frames[-1][0] < response_frames[0][0]
    assert not engine.streaming_playback_manager._caller_wait_ambience_tasks
    assert not engine.streaming_playback_manager._caller_wait_ambience_stops

    if outcome in {"success", "barge-in-pending", "terminal-tts-correction", "terminal-tts-unchanged-retry", "barge-in-terminal-absence"}:
        await stt.results.put("Yes please.")
    await asyncio.sleep(0.08)
    assert llm.calls == expected_inferences
    assert len(requests) == (3 if outcome == "terminal-tts-unchanged-retry"
                             else 2 if outcome in {"terminal-tts-correction", "barge-in-terminal-absence"} else 1)

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        await engine._cleanup_call(call_id)
    assert engine._pipeline_message_deposit_guard().snapshot(call_id) is None
    assert not engine._pipeline_tasks
    assert engine.no_input_watchdog.stopped == [call_id]
    assert hangup_channels == [call_id]
    assert not [record for record in caplog.records if record.exc_info]
    assert not engine.streaming_playback_manager._caller_wait_ambience_tasks
    writer.write(bytes([TYPE_TERMINATE, 0, 0]))
    await writer.drain()
    writer.close()
    await writer.wait_closed()
    await audio_server.stop()
    capture_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await capture_task
    http_server.close()
    await http_server.wait_closed()


@pytest.mark.parametrize(
    "options,expected",
    [
        ({"call_id_header_enabled": True, "session_user_from_call_id": True}, True),
        ({}, False),
        ({"call_id_header_enabled": True}, False),
        ({"session_user_from_call_id": True}, False),
        ({"call_id_header_enabled": "true", "session_user_from_call_id": "true"}, False),
    ],
)
def test_direct_confirmation_reuses_existing_call_scoped_transport_only(options, expected):
    engine = Engine.__new__(Engine)
    guard = engine._pipeline_message_deposit_guard()
    call_id = "native-asterisk-1789.42"
    configured = dict(options, tools=["pbx_message_deposit"])
    assert engine._confirmed_pipeline_message_deposit_call(call_id, configured) is None
    guard.decide(call_id, "I'd like to leave a message for Gary.", enabled=True)
    guard.decide(call_id, "The sky is blue.", enabled=True)
    assert engine._confirmed_pipeline_message_deposit_call(call_id, configured) is None
    guard.decide(call_id, "Yes.", enabled=True)
    selected = engine._confirmed_pipeline_message_deposit_call(call_id, configured)
    assert bool(selected) is expected
    if selected:
        assert selected == {"name": "pbx_message_deposit", "parameters": {}}
        assert engine._confirmed_pipeline_message_deposit_call("another-call", configured) is None
        assert engine._confirmed_pipeline_message_deposit_call(call_id, dict(configured, tools=[])) is None
        bound = engine._bind_pipeline_tool_parameters(call_id, selected["name"], selected["parameters"])
        assert bound == {"target": "Gary", "message": "The sky is blue."}
        assert engine._confirmed_pipeline_message_deposit_call(call_id, configured) is None
        with pytest.raises(ValueError, match="already_consumed"):
            engine._bind_pipeline_tool_parameters(call_id, selected["name"], {})


def test_direct_confirmation_does_not_extend_expired_guard_or_retain_after_cleanup():
    engine = Engine.__new__(Engine)
    now = [100.0]
    guard = PipelineMessageDepositGuard(clock=lambda: now[0])
    engine._pipeline_message_deposit_guard_state = guard
    options = {
        "call_id_header_enabled": True,
        "session_user_from_call_id": True,
        "tools": ["pbx_message_deposit"],
    }
    call_id = "1789.42"
    guard.decide(call_id, "I'd like to leave a message for Gary.", enabled=True)
    guard.decide(call_id, "The sky is blue.", enabled=True)
    guard.decide(call_id, "Yes.", enabled=True)
    selected = engine._confirmed_pipeline_message_deposit_call(call_id, options)
    now[0] += 5.01
    with pytest.raises(ValueError, match="confirmation_expired"):
        engine._bind_pipeline_tool_parameters(call_id, selected["name"], selected["parameters"])
    assert guard.snapshot(call_id)["phase"] == "awaiting_confirmation"
    assert engine._confirmed_pipeline_message_deposit_call(call_id, options) is None
    guard.cleanup(call_id)
    assert guard.snapshot(call_id) is None
    assert engine._confirmed_pipeline_message_deposit_call(call_id, options) is None
