import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from src.audio.audiosocket_server import AudioSocketServer, TYPE_AUDIO, TYPE_UUID
from src.config import AppConfig
from src.core.models import CallSession
from src.engine import Engine
from src.pipelines.base import LLMComponent
pytest.register_assert_rewrite("test_pipeline_message_deposit_true_path")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pipeline_message_deposit_true_path import _RecordingTTS, _Resolution, _ResultSTT, _wait_until


class _ColdNativeLLM(LLMComponent):
    supports_streaming = True

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.serial_calls = 0

    async def generate(self, *args, **kwargs):
        self.serial_calls += 1
        return ""

    async def generate_stream(self, *args, **kwargs):
        self.started.set()
        try:
            await self.release.wait()
            yield "Your appointment is tomorrow. "
        finally:
            self.closed.set()


@asynccontextmanager
async def _native_wait_pipeline(monkeypatch, tmp_path, *, scoped=True, established_context=None,
                                llm=None, tts=None, downstream_mode="stream"):
    monkeypatch.setenv("CALL_HISTORY_DB_PATH", str(tmp_path / "history.db"))
    engine = Engine(AppConfig(**{
        "default_provider": "local", "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "localhost", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "", "prompt": "Synthetic test", "model": "test"},
        "pipelines": {"native-main": {}}, "active_pipeline": "native-main",
        "audio_transport": "audiosocket", "downstream_mode": downstream_mode,
        "streaming": {"pipeline_streaming_overlap": True, "jitter_buffer_ms": 50, "greeting_media_ready_delay_ms": 350},
    }))
    engine.pipeline_orchestrator._started = True
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    stt, llm, tts = _ResultSTT(), llm or _ColdNativeLLM(), tts or _RecordingTTS()
    resolution = _Resolution(stt, llm, tts)
    resolution.llm_options = {"aggregation_timeout_sec": 0.02}
    if scoped:
        resolution.llm_options.update(session_user_from_call_id=True, call_id_header_enabled=True)
    engine.pipeline_orchestrator.get_pipeline = lambda *args, **kwargs: resolution
    call_id = str(uuid.uuid4())
    session = CallSession(call_id=call_id, caller_channel_id=call_id, audio_capture_enabled=True)
    session.pipeline_name = "native-main"
    if established_context:
        session.is_outbound = True
        session.rita_outbound_context = established_context
        from src.core.rita_outbound_context import rita_outbound_greeting
        session.provider_overrides["greeting"] = rita_outbound_greeting(established_context)
    await engine.session_store.upsert_call(session)
    bound = asyncio.Event()

    async def on_uuid(connection, received):
        assert received == call_id
        session.audiosocket_conn_id = connection
        engine.conn_to_channel[connection] = call_id
        await engine.session_store.upsert_call(session)
        bound.set()
        return True

    async def on_audio(connection, frame):
        await engine._audiosocket_handle_audio(connection, frame)

    server = AudioSocketServer("127.0.0.1", 0, on_uuid=on_uuid, on_audio=on_audio)
    await server.start()
    engine.audio_socket_server = server
    manager = engine.streaming_playback_manager
    manager.audiosocket_server = server
    manager.audiosocket_format = "slin"
    frames = []
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(bytes([TYPE_UUID]) + (16).to_bytes(2, "big") + uuid.UUID(call_id).bytes)
    await writer.drain()
    await asyncio.wait_for(bound.wait(), 2)

    async def capture():
        try:
            while True:
                header = await reader.readexactly(3)
                payload = await reader.readexactly(int.from_bytes(header[1:], "big"))
                if header[0] == TYPE_AUDIO:
                    frames.append((time.monotonic(), payload))
        except asyncio.IncompleteReadError:
            pass

    capture_task = asyncio.create_task(capture())
    playback_observations = []
    native_start = manager.start_streaming_playback

    async def record_start(call, queue, **kwargs):
        playback_observations.append((bool(manager._caller_wait_ambience_tasks), list(queue._queue), session.audio_capture_enabled))
        return await native_start(call, queue, **kwargs)

    manager.start_streaming_playback = record_start
    try:
        await engine._ensure_pipeline_runner(session, forced=True)
        await asyncio.wait_for(stt.started.wait(), 2)
        yield SimpleNamespace(engine=engine, session=session, stt=stt, llm=llm, tts=tts,
                              manager=manager, frames=frames, starts=playback_observations)
    finally:
        await engine._cleanup_call(call_id)
        writer.close()
        await writer.wait_closed()
        await server.stop()
        await capture_task


@pytest.mark.asyncio
async def test_native_cold_wait_paces_real_audio_for_54_seconds_without_gating(monkeypatch, tmp_path):
    async with _native_wait_pipeline(monkeypatch, tmp_path) as h:
        await h.stt.results.put("Please check my appointment details.")
        await asyncio.wait_for(h.llm.started.wait(), 3)
        await _wait_until(lambda: len(h.frames) >= 3)
        owner = h.manager._caller_wait_ambience_tasks[h.session.call_id]
        assert await h.manager.start_caller_wait_ambience(h.session.call_id) is False
        await asyncio.sleep(54.5)
        assert h.manager._caller_wait_ambience_tasks == {h.session.call_id: owner}
        assert h.manager.active_streams == {}
        assert h.session.audio_capture_enabled is True
        assert not h.session.tts_playing
        assert h.tts.texts == []
        frames = list(h.frames)
        assert len(frames) >= 2500
        assert frames[-1][0] - frames[0][0] > 53.0
        assert all(len(payload) == 320 for _, payload in frames)
        assert any(any(payload) for _, payload in frames)
        gaps = [right[0] - left[0] for left, right in zip(frames, frames[1:])]
        assert max(gaps) < 1.0
        h.llm.release.set()
        await _wait_until(lambda: bool(h.starts))
        assert h.starts[0][0] is False
        assert any(h.starts[0][1])
        assert h.starts[0][2] is True
        assert owner.done()
        await _wait_until(lambda: not h.manager.active_streams)
        assert not h.manager._caller_wait_ambience_tasks
        assert h.llm.serial_calls == 0
        print(f"PACED_AUDIO frames={len(frames)} span={frames[-1][0]-frames[0][0]:.6f}s max_gap={max(gaps):.6f}s")


@pytest.mark.asyncio
async def test_native_model_wait_teardown_joins_writer_and_inference(monkeypatch, tmp_path):
    async with _native_wait_pipeline(monkeypatch, tmp_path) as h:
        await h.stt.results.put("Please check my appointment details.")
        await asyncio.wait_for(h.llm.started.wait(), 3)
        await _wait_until(lambda: bool(h.frames))
        await h.engine._cleanup_call(h.session.call_id)
        assert h.llm.closed.is_set()
        assert not h.manager._caller_wait_ambience_tasks
        assert not h.manager._caller_wait_ambience_stops
        assert not h.engine._pipeline_tasks
        count = len(h.frames)
        await asyncio.sleep(0.08)
        assert len(h.frames) == count
        assert not h.starts
        assert h.session.provider_name == "local"


@pytest.mark.asyncio
async def test_legacy_model_wait_does_not_opt_into_new_ambience(monkeypatch, tmp_path):
    async with _native_wait_pipeline(monkeypatch, tmp_path, scoped=False) as h:
        await h.stt.results.put("Please check my appointment details.")
        await asyncio.wait_for(h.llm.started.wait(), 3)
        await asyncio.sleep(0.08)
        assert h.frames == []
        assert not h.manager._caller_wait_ambience_tasks
        assert h.session.audio_capture_enabled is True


@pytest.mark.asyncio
async def test_outbound_greeting_uses_existing_pipeline_and_retained_preroll(monkeypatch, tmp_path):
    context = {"status": "ready", "purpose": "your requested appointment check.", "target_display_label": "Gary"}
    async with _native_wait_pipeline(monkeypatch, tmp_path, established_context=context) as h:
        await _wait_until(lambda: bool(h.tts.texts))
        assert h.tts.texts == ["Hi Gary, it's AIm\u00e8e. I'm calling because your requested appointment check."]
        assert not h.llm.started.is_set()
        assert h.llm.serial_calls == 0
        await _wait_until(lambda: not h.manager.active_streams and len(h.frames) >= 9)
        output = b"".join(payload for _, payload in h.frames)
        # 350ms in the negotiated PCM16/8kHz AudioSocket path is 5600 bytes.
        assert output[:5600] == b"\x00" * 5600
        assert any(output[5600:])


class _ColdSerialLLM(LLMComponent):
    supports_streaming = False

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.calls = 0

    async def generate(self, *args, **kwargs):
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
            return "Your appointment is tomorrow."
        finally:
            self.closed.set()


class _PendingSerialTTS(_RecordingTTS):
    def __init__(self, mode):
        super().__init__()
        self.downstream_mode_override = mode
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def synthesize(self, call_id, text, options):
        self.started.set()
        self.ambience_active_at_start.append(bool(self.on_start and self.on_start()))
        try:
            await self.release.wait()
            async for chunk in super().synthesize(call_id, text, options):
                yield chunk
        finally:
            self.closed.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stream", "file", "file-fallback"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_serial_speech_joins_model_wait_before_playback(monkeypatch, tmp_path, mode, cancel):
    llm = _ColdSerialLLM()
    tts = _PendingSerialTTS("file" if mode == "file" else "stream")
    async with _native_wait_pipeline(monkeypatch, tmp_path, llm=llm, tts=tts,
                                    downstream_mode="file" if mode == "file" else "stream") as h:
        tts.on_start = lambda: h.manager._caller_wait_ambience_tasks.get(h.session.call_id)
        file_starts = []
        file_played = asyncio.Event()

        async def create_fixture_file(audio, playback_id):
            file_starts.append((bool(h.manager._caller_wait_ambience_tasks), bytes(audio),
                                h.session.audio_capture_enabled))
            return str(tmp_path / (playback_id + ".ulaw"))

        async def fixture_ari_play(session, audio_file, playback_id):
            file_played.set()
            return True

        # Run native file gating/reference lifecycle; inject only filesystem/ARI effects.
        monkeypatch.setattr(h.engine.playback_manager, "_create_audio_file", create_fixture_file)
        monkeypatch.setattr(h.engine.playback_manager, "_play_via_ari", fixture_ari_play)
        monkeypatch.setattr(h.engine.playback_manager, "_schedule_gating_fallback", AsyncMock())
        monkeypatch.setattr(h.engine.playback_manager, "_cleanup_audio_file", AsyncMock())
        if mode == "file-fallback":
            async def fail_before_serial_stream(call, queue, **kwargs):
                h.starts.append((bool(h.manager._caller_wait_ambience_tasks), list(queue._queue),
                                 h.session.audio_capture_enabled))
                raise RuntimeError("synthetic serial stream start failure")
            monkeypatch.setattr(h.manager, "start_streaming_playback", fail_before_serial_stream)

        await h.stt.results.put("Please check my appointment details.")
        await asyncio.wait_for(llm.started.wait(), 3)
        await _wait_until(lambda: bool(h.frames))
        owner = h.manager._caller_wait_ambience_tasks[h.session.call_id]
        assert h.session.audio_capture_enabled is True
        llm.release.set()
        await asyncio.wait_for(tts.started.wait(), 3)
        assert owner.done()
        assert not h.manager._caller_wait_ambience_tasks
        assert not h.manager._caller_wait_ambience_stops
        assert tts.ambience_active_at_start == [False]
        if h.starts:
            assert all(observation[0] is False for observation in h.starts)
        assert llm.calls == 1

        if cancel:
            await h.engine._cleanup_call(h.session.call_id)
            assert tts.closed.is_set() and llm.closed.is_set()
            assert not h.engine._pipeline_tasks and not h.manager.active_streams
            assert not h.manager._caller_wait_ambience_tasks
            await asyncio.sleep(0.06)
            settled_frames = len(h.frames)
            await asyncio.sleep(0.06)
            assert len(h.frames) == settled_frames
            assert not file_starts
            assert h.session.provider_name == "local"
        else:
            tts.release.set()
            if mode == "stream":
                await _wait_until(lambda: tts.closed.is_set() and not h.manager.active_streams)
                assert not file_starts
            else:
                await asyncio.wait_for(file_played.wait(), 3)
                assert file_starts and file_starts[0][0] is False
                assert file_starts[0][1] and any(file_starts[0][1])
                assert file_starts[0][2] is True
                await _wait_until(lambda: bool(h.engine.session_store._playbacks))
                playback_id = next(iter(h.engine.session_store._playbacks))
                await h.engine.playback_manager.on_playback_finished(playback_id)
            assert not h.manager._caller_wait_ambience_tasks
            assert llm.calls == 1
