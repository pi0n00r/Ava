import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit

import pytest

from src.config import AppConfig
from src.ari_client import ARIClient
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


LOCAL_ID = "rita-int-a-00ec790ae7f1fb07ddb8b04480db300c6a605ab32877f4460a65c6ce7bfb0688"
LOCAL_NAME = "Local/1@from-internal-00000003;1"
APP = "asterisk-ai-voice-agent"
ROUTE_CONTEXTS = {"aimee_main": "aimee-main-engine", "aimee": "aimee-engine"}


def local_event(channel_id=LOCAL_ID, name=LOCAL_NAME, agent="aimee_main"):
    # Native route context and Stasis application are different namespaces.
    return {
        "type": "StasisStart",
        "application": APP,
        "args": [],
        "channel": {
            "id": channel_id, "name": name, "state": "Up",
            "dialplan": {"context": ROUTE_CONTEXTS[agent], "exten": "s", "priority": 1,
                         "app_name": "Stasis", "app_data": APP},
        },
    }


def ingress_engine(event=None):
    event = event or local_event()
    channel = copy.deepcopy(event["channel"])
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(streaming=SimpleNamespace(connection_timeout_ms=10000))
    engine._pre_stasis_channels = {channel["id"]}
    engine._seen_caller_stasis_channels = set()
    engine._seen_aux_channels = set()
    engine.pending_local_channels = {}
    engine.session_store = SimpleNamespace(
        get_all_sessions=AsyncMock(return_value=[]),
        get_by_call_id=AsyncMock(return_value=None),
    )

    async def readback(method, path, **kwargs):
        assert method == "GET"
        if path == f"channels/{channel['id']}":
            return copy.deepcopy(channel)
        raise AssertionError(f"Unexpected fixture path: {path}")

    engine.ari_client = SimpleNamespace(
        app_name=APP,
        send_command=AsyncMock(side_effect=readback),
        hangup_channel=AsyncMock(),
    )
    engine._handle_caller_stasis_start_hybrid = AsyncMock()
    engine._handle_local_stasis_start_hybrid = AsyncMock()
    engine._handle_outbound_stasis = AsyncMock()
    engine._handle_agent_action_stasis = AsyncMock()
    engine._handle_audiosocket_channel_stasis_start = AsyncMock()
    engine._handle_external_media_stasis_start = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_real_empty_args_local_a_is_the_caller_without_prior_session():
    event = local_event()
    engine = ingress_engine(event)
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(
        LOCAL_ID, event["channel"]
    )
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert LOCAL_ID in engine._seen_caller_stasis_channels
    assert LOCAL_ID not in engine._seen_aux_channels
    assert LOCAL_ID not in engine._pre_stasis_channels


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [LOCAL_NAME, "Local/9@retained-route-00000044;2"])
async def test_role_is_not_a_rita_id_number_or_local_half_allowlist(name):
    event = local_event(channel_id="native-owned-id", name=name)
    engine = ingress_engine(event)
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(
        "native-owned-id", event["channel"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", ["pending", "session", "seen_aux"])
async def test_explicit_auxiliary_local_ownership_preserves_helper_path(ownership):
    event = local_event()
    engine = ingress_engine(event)
    if ownership == "pending":
        engine.pending_local_channels[LOCAL_ID] = "original-caller"
    elif ownership == "session":
        session = CallSession(call_id="original-caller", caller_channel_id="original-caller")
        session.local_channel_id = LOCAL_ID
        engine.session_store.get_all_sessions.return_value = [session]
    else:
        engine._seen_aux_channels.add(LOCAL_ID)
    await engine._handle_stasis_start(event)
    engine._handle_local_stasis_start_hybrid.assert_awaited_once_with(
        LOCAL_ID, event["channel"]
    )
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.send_command.assert_not_awaited()
    assert LOCAL_ID in engine._seen_aux_channels
    assert LOCAL_ID not in engine._seen_caller_stasis_channels

@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
async def test_wildcard_subscription_accepts_current_owned_local_stasis_without_id_allowlist(agent):
    event = local_event(agent=agent)
    engine = controlled_engine([_ControlledResponse(200, event["channel"])], event)
    engine.ari_client.http_session.application_response = _ControlledResponse(
        200, {"name": APP, "channel_ids": ["__AST_CHANNEL_ALL_TOPIC"]}
    )
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, event["channel"])
    assert engine.ari_client.http_session.requests == [("GET", f"channels/{LOCAL_ID}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["channel"])
@pytest.mark.parametrize("terminal", ["StasisEnd", "ChannelDestroyed"])
async def test_terminal_event_during_real_http_read_prevents_late_real_initializer(monkeypatch, tmp_path, phase, terminal):
    engine, pipeline, playback, waiting, ready = timing_engine(monkeypatch, tmp_path)
    release = asyncio.Event()
    channel = copy.deepcopy(local_event()["channel"])
    blocked = _ControlledResponse(200, channel, release)
    replies = [blocked]
    engine.ari_client.http_session = _ControlledHTTP(replies)
    engine.ari_client.send_command = ARIClient.send_command.__get__(engine.ari_client, ARIClient)
    original_initializer = engine._handle_caller_stasis_start_hybrid
    engine._handle_caller_stasis_start_hybrid = AsyncMock(wraps=original_initializer)
    start = asyncio.create_task(engine._handle_stasis_start(local_event()))
    try:
        await asyncio.wait_for(blocked.entered.wait(), timeout=0.5)
        end_event = dict(local_event(), type=terminal)
        if terminal == "StasisEnd":
            await engine._handle_stasis_end(end_event)
        else:
            await engine._handle_channel_destroyed(end_event)
        assert await engine.session_store.get_by_call_id(LOCAL_ID) is None
        release.set()
        await asyncio.wait_for(start, timeout=1)
        engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
        assert await engine.session_store.get_by_call_id(LOCAL_ID) is None
        assert LOCAL_ID not in engine._seen_caller_stasis_channels
        assert not getattr(engine, "_local_ingress_checks", {})
        assert not playback.starts
        assert len(engine.ari_client.http_session.requests) == 1
    finally:
        release.set()
        await asyncio.gather(start, return_exceptions=True)
        await engine._cleanup_call(LOCAL_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("app_name,app_data", [("Wait", ""), ("Stasis", "other-application")])
async def test_active_channel_no_longer_in_our_stasis_is_rejected_even_without_terminal_event(monkeypatch, app_name, app_data):
    logger = diagnostic_logger(monkeypatch)
    channel = copy.deepcopy(local_event()["channel"])
    channel["dialplan"].update(app_name=app_name, app_data=app_data)
    engine = controlled_engine([
        _ControlledResponse(200, channel),
    ])
    await engine._handle_stasis_start(local_event())
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    assert_diagnostic(logger, "channel_stasis_application_mismatch", "channel")


@pytest.mark.asyncio
@pytest.mark.parametrize("application", [None, "other-app", ""])
async def test_foreign_or_unattributed_local_event_cannot_actuate(application):
    event = local_event()
    event["application"] = application
    engine = ingress_engine(event)
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.send_command.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [
    None, {}, [], False, "invalid-channel", {"status": 404},
])
async def test_invalid_active_channel_readback_is_rejected(readback):
    engine = ingress_engine()
    engine.ari_client.send_command.return_value = readback
    engine.ari_client.send_command.side_effect = None
    await engine._handle_stasis_start(local_event())
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [
    None, {}, {"id": "different-id", "name": LOCAL_NAME},
    {"id": LOCAL_ID, "name": "Local/1@from-internal-00000003;2"},
])
async def test_stale_or_different_active_channel_cannot_become_caller(readback):
    engine = ingress_engine()
    engine.ari_client.send_command.side_effect = [readback]
    await engine._handle_stasis_start(local_event())
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_readback_failure_does_not_guess_another_caller_or_hangup():
    engine = ingress_engine()
    engine.ari_client.send_command.side_effect = RuntimeError("dummy readback failure")
    await engine._handle_stasis_start(local_event())
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_duplicate_owned_local_entry_initializes_once():
    engine = ingress_engine()
    both_channel_reads = asyncio.Event()
    reads_in_flight = 0

    async def readback(method, path, **kwargs):
        nonlocal reads_in_flight
        assert method == "GET"
        assert path == f"channels/{LOCAL_ID}"
        reads_in_flight += 1
        if reads_in_flight == 2:
            both_channel_reads.set()
        await both_channel_reads.wait()
        await asyncio.sleep(0)
        return local_event()["channel"]

    engine.ari_client.send_command.side_effect = readback
    await asyncio.gather(
        engine._handle_stasis_start(local_event()),
        engine._handle_stasis_start(local_event()),
    )
    assert reads_in_flight == 2
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["channel"])
async def test_local_ownership_timeout_cancels_blocked_read_without_retry(monkeypatch, phase):
    engine = ingress_engine()
    engine.config.streaming.connection_timeout_ms = 20
    cancelled = asyncio.Event()
    original_timeout = asyncio.timeout
    budgets = []

    def short_timeout(seconds):
        budgets.append(seconds)
        return original_timeout(seconds)

    async def blocked_read(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr("src.engine.asyncio.timeout", short_timeout)
    engine.ari_client.send_command.side_effect = blocked_read
    await asyncio.wait_for(engine._handle_stasis_start(local_event()), timeout=0.3)
    assert budgets == [0.02]
    assert cancelled.is_set()
    assert engine.ari_client.send_command.await_count == 1
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_cleanup_clears_local_caller_marker_and_deduplicates():
    engine = ingress_engine()
    await engine._handle_stasis_start(local_event())
    engine.session_store = SessionStore()
    engine._attended_transfer_agent_channel_to_call_id = {}
    engine._persist_abandoned_call_history = AsyncMock()
    await engine._cleanup_call(LOCAL_ID)
    await engine._cleanup_call(LOCAL_ID)
    assert LOCAL_ID not in engine._seen_caller_stasis_channels
    engine._persist_abandoned_call_history.assert_awaited_once_with(LOCAL_ID)


@pytest.mark.asyncio
async def test_registered_local_uses_real_helper_and_original_caller_bridge():
    engine = ingress_engine()
    engine.session_store = SessionStore()
    session = CallSession(call_id="original-caller", caller_channel_id="original-caller")
    session.local_channel_id = LOCAL_ID
    session.bridge_id = "original-bridge"
    await engine.session_store.upsert_call(session)
    engine.local_channels = {}
    engine._save_session = AsyncMock(side_effect=engine.session_store.upsert_call)
    engine._ensure_provider_session_started = AsyncMock()
    engine._enable_pipeline_talk_detect = AsyncMock()
    engine.ari_client.add_channel_to_bridge = AsyncMock(return_value=True)
    engine._handle_local_stasis_start_hybrid = Engine._handle_local_stasis_start_hybrid.__get__(engine)
    await engine._handle_stasis_start(local_event())
    engine.ari_client.add_channel_to_bridge.assert_awaited_once_with("original-bridge", LOCAL_ID)
    engine._ensure_provider_session_started.assert_awaited_once_with("original-caller")
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


class _TimingTTS:
    def __init__(self):
        self.requests = []

    async def open_call(self, *args):
        pass

    async def close_call(self, *args):
        pass

    async def synthesize(self, call_id, text, options):
        self.requests.append((call_id, text))
        yield b""
        yield b"first-spoken-chunk"
        yield b"second-spoken-chunk"


class _TimingPlayback:
    def __init__(self):
        self.queues = {}
        self.active_streams = {}
        self.starts = []
        self.drained = []
        self.started = asyncio.Event()

    async def start_streaming_playback(self, call_id, queue, **kwargs):
        stream_id = f"stream-{len(self.starts)}"
        self.starts.append((call_id, kwargs))
        self.queues[call_id] = queue
        self.active_streams[call_id] = {"stream_id": stream_id}
        self.started.set()
        return stream_id

    def get_stream_id(self, call_id):
        return self.active_streams.get(call_id, {}).get("stream_id")

    def is_stream_active(self, call_id, stream_id=None):
        current = self.active_streams.get(call_id)
        return bool(current and (stream_id is None or current["stream_id"] == stream_id))

    async def stop_streaming_playback(self, call_id, *, drain=False, **kwargs):
        queue = self.queues.pop(call_id, None)
        if queue:
            while not queue.empty():
                chunk = queue.get_nowait()
                if drain:
                    self.drained.append(chunk)
        self.active_streams.pop(call_id, None)
        return True

    async def stop_caller_wait_ambience(self, call_id):
        return True


def timing_engine(monkeypatch, tmp_path, agent="aimee_main"):
    # Explicit dummy config, absent test agents DB, and injected ARI operations.
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "absent-agents.db"))
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "false")
    monkeypatch.setattr("src.engine._cleanup_completed_at", {})
    monkeypatch.setattr("src.engine._cleanup_in_progress", set())
    config = AppConfig(**{
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "pbx.invalid", "username": "fixture", "password": "fixture", "app_name": APP},
        "llm": {"initial_greeting": "hello", "prompt": "fixture", "model": "fixture"},
        "pipelines": {"local_hybrid": {}},
        "active_pipeline": "local_hybrid",
        "audio_transport": "externalmedia",
        "downstream_mode": "stream",
        "streaming": {"greeting_min_start_ms": 350, "greeting_media_ready_delay_ms": 350, "greeting_media_ready_timeout_ms": 1000},
    })
    engine = Engine(config)
    playback = _TimingPlayback()
    engine.streaming_playback_manager = playback
    engine.pipeline_orchestrator._started = True
    tts = _TimingTTS()
    adapter = SimpleNamespace(open_call=AsyncMock(), close_call=AsyncMock(), supports_streaming=False)
    pipeline = SimpleNamespace(
        pipeline_name="local_hybrid", stt_key="fixture-stt", stt_adapter=adapter,
        llm_adapter=SimpleNamespace(bind_tool_registry=MagicMock(), open_call=AsyncMock(), close_call=AsyncMock()),
        tts_adapter=tts, stt_options={}, llm_options={}, tts_options={}, prepared=True,
        component_summary=lambda: {"stt": "fixture", "llm": "fixture", "tts": "fixture"},
    )
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *args: pipeline)
    context = SimpleNamespace(pipeline="local_hybrid", provider=None, profile="telephony_ulaw_8k" if agent == "aimee" else None, prompt="fixture", greeting="hello", tools=[], disable_global_in_call_tools=[], background_music=None)
    monkeypatch.setattr(engine.transport_orchestrator, "get_context_config", lambda *args: context)
    monkeypatch.setattr(engine.transport_orchestrator, "yaml_context_shadowed_by_agent_db", lambda *args: False)
    for name in ("_hydrate_transport_from_dialplan", "_detect_caller_codec", "_hydrate_vicidial_session", "_export_config_metrics", "_ensure_provider_session_started", "_enable_pipeline_talk_detect", "_disable_pipeline_talk_detect"):
        monkeypatch.setattr(engine, name, AsyncMock())
    monkeypatch.setattr(engine, "_schedule_connection_audio_handoff_timeout", lambda *args: None)
    monkeypatch.setattr(engine, "_start_external_media_channel", AsyncMock(return_value="fixture-media"))

    async def assign(session, **kwargs):
        assert kwargs.get("pipeline_name") == "local_hybrid"
        session.pipeline_name = "local_hybrid"
        return pipeline

    monkeypatch.setattr(engine, "_assign_pipeline_to_session", assign)

    async def readback(method, path, params=None, **kwargs):
        assert method == "GET"
        if path == f"channels/{LOCAL_ID}":
            return local_event(agent=agent)["channel"]
        if path.endswith("/variable"):
            return {"value": agent if (params or {}).get("variable") == "AI_AGENT" else ""}
        raise AssertionError(f"Unexpected fixture path: {path}")

    engine.ari_client.send_command = AsyncMock(side_effect=readback)
    for name in ("answer_channel", "add_channel_to_bridge", "hangup_channel", "destroy_bridge", "stop_playback"):
        monkeypatch.setattr(engine.ari_client, name, AsyncMock(return_value=True))
    engine.ari_client.create_bridge = AsyncMock(return_value="fixture-bridge")
    ready = asyncio.Event()
    waiting = asyncio.Event()
    original_wait = engine._wait_for_initial_media

    async def wait_for_media(call_id, session):
        waiting.set()
        return await original_wait(call_id, session)

    monkeypatch.setattr(engine, "_wait_for_initial_media", AsyncMock(side_effect=wait_for_media))
    monkeypatch.setattr(engine, "_no_input_mark_ready", AsyncMock(side_effect=lambda *args: ready.set()))
    return engine, pipeline, playback, waiting, ready


@pytest.mark.asyncio
async def test_real_local_caller_initializer_reaches_existing_350_preroll_once(monkeypatch, tmp_path):
    engine, pipeline, playback, waiting, ready = timing_engine(monkeypatch, tmp_path)
    original_readback = engine.ari_client.send_command.side_effect
    both_channel_reads = asyncio.Event()
    reads_in_flight = 0

    async def interleaved_readback(method, path, **kwargs):
        nonlocal reads_in_flight
        if path == f"channels/{LOCAL_ID}":
            reads_in_flight += 1
            if reads_in_flight == 2:
                both_channel_reads.set()
            await both_channel_reads.wait()
            await asyncio.sleep(0)
        return await original_readback(method, path, **kwargs)

    engine.ari_client.send_command.side_effect = interleaved_readback
    try:
        await asyncio.wait_for(asyncio.gather(
            engine._handle_stasis_start(local_event()),
            engine._handle_stasis_start(local_event()),
        ), timeout=1)
        assert reads_in_flight == 2
        engine.ari_client.create_bridge.assert_awaited_once()
        session = await engine.session_store.get_by_call_id(LOCAL_ID)
        assert session and session.call_id == session.caller_channel_id == LOCAL_ID
        assert session.context_name == "aimee_main" and session.pipeline_name == "local_hybrid"
        assert session.local_channel_id is None
        engine.ari_client.answer_channel.assert_awaited_once_with(LOCAL_ID)
        engine.ari_client.add_channel_to_bridge.assert_any_await("fixture-bridge", LOCAL_ID)
        await asyncio.wait_for(waiting.wait(), timeout=1)
        queue = playback.queues[LOCAL_ID]
        assert queue.empty(), "Speech/pre-roll cannot be queued before initial-media wait finishes"
        session.media_rx_confirmed = True
        await asyncio.wait_for(ready.wait(), timeout=1)
        chunks = []
        while not queue.empty():
            chunks.append(queue.get_nowait())
        assert chunks == [b"\xff" * 2800, b"first-spoken-chunk", b"second-spoken-chunk", None]
        assert playback.starts[0][1]["playback_type"] == "pipeline-tts-greeting"
        assert engine.config.streaming.greeting_min_start_ms == 350
        assert engine.config.streaming.greeting_media_ready_delay_ms == 350
        assert engine._wait_for_initial_media.await_count == 1
        await engine._stream_pipeline_tts_text(LOCAL_ID, session, pipeline, "next reply")
        assert playback.drained == [b"first-spoken-chunk", b"second-spoken-chunk", None]
        assert engine._wait_for_initial_media.await_count == 1
    finally:
        await engine._cleanup_call(LOCAL_ID)
    assert LOCAL_ID not in engine._seen_caller_stasis_channels
    assert LOCAL_ID not in engine._pipeline_tasks


@pytest.mark.asyncio
async def test_cancelled_ownership_read_propagates_and_teardown_has_no_caller():
    engine = ingress_engine()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_read(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine.ari_client.send_command.side_effect = blocked_read
    task = asyncio.create_task(engine._handle_stasis_start(local_event()))
    await asyncio.wait_for(started.wait(), timeout=0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    engine.session_store = SessionStore()
    engine._attended_transfer_agent_channel_to_call_id = {}
    engine._persist_abandoned_call_history = AsyncMock()
    await engine._cleanup_call(LOCAL_ID)
    assert cancelled.is_set()
    assert engine._seen_caller_stasis_channels == engine._seen_aux_channels == set()
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine._persist_abandoned_call_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_hangup_during_initial_media_wait_cancels_greeting_and_leaves_next_call_clean(monkeypatch, tmp_path):
    engine, pipeline, playback, waiting, ready = timing_engine(monkeypatch, tmp_path)
    await engine._handle_stasis_start(local_event())
    await asyncio.wait_for(waiting.wait(), timeout=1)
    old_queue = playback.queues[LOCAL_ID]
    old_task = engine._pipeline_tasks[LOCAL_ID]
    assert old_queue.empty()
    await engine._cleanup_call(LOCAL_ID)
    assert old_task.done()
    assert old_queue.empty()
    assert LOCAL_ID not in engine._seen_caller_stasis_channels
    assert LOCAL_ID not in playback.active_streams
    assert not ready.is_set()
    next_engine, next_pipeline, next_playback, next_waiting, next_ready = timing_engine(monkeypatch, tmp_path)
    next_id = "next-native-local-id"
    next_session = CallSession(call_id=next_id, caller_channel_id=next_id)
    next_session.context_name = "aimee"
    next_session.pipeline_name = "local_hybrid"
    next_session.media_rx_confirmed = True
    await next_engine.session_store.upsert_call(next_session)
    try:
        await next_engine._ensure_pipeline_runner(next_session, forced=True)
        await asyncio.wait_for(next_ready.wait(), timeout=1)
        assert next_playback.starts[0][0] == next_id
        assert next_playback.queues[next_id].get_nowait() == b"\xff" * 2800
        assert old_queue.empty()
        assert old_task.done()
    finally:
        await next_engine._cleanup_call(next_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["outbound", "outbound_amd", "attended-transfer"])
async def test_reserved_action_entry_keeps_existing_dispatch(action):
    event = local_event()
    event["args"] = [action, "original-call"]
    engine = ingress_engine(event)
    await engine._handle_stasis_start(event)
    expected = engine._handle_outbound_stasis if action.startswith("outbound") else engine._handle_agent_action_stasis
    expected.assert_awaited_once_with(LOCAL_ID, event["channel"], event["args"])
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine._handle_local_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.send_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["PJSIP/1-0000000e", "SIP/test", "DAHDI/1", "IAX2/test", "Dongle/test"])
async def test_existing_native_caller_types_keep_their_dispatch(name):
    event = local_event(channel_id="native-caller", name=name)
    engine = ingress_engine(event)
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(
        "native-caller", event["channel"]
    )
    engine.ari_client.send_command.assert_not_awaited()


class _ControlledResponse:
    def __init__(self, status, body, release=None):
        self.status = status
        self.body_text = json.dumps(body)
        self.release = release
        self.entered = asyncio.Event()
        self.cancelled = False
        self.json_calls = 0
        self.text_calls = 0

    async def __aenter__(self):
        self.entered.set()
        try:
            if self.release is not None:
                await self.release.wait()
            return self
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def __aexit__(self, *args):
        return False

    async def json(self):
        self.json_calls += 1
        return json.loads(self.body_text)

    async def text(self):
        self.text_calls += 1
        return self.body_text


class _ControlledHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.application_response = None

    def request(self, method, url, **kwargs):
        assert method == "GET", "Controlled transport permits no actuation"
        resource = urlsplit(url).path.removeprefix("/ari/")
        self.requests.append((method, resource))
        if resource.startswith("applications/") and self.application_response is not None:
            return self.application_response
        assert self.replies, "Unexpected extra HTTP read"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def controlled_engine(replies, event=None):
    engine = ingress_engine(event)
    client = ARIClient(
        username="fixture", password="fixture",
        base_url="https://pbx.fixture.invalid/ari", app_name=APP,
    )
    client.http_session = _ControlledHTTP(replies)
    client.hangup_channel = AsyncMock()
    engine.ari_client = client
    return engine


def valid_application():
    return {"name": APP, "channel_ids": [LOCAL_ID], "bridge_ids": [], "endpoint_ids": []}


def diagnostic_logger(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr("src.engine.logger", logger)
    return logger


def assert_diagnostic(logger, code, phase, status=None):
    calls = [
        call for call in logger.warning.call_args_list
        if call.args == ("Local ingress ownership rejected",)
    ]
    assert len(calls) == 1
    values = calls[0].kwargs
    assert set(values) == {"reason_code", "phase", "http_status", "elapsed_ms"}
    assert values["reason_code"] == code
    assert values["phase"] == phase
    assert values["http_status"] == status
    assert isinstance(values["elapsed_ms"], (int, float))
    assert values["elapsed_ms"] >= 0
    assert "DO_NOT_LOG" not in repr(logger.mock_calls)
    return values


@pytest.mark.asyncio
async def test_real_ari_send_command_returns_parsed_json_not_envelope():
    good = _ControlledResponse(200, valid_application())
    missing = _ControlledResponse(404, {"message": "dummy missing application"})
    engine = controlled_engine([good, missing])
    result = await engine.ari_client.send_command("GET", f"applications/{APP}")
    assert result == valid_application()
    assert "status" not in result and good.json_calls == 1
    result = await engine.ari_client.send_command(
        "GET", f"applications/{APP}", tolerate_statuses=[404]
    )
    assert result == {"status": 404, "reason": missing.body_text}
    assert missing.text_calls == 1 and missing.json_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_status,channel_body,code,phase,status", [
    (404, {"message": "DO_NOT_LOG_BODY"}, "channel_404", "channel", 404),
    (200, ["DO_NOT_LOG_BODY"], "channel_nonobject", "channel", None),
    (200, {"id": "DO_NOT_LOG_ID", "name": LOCAL_NAME}, "channel_id_mismatch", "channel", None),
    (200, {"id": LOCAL_ID, "name": "DO_NOT_LOG_NAME"}, "channel_name_mismatch", "channel", None),
])
async def test_rejection_codes_use_real_client_and_do_not_log_response(
    monkeypatch, channel_status, channel_body, code, phase, status
):
    logger = diagnostic_logger(monkeypatch)
    replies = [_ControlledResponse(channel_status, channel_body)]
    engine = controlled_engine(replies)
    assert not await engine._is_owned_local_ingress(local_event(), local_event()["channel"])
    assert_diagnostic(logger, code, phase, status)
    assert len(engine.ari_client.http_session.requests) == 1
    assert not engine.ari_client.http_session.replies
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_type_rejection_does_not_log_channel_name(monkeypatch):
    logger = diagnostic_logger(monkeypatch)
    event = local_event(name="PJSIP/DO_NOT_LOG_NAME")
    engine = controlled_engine([
        _ControlledResponse(200, event["channel"]),
    ], event)
    assert not await engine._is_owned_local_ingress(event, event["channel"])
    assert_diagnostic(logger, "channel_type_mismatch", "channel")


@pytest.mark.asyncio
@pytest.mark.parametrize("status_value", ["DO_NOT_LOG_STATUS", True, 999, {"private": "DO_NOT_LOG_STATUS"}])
async def test_payload_status_is_not_logged_unless_known_integer_http_status(monkeypatch, status_value):
    logger = diagnostic_logger(monkeypatch)
    engine = controlled_engine([
        _ControlledResponse(200, {"id": LOCAL_ID, "name": "DO_NOT_LOG_NAME", "status": status_value}),
    ])
    assert not await engine._is_owned_local_ingress(local_event(), local_event()["channel"])
    assert_diagnostic(logger, "channel_name_mismatch", "channel")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["channel"])
async def test_real_transport_timeout_is_one_budget_cancels_and_never_retries(monkeypatch, phase):
    logger = diagnostic_logger(monkeypatch)
    blocked = _ControlledResponse(200, local_event()["channel"], asyncio.Event())
    replies = [blocked]
    engine = controlled_engine(replies)
    engine.config.streaming.connection_timeout_ms = 20
    await asyncio.wait_for(engine._handle_stasis_start(local_event()), timeout=0.5)
    values = assert_diagnostic(logger, "ownership_timeout", phase)
    assert values["elapsed_ms"] >= 15
    assert blocked.cancelled
    assert len(engine.ari_client.http_session.requests) == 1
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    engine.ari_client.hangup_channel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["channel"])
async def test_real_transport_exception_has_safe_phase_without_exception_text(monkeypatch, phase):
    logger = diagnostic_logger(monkeypatch)
    replies = [RuntimeError("DO_NOT_LOG_EXCEPTION")]
    engine = controlled_engine(replies)
    assert not await engine._is_owned_local_ingress(local_event(), local_event()["channel"])
    assert_diagnostic(logger, "ownership_exception", phase)
    assert len(engine.ari_client.http_session.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["channel"])
async def test_real_transport_cancellation_propagates_without_diagnostic_or_role_claim(monkeypatch, phase):
    logger = diagnostic_logger(monkeypatch)
    blocked = _ControlledResponse(200, local_event()["channel"], asyncio.Event())
    replies = [blocked]
    engine = controlled_engine(replies)
    task = asyncio.create_task(engine._handle_stasis_start(local_event()))
    try:
        await asyncio.wait_for(blocked.entered.wait(), timeout=0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert blocked.cancelled
    assert not [c for c in logger.warning.call_args_list if c.args == ("Local ingress ownership rejected",)]
    assert not engine._seen_caller_stasis_channels
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_delayed_channel_read_does_not_trigger_an_automatic_retry(monkeypatch):
    logger = diagnostic_logger(monkeypatch)
    release = asyncio.Event()
    delayed = _ControlledResponse(200, local_event()["channel"], release)
    engine = controlled_engine([delayed])
    task = asyncio.create_task(engine._handle_stasis_start(local_event()))
    try:
        await asyncio.wait_for(delayed.entered.wait(), timeout=0.5)
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, timeout=0.5)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, local_event()["channel"])
    assert len(engine.ari_client.http_session.requests) == 1
    assert not getattr(engine, "_local_ingress_checks", {})
    assert not any(call.args == ("Local ingress ownership rejected",) for call in logger.warning.call_args_list)
    engine.ari_client.http_session.replies.append(_ControlledResponse(200, local_event()["channel"]))
    await asyncio.sleep(0)
    assert len(engine.ari_client.http_session.requests) == 1

@pytest.mark.asyncio
async def test_real_client_two_channel_reads_interleave_but_only_one_dispatch_wins():
    release = asyncio.Event()
    first = _ControlledResponse(200, local_event()["channel"], release)
    second = _ControlledResponse(200, local_event()["channel"], release)
    engine = controlled_engine([
        first, second,
    ])
    tasks = [asyncio.create_task(engine._handle_stasis_start(local_event())) for _ in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(first.entered.wait(), second.entered.wait()), timeout=0.5)
        engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
        assert len(engine.ari_client.http_session.requests) == 2
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=0.5)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, local_event()["channel"])


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
async def test_both_owned_local_route_contexts_share_actual_app_and_real_client(agent):
    event = local_event(agent=agent)
    engine = controlled_engine([
        _ControlledResponse(200, event["channel"]),
    ], event)
    assert event["application"] == APP != event["channel"]["dialplan"]["context"]
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, event["channel"])


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
async def test_native_pjsip_main_and_ext7_routes_keep_dispatch_without_ownership_reads(agent):
    event = local_event(channel_id="native-pjsip-fixture", name="PJSIP/1-fixture", agent=agent)
    engine = controlled_engine([], event)
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(event["channel"]["id"], event["channel"])
    assert not engine.ari_client.http_session.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
async def test_registered_auxiliary_local_on_each_route_stays_helper_with_real_client(agent):
    event = local_event(agent=agent)
    engine = controlled_engine([], event)
    engine.pending_local_channels[LOCAL_ID] = "original-caller"
    await engine._handle_stasis_start(event)
    engine._handle_local_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, event["channel"])
    engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
    assert not engine.ari_client.http_session.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
async def test_both_ai_agent_selectors_reach_original_initializer_and_unchanged_preroll(monkeypatch, tmp_path, agent):
    engine, pipeline, playback, waiting, ready = timing_engine(monkeypatch, tmp_path, agent=agent)
    try:
        await engine._handle_stasis_start(local_event(agent=agent))
        session = await engine.session_store.get_by_call_id(LOCAL_ID)
        assert session.context_name == agent
        if agent == "aimee":
            # This dummy config declares no named audio profiles; do not equate
            # the context's raw profile label with its normalized transport.
            assert session.transport_profile.profile_name == "legacy_compat"
            assert session.transport_profile.wire_sample_rate == 8000
        assert session.call_id == session.caller_channel_id == LOCAL_ID
        await asyncio.wait_for(waiting.wait(), timeout=1)
        queue = playback.queues[LOCAL_ID]
        assert queue.empty()
        session.media_rx_confirmed = True
        await asyncio.wait_for(ready.wait(), timeout=1)
        assert queue.get_nowait() == b"\xff" * 2800
        assert engine.config.streaming.greeting_min_start_ms == 350
        assert engine.config.streaming.greeting_media_ready_delay_ms == 350
        assert playback.starts[0][1]["playback_type"] == "pipeline-tts-greeting"
        while not queue.empty():
            queue.get_nowait()
        await engine._stream_pipeline_tts_text(LOCAL_ID, session, pipeline, "next reply")
        assert engine._wait_for_initial_media.await_count == 1
        assert playback.drained == [b"first-spoken-chunk", b"second-spoken-chunk", None]
    finally:
        await engine._cleanup_call(LOCAL_ID)
    assert LOCAL_ID not in engine._seen_caller_stasis_channels


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
@pytest.mark.parametrize("inventory", [{}, {"channel_ids": []}, {"channel_ids": [LOCAL_ID, LOCAL_ID]}, {"channel_ids": ["unrelated-subscription"]}, {"channel_ids": "__AST_CHANNEL_ALL_TOPIC"}, {"channel_ids": ["__AST_CHANNEL_ALL_TOPIC"]}])
async def test_subscription_inventory_is_not_an_ownership_permission_gate(agent, inventory):
    event = local_event(agent=agent)
    engine = controlled_engine([_ControlledResponse(200, event["channel"])], event)
    engine.ari_client.http_session.application_response = _ControlledResponse(200, dict(inventory, name=APP))
    await engine._handle_stasis_start(event)
    engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, event["channel"])
    assert not getattr(engine, "_local_ingress_checks", {})
    assert engine.ari_client.http_session.requests == [("GET", f"channels/{LOCAL_ID}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
@pytest.mark.parametrize("terminal", ["StasisEnd", "ChannelDestroyed"])
async def test_terminal_event_during_first_session_lookup_is_bound_before_await(agent, terminal):
    event = local_event(agent=agent)
    engine = controlled_engine([], event)
    engine._outbound_awaiting_amd_channel_ids = set()
    engine.pending_audiosocket_channels = {}
    engine._handle_outbound_channel_destroyed = AsyncMock()
    engine._cleanup_call = AsyncMock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def find(*args):
        entered.set()
        await release.wait()
        return None

    engine._find_caller_for_local = AsyncMock(side_effect=find)
    start = asyncio.create_task(engine._handle_stasis_start(event))
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.5)
        assert len(engine._local_ingress_checks[LOCAL_ID]["tasks"]) == 1
        if terminal == "StasisEnd":
            await engine._handle_stasis_end(dict(event, type=terminal))
        else:
            await engine._handle_channel_destroyed(dict(event, type=terminal))
        release.set()
        await asyncio.wait_for(start, timeout=0.5)
        engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
        engine._handle_local_stasis_start_hybrid.assert_not_awaited()
        assert not engine.ari_client.http_session.requests
        assert not engine._local_ingress_checks
    finally:
        release.set()
        await asyncio.gather(start, return_exceptions=True)

@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["StasisEnd", "ChannelDestroyed"])
@pytest.mark.parametrize("boundary", ["first_native_read", "answer_channel", "create_bridge", "add_channel_to_bridge", "_save_session", "_resolve_audio_profile", "_assign_pipeline_to_session", "_start_external_media_channel", "_ensure_provider_session_started"])
async def test_real_initializer_terminal_at_setup_await_never_revives_session_or_publishes(monkeypatch, tmp_path, boundary, terminal):
    engine, pipeline, playback, waiting, ready = timing_engine(monkeypatch, tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    ended = False
    late_saves = []
    original_save = engine._save_session

    async def record_save(session, **kwargs):
        if ended:
            late_saves.append(session.call_id)
        return await original_save(session, **kwargs)

    engine._save_session = record_save
    if boundary == "first_native_read":
        owner = engine.ari_client
        name = "send_command"
    elif boundary in ("answer_channel", "create_bridge", "add_channel_to_bridge"):
        owner = engine.ari_client
        name = boundary
    else:
        owner = engine
        name = boundary
    original = getattr(owner, name)
    blocked_once = False

    async def pause(*args, **kwargs):
        nonlocal blocked_once
        is_boundary = boundary != "first_native_read" or (
            args[0] == "GET" and args[1].endswith("/variable")
            and (kwargs.get("params") or {}).get("variable") == "AAVA_OUTBOUND"
        )
        if is_boundary and not blocked_once:
            blocked_once = True
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    setattr(owner, name, pause)
    start = asyncio.create_task(engine._handle_stasis_start(local_event()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert start in engine._local_ingress_checks[LOCAL_ID]["tasks"]
        ended = True
        if terminal == "StasisEnd":
            await engine._handle_stasis_end(dict(local_event(), type=terminal))
        else:
            await engine._handle_channel_destroyed(dict(local_event(), type=terminal))
        published_at_end = len(playback.starts)
        release.set()
        await asyncio.wait_for(start, timeout=1)
        for _ in range(3):
            await asyncio.sleep(0)
        assert await engine.session_store.get_by_call_id(LOCAL_ID) is None
        assert not late_saves
        assert len(playback.starts) == published_at_end
        assert LOCAL_ID not in engine._pipeline_tasks
        assert LOCAL_ID not in engine._pipeline_transcript_queues
        assert not engine._local_ingress_checks
        assert LOCAL_ID not in engine._seen_caller_stasis_channels
    finally:
        release.set()
        await asyncio.gather(start, return_exceptions=True)
        await engine._cleanup_call(LOCAL_ID)


class _LateReply(_ControlledResponse):
    async def __aenter__(self):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # A late transport completion must not defeat generation invalidation.
            self.cancelled = True
            await self.release.wait()
        return self


@pytest.mark.asyncio
async def test_late_stasis_snapshot_after_terminal_cancellation_cannot_initialize():
    release = asyncio.Event()
    late = _LateReply(200, local_event()["channel"], release)
    engine = controlled_engine([late])
    engine._outbound_awaiting_amd_channel_ids = set()
    engine._cleanup_call = AsyncMock()
    start = asyncio.create_task(engine._handle_stasis_start(local_event()))
    try:
        await asyncio.wait_for(late.entered.wait(), timeout=0.5)
        await engine._handle_stasis_end(dict(local_event(), type="StasisEnd"))
        release.set()
        await asyncio.wait_for(start, timeout=0.5)
        assert late.cancelled
        engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
        assert not engine._local_ingress_checks
        assert LOCAL_ID not in engine._seen_caller_stasis_channels
    finally:
        release.set()
        await asyncio.gather(start, return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_cancels_only_matching_duplicate_generation_not_other_call():
    release = asyncio.Event()
    first = _ControlledResponse(200, local_event()["channel"], release)
    second = _ControlledResponse(200, local_event()["channel"], release)
    other = local_event(channel_id="independent-native-local", name="Local/9@retained-route-00000044;2", agent="aimee")
    engine = controlled_engine([
        first, second,
        _ControlledResponse(200, other["channel"]),
    ])
    engine._outbound_awaiting_amd_channel_ids = set()
    engine._cleanup_call = AsyncMock()
    starts = [asyncio.create_task(engine._handle_stasis_start(local_event())) for _ in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(first.entered.wait(), second.entered.wait()), timeout=0.5)
        assert len(engine._local_ingress_checks[LOCAL_ID]["tasks"]) == 2
        await engine._handle_stasis_end(dict(local_event(), type="StasisEnd"))
        await engine._handle_stasis_start(other)
        release.set()
        await asyncio.wait_for(asyncio.gather(*starts), timeout=0.5)
        engine._handle_caller_stasis_start_hybrid.assert_awaited_once_with(other["channel"]["id"], other["channel"])
        assert not engine._local_ingress_checks
        assert LOCAL_ID not in engine._seen_caller_stasis_channels
    finally:
        release.set()
        await asyncio.gather(*starts, return_exceptions=True)


@pytest.mark.asyncio
async def test_auxiliary_role_registered_during_read_wins_before_atomic_caller_claim():
    release = asyncio.Event()
    channel = _ControlledResponse(200, local_event()["channel"], release)
    engine = controlled_engine([channel])
    start = asyncio.create_task(engine._handle_stasis_start(local_event()))
    try:
        await asyncio.wait_for(channel.entered.wait(), timeout=0.5)
        engine.pending_local_channels[LOCAL_ID] = "existing-caller-fixture"
        release.set()
        await asyncio.wait_for(start, timeout=0.5)
        engine._handle_local_stasis_start_hybrid.assert_awaited_once_with(LOCAL_ID, local_event()["channel"])
        engine._handle_caller_stasis_start_hybrid.assert_not_awaited()
        assert not engine._local_ingress_checks
    finally:
        release.set()
        await asyncio.gather(start, return_exceptions=True)
