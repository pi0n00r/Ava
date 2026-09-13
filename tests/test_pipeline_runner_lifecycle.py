import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import AppConfig
from src.engine import Engine, _PipelinePlaybackInterrupted
from src.pipelines.base import STTComponent, LLMComponent, LLMResponse, TTSComponent
from src.tools.base import Tool, ToolCategory, ToolDefinition
from src.tools.registry import ToolRegistry
from src.tools.telephony.hangup_policy import normalize_hangup_policy


class _StubSTT(STTComponent):
    async def transcribe(self, call_id, audio_pcm16, sample_rate_hz, options):
        return "hi"


class _StreamingStubSTT(STTComponent):
    supports_streaming = True

    def __init__(self):
        self.open_options = None
        self.start_options = None
        self.start_format = None
        self.sent = []
        self.started = asyncio.Event()
        self.audio_sent = asyncio.Event()
        self._keep_receiving = asyncio.Event()

    async def open_call(self, call_id, options):
        self.open_options = dict(options)

    async def transcribe(self, call_id, audio_pcm16, sample_rate_hz, options):
        raise AssertionError("streaming adapter should not use buffered transcription")

    async def start_stream(self, call_id, options, *, sample_rate_hz, fmt):
        self.start_options = dict(options)
        self.start_format = (sample_rate_hz, fmt)
        self.started.set()

    async def send_audio(self, call_id, audio, *, fmt="pcm16_16k"):
        self.sent.append((bytes(audio), fmt))
        self.audio_sent.set()

    async def iter_results(self, call_id):
        await self._keep_receiving.wait()
        if False:
            yield ""

    async def stop_stream(self, call_id):
        self._keep_receiving.set()


class _ResultStreamingStubSTT(_StreamingStubSTT):
    def __init__(self):
        super().__init__()
        self.results = asyncio.Queue()

    async def iter_results(self, call_id):
        while True:
            value = await self.results.get()
            if value is None:
                return
            yield value

    async def stop_stream(self, call_id):
        self.results.put_nowait(None)


class _StubLLM(LLMComponent):
    async def generate(self, call_id, transcript, context, options):
        return "hello"


class _MessageTakingStreamingLLM(LLMComponent):
    supports_streaming = True

    async def generate(self, call_id, transcript, context, options):
        return "Of course. What would you like me to tell Gary?"

    async def generate_stream(self, call_id, transcript, context, options):
        assert transcript == "Could I leave a message?"
        yield "Of course. "
        yield "What would you like me to tell Gary?"


class _RecordingLLM(LLMComponent):
    def __init__(self):
        self.transcripts = []
        self.called = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.transcripts.append(transcript)
        self.called.set()
        return ""


class _BlockingLLM(LLMComponent):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return "late response after caller hangup"


class _ToolOnlyThenTextLLM(LLMComponent):
    def __init__(self):
        self.calls = 0
        self.completed = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    {
                        "id": "pipeline-tool-1",
                        "name": "transcript_tool",
                        "parameters": {},
                    }
                ],
            )
        self.completed.set()
        return LLMResponse(text="Done", tool_calls=[])


class _AllowedThenDisallowedToolLLM(LLMComponent):
    def __init__(self):
        self.calls = 0

    async def generate(self, call_id, transcript, context, options):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    {
                        "id": "pipeline-allowed-1",
                        "name": "transcript_tool",
                        "parameters": {},
                    }
                ],
            )
        return LLMResponse(
            text="",
            tool_calls=[
                {
                    "id": "pipeline-disallowed-1",
                    "name": "disallowed_tool",
                    "parameters": {},
                }
            ],
        )


class _TranscriptTool(Tool):
    @property
    def definition(self):
        return ToolDefinition(
            name="transcript_tool",
            description="Test tool",
            category=ToolCategory.BUSINESS,
        )

    async def execute(self, parameters, context):
        return {"status": "success", "message": "Tool completed"}


class _ExecutionProbeTool(Tool):
    def __init__(self):
        self.executed = asyncio.Event()

    @property
    def definition(self):
        return ToolDefinition(
            name="disallowed_tool",
            description="Must remain blocked",
            category=ToolCategory.BUSINESS,
        )

    async def execute(self, parameters, context):
        self.executed.set()
        return {"status": "success", "message": "Should not execute"}


class _CancellationResistantLLM(LLMComponent):
    """Model a provider request that completes after task cancellation."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                continue
        return "late response after caller hangup"


class _StubTTS(TTSComponent):
    async def synthesize(self, call_id, text, options):
        yield b"ulaw-bytes"


class _SilentTTS(TTSComponent):
    async def synthesize(self, call_id, text, options):
        if False:
            yield b""


class _RecordingTTS(TTSComponent):
    def __init__(self):
        self.started = asyncio.Event()

    async def synthesize(self, call_id, text, options):
        self.started.set()
        yield b"ulaw-bytes"


class _HangingTTS(TTSComponent):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(self, call_id, text, options):
        """Hold synthesis open without yielding caller-facing audio."""
        self.started.set()
        await self.release.wait()
        if False:
            yield b""


class _StreamOwnershipStub:
    def __init__(self, stream_id="stream-1"):
        self.stream_id = stream_id
        self.active = True

    def is_stream_active(self, call_id, stream_id=None):
        return self.active and stream_id == self.stream_id


class _DrainingStreamingStub(_StreamOwnershipStub):
    def __init__(self):
        super().__init__("tool-stream")
        self.start_args = None
        self.queue = None
        self.drained = []

    async def start_streaming_playback(self, call_id, queue, **kwargs):
        self.start_args = (call_id, kwargs)
        self.queue = queue
        return self.stream_id

    async def stop_streaming_playback(self, call_id, *, drain=False):
        assert call_id == "call-tool"
        if drain:
            while True:
                chunk = await self.queue.get()
                if chunk is None:
                    break
                self.drained.append(chunk)
        self.active = False
        return True


class _CollectingStreamingStub(_StreamOwnershipStub):
    def __init__(self):
        super().__init__("message-stream")
        self.drained = []
        self.done = asyncio.Event()
        self._consumer = None

    async def start_streaming_playback(self, call_id, queue, **kwargs):
        async def consume():
            while True:
                chunk = await queue.get()
                if chunk is None:
                    self.done.set()
                    return
                self.drained.append(chunk)

        self._consumer = asyncio.create_task(consume())
        return self.stream_id

    async def stop_streaming_playback(self, call_id, *, drain=False):
        self.active = False
        if self._consumer and not self._consumer.done():
            self._consumer.cancel()
        return True


class _MessageTakingTTS(TTSComponent):
    def __init__(self):
        self.texts = []

    async def synthesize(self, call_id, text, options):
        self.texts.append(text)
        yield (text.encode("utf-8") or b"audio")


class _PCM16TTS:
    downstream_mode_override = "auto"

    async def synthesize(self, call_id, text, options):
        assert (call_id, text) == ("call-tool", "Available slots")
        yield b"pcm16-a"
        yield b"pcm16-b"


@pytest.mark.asyncio
async def test_pipeline_stream_put_exits_when_barge_in_stops_full_queue():
    engine = Engine.__new__(Engine)
    manager = _StreamOwnershipStub()
    engine.streaming_playback_manager = manager
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(b"already-full")

    async def stop_stream():
        await asyncio.sleep(0.05)
        manager.active = False

    stopper = asyncio.create_task(stop_stream())
    with pytest.raises(_PipelinePlaybackInterrupted):
        await asyncio.wait_for(
            engine._put_pipeline_stream_chunk(
                "call-deadlock", "stream-1", queue, b"blocked", wait_slice_sec=0.02
            ),
            timeout=0.5,
        )
    await stopper


@pytest.mark.asyncio
async def test_pipeline_stream_put_rejects_replaced_stream_owner():
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = _StreamOwnershipStub(stream_id="new-stream")
    queue = asyncio.Queue(maxsize=1)

    with pytest.raises(_PipelinePlaybackInterrupted):
        await engine._put_pipeline_stream_chunk(
            "call-replaced", "old-stream", queue, b"stale"
        )
    assert queue.empty()


@pytest.mark.asyncio
async def test_pipeline_stream_put_allows_healthy_consumer():
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = _StreamOwnershipStub()
    queue = asyncio.Queue(maxsize=1)

    await engine._put_pipeline_stream_chunk(
        "call-healthy", "stream-1", queue, b"audio"
    )
    assert await queue.get() == b"audio"


def test_pipeline_pending_stream_sustained_inbound_cannot_barge_before_tx():
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = SimpleNamespace(
        active_streams={"call-pending": {"first_frame_ts": 0.0}}
    )
    session = SimpleNamespace(tts_started_ts=1.0)

    for _ in range(8):
        assert (
            engine._pipeline_barge_tts_elapsed_ms(
                "call-pending", session, now=100.0
            )
            is None
        )


def test_pipeline_barge_protection_is_anchored_to_first_tx_frame():
    engine = Engine.__new__(Engine)
    session = SimpleNamespace(tts_started_ts=1.0)
    engine.streaming_playback_manager = SimpleNamespace(
        active_streams={"call-audible": {"first_frame_ts": 99.85}}
    )
    assert engine._pipeline_barge_tts_elapsed_ms(
        "call-audible", session, now=100.0
    ) == 150

    engine.streaming_playback_manager.active_streams["call-audible"][
        "first_frame_ts"
    ] = 99.75
    elapsed_ms = engine._pipeline_barge_tts_elapsed_ms(
        "call-audible", session, now=100.0
    )
    assert elapsed_ms == 250
    candidate_ms = sum(20 for _ in range(6) if elapsed_ms >= 200 and 500 >= 300)
    assert candidate_ms == 120


def test_near_end_reply_is_released_once_into_one_second_stt_turn():
    engine = Engine.__new__(Engine)
    session = SimpleNamespace(
        call_id="call-near-end",
        vad_state={},
        tts_started_ts=10.0,
        tts_ended_ts=20.0,
    )
    speech_frame = b"\xff\x7f" * 160  # 20 ms at 8 kHz PCM16

    # "The sky is blue" starts 100 ms before TTS ends, but has not reached
    # the 120 ms barge threshold.
    for index in range(5):
        engine._buffer_pipeline_pending_caller_audio(
            session,
            speech_frame,
            8000,
            frame_ms=20,
            now=19.90 + (index * 0.02),
        )

    first_post_gate = engine._take_pipeline_pending_caller_audio(
        session,
        speech_frame,
        8000,
        now=20.02,
        energy=32767,
        threshold=1000,
    )
    # A natural 20 ms dip immediately after capture reopens is held, then two
    # energetic frames prove continuation and release the onset once.
    post_gap = engine._take_pipeline_pending_caller_audio(
        session, b"\x00\x00" * 160, 8000,
        now=20.04, energy=0, threshold=1000,
    )
    subsequent = [
        engine._take_pipeline_pending_caller_audio(
            session,
            speech_frame,
            8000,
            now=20.06 + (index * 0.02),
            energy=32767,
            threshold=1000,
        )
        for index in range(19)
    ]

    stt_audio = first_post_gate + b"".join(subsequent)
    assert first_post_gate == b""  # One energetic tail frame is not enough.
    assert post_gap == b""
    assert len(subsequent[0]) == len(speech_frame) * 8
    assert len(stt_audio) == len(speech_frame) * 26  # one 520 ms turn
    finals = ["The sky is blue."] if len(stt_audio) >= 8000 else []
    assert finals == ["The sky is blue."]
    assert "pipeline_pending_caller_audio" not in session.vad_state


def test_near_end_silence_or_tts_tail_is_not_released():
    engine = Engine.__new__(Engine)
    session = SimpleNamespace(
        call_id="call-echo-tail",
        vad_state={},
        tts_started_ts=10.0,
        tts_ended_ts=20.0,
    )
    speech_frame = b"\xff\x7f" * 160
    silence = b"\x00\x00" * 160
    for index in range(4):
        engine._buffer_pipeline_pending_caller_audio(
            session,
            speech_frame,
            8000,
            frame_ms=20,
            now=19.92 + (index * 0.02),
        )

    # One post-gate energetic echo frame and bounded silence are held, never
    # flushed. The candidate expires after the 160 ms reopen window.
    held = engine._take_pipeline_pending_caller_audio(
        session,
        speech_frame,
        8000,
        now=20.02,
        energy=32767,
        threshold=1000,
    )
    assert held == b""

    held_silence = [
        engine._take_pipeline_pending_caller_audio(
            session, silence, 8000, now=20.04 + (index * 0.02),
            energy=0, threshold=1000,
        )
        for index in range(7)
    ]
    assert held_silence[:6] == [b""] * 6
    assert held_silence[6] == silence
    released = engine._take_pipeline_pending_caller_audio(
        session, silence, 8000, now=20.18, energy=0, threshold=1000,
    )
    assert released == silence
    assert "pipeline_pending_caller_audio" not in session.vad_state


def test_valid_barge_releases_buffer_without_duplicate_frames():
    engine = Engine.__new__(Engine)
    session = SimpleNamespace(
        call_id="call-valid-barge",
        vad_state={},
        tts_started_ts=10.0,
        tts_ended_ts=0.0,
    )
    speech_frame = b"\xff\x7f" * 160
    for index in range(6):
        engine._buffer_pipeline_pending_caller_audio(
            session,
            speech_frame,
            8000,
            frame_ms=20,
            now=15.00 + (index * 0.02),
        )

    released = engine._take_pipeline_pending_caller_audio(
        session,
        speech_frame,
        8000,
        now=15.12,
        energy=32767,
        threshold=1000,
        current_already_buffered=True,
    )
    next_frame = engine._take_pipeline_pending_caller_audio(
        session,
        speech_frame,
        8000,
        now=15.14,
        energy=32767,
        threshold=1000,
    )
    assert len(released) == len(speech_frame) * 6
    assert next_frame == speech_frame


@pytest.mark.asyncio
async def test_message_taking_pipeline_emits_nonzero_audio_for_live_request(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {
            "initial_greeting": "",
            "prompt": "You are helpful",
            "model": "test-model",
        },
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
        "streaming": {"pipeline_streaming_overlap": True},
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    tts = _MessageTakingTTS()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=_MessageTakingStreamingLLM(),
        tts_adapter=tts,
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator, "get_pipeline", lambda *args, **kwargs: resolution
    )
    manager = _CollectingStreamingStub()
    engine.streaming_playback_manager = manager
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)

    from src.core.models import CallSession

    call_id = "call-message-taking"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("Could I leave a message?")
    await asyncio.wait_for(manager.done.wait(), timeout=2)

    assert tts.texts == [
        "Of course.",
        "What would you like me to tell Gary?",
    ]
    assert sum(len(chunk) for chunk in manager.drained) > 0
    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
async def test_pipeline_tool_continuation_uses_negotiated_stream_and_drains():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(downstream_mode="stream")
    manager = _DrainingStreamingStub()
    engine.streaming_playback_manager = manager
    pipeline = SimpleNamespace(
        tts_adapter=_PCM16TTS(),
        tts_options={"format": {"encoding": "linear16", "sample_rate_hz": 16000}},
    )

    stream_id = await engine._stream_pipeline_tts_text(
        "call-tool",
        SimpleNamespace(),
        pipeline,
        "Available slots",
    )

    assert stream_id == "tool-stream"
    assert manager.start_args == (
        "call-tool",
        {
            "playback_type": "pipeline-tts",
            "source_encoding": "linear16",
            "source_sample_rate": 16000,
        },
    )
    assert manager.drained == [b"pcm16-a", b"pcm16-b"]
    assert engine._pipeline_tts_uses_streaming(pipeline) is True


def test_pipeline_terminal_fallback_matches_first_live_farewell():
    assert Engine._is_pipeline_farewell_without_tool(
        "Okay, never mind. That's all. Thank you.",
        "Thanks for calling! If you're in the US or Canada, you'll get a text with helpful links in just a moment. Have a great day!",
        normalize_hangup_policy({}),
    )


def test_pipeline_terminal_fallback_rejects_casual_mid_call_thanks():
    assert not Engine._is_pipeline_farewell_without_tool(
        "Thanks, now explain Local Hybrid pricing.",
        "You're welcome. Local Hybrid costs about two tenths of a cent per minute.",
        normalize_hangup_policy({}),
    )


def test_pipeline_terminal_fallback_requires_assistant_farewell():
    assert not Engine._is_pipeline_farewell_without_tool(
        "That's all.",
        "Is there anything else you'd like to know?",
        normalize_hangup_policy({}),
    )


class _StubResolution:
    def __init__(self, stt_adapter=None, stt_options=None, llm_adapter=None, tts_adapter=None):
        self.pipeline_name = "stub"
        self.stt_key = "stub_stt"
        self.stt_adapter = stt_adapter or _StubSTT()
        self.llm_adapter = llm_adapter or _StubLLM()
        self.tts_adapter = tts_adapter or _StubTTS()
        self.stt_options = stt_options or {}
        self.llm_options = {}
        self.tts_options = {}
        self.prepared = True

    def component_summary(self):
        return {"stt": "stub", "llm": "stub", "tts": "stub"}


@pytest.mark.asyncio
async def test_pipeline_hanging_greeting_stops_connection_audio_after_timeout(monkeypatch):
    """A pipeline TTS generator that never yields cannot ring indefinitely."""
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"hanging": {}},
        "active_pipeline": "hanging",
        "audio_transport": "externalmedia",
        "downstream_mode": "file",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    engine._connection_audio_handoff_timeout_seconds = 0.05
    engine.ari_client.stop_playback = AsyncMock(return_value=True)
    hanging_tts = _HangingTTS()
    resolution = _StubResolution(tts_adapter=hanging_tts)
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )
    from src.core.models import CallSession

    call_id = "call-hanging-greeting"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "hanging"
    session.connection_audio_playback_id = "connection-audio-hanging"
    session.connection_audio_media_uri = "tone:ring"
    await engine.session_store.upsert_call(session)

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(hanging_tts.started.wait(), timeout=2)
    for _ in range(20):
        if session.connection_audio_playback_id is None:
            break
        await asyncio.sleep(0.01)

    engine.ari_client.stop_playback.assert_awaited_once_with(
        "connection-audio-hanging"
    )
    assert session.connection_audio_playback_id is None
    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
async def test_pipeline_greeting_is_not_synthesized_after_cleanup_gate(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {
            "initial_greeting": "hello",
            "prompt": "You are helpful",
            "model": "gpt-4o",
        },
        "pipelines": {"gated": {}},
        "active_pipeline": "gated",
        "audio_transport": "audiosocket",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    tts = _RecordingTTS()
    resolution = _StubResolution(tts_adapter=tts)
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )
    original_gate = engine._pipeline_output_allowed

    def gate(call_id, session, *, stage):
        if stage == "greeting-start":
            session.cleanup_in_progress = True
        return original_gate(call_id, session, stage=stage)

    monkeypatch.setattr(engine, "_pipeline_output_allowed", gate)

    from src.core.models import CallSession

    call_id = "call-greeting-cleanup-gate"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "gated"
    await engine.session_store.upsert_call(session)

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.sleep(0.05)

    assert not tts.started.is_set()
    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
async def test_pipeline_runner_lifecycle(monkeypatch):
    # Minimal AppConfig, orchestrator presence is enough; we will stub its output
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"local_only": {}},
        "active_pipeline": "local_only",
        "audio_transport": "externalmedia",
    }
    app_config = AppConfig(**config_data)

    engine = Engine(app_config)
    engine.pipeline_orchestrator._started = True

    # Stub orchestrator to return a fake resolution with in-memory adapters
    resolution = _StubResolution()

    def fake_get_pipeline(call_id, pipeline_name=None):
        return resolution

    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", fake_get_pipeline)

    # Register a fake session
    from src.core.models import CallSession
    call_id = "call-abc"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "local_only"
    captured_registry = object()
    session.tool_runtime_registry = captured_registry
    await engine.session_store.upsert_call(session)

    # Start pipeline runner explicitly
    await engine._ensure_pipeline_runner(session, forced=True)

    assert call_id in engine._pipeline_tasks
    assert call_id in engine._pipeline_queues
    for _ in range(20):
        if getattr(resolution.llm_adapter, "_call_tool_registry", None) is not None:
            break
        await asyncio.sleep(0.01)
    assert resolution.llm_adapter.tool_registry_or(None) is captured_registry

    # Feed some audio and then cleanup
    q = engine._pipeline_queues[call_id]
    await q.put(b"\x00\x00" * 512)  # short chunk; runner will batch and continue

    await engine._cleanup_call(call_id)

    # Runner should be cancelled and queues/flags cleared
    assert call_id not in engine._pipeline_tasks
    assert call_id not in engine._pipeline_queues
    assert call_id not in engine._pipeline_forced


@pytest.mark.asyncio
async def test_pipeline_tool_only_turn_keeps_persisted_history_transcript_only(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"tool_only": {}},
        "active_pipeline": "tool_only",
        "audio_transport": "audiosocket",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _ToolOnlyThenTextLLM()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
        tts_adapter=_SilentTTS(),
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        engine.transport_orchestrator,
        "get_context_config",
        lambda *args, **kwargs: SimpleNamespace(
            prompt=None,
            greeting=None,
            tools=["transcript_tool"],
            in_call_http_tools={},
            disable_global_in_call_tools=[],
        ),
    )
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)

    from src.core.models import CallSession

    call_id = "call-tool-only-history"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "tool_only"
    session.context_name = "tool-context"
    session.allowed_tools = ["transcript_tool"]
    registry = ToolRegistry.isolated()
    registry.register_instance(_TranscriptTool())
    session.tool_runtime_registry = registry
    await engine.session_store.upsert_call(session)

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("please run the tool")
    await asyncio.wait_for(llm.completed.wait(), timeout=2)

    assert [entry["role"] for entry in session.conversation_history] == ["user", "assistant"]
    assert session.conversation_history[0]["content"] == "please run the tool"
    assert session.conversation_history[1]["content"] == "Done"
    assert all(entry.get("content") != "(tool execution)" for entry in session.conversation_history)
    assert all("tool_calls" not in entry and "tool_call_id" not in entry for entry in session.conversation_history)
    assert session.tool_calls[0]["tool_call_id"] == "pipeline-tool-1"

    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["record", "canonicalize"])
async def test_disallowed_follow_up_tool_stays_blocked_when_guardrail_bookkeeping_fails(
    monkeypatch,
    failure_mode,
):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"guarded": {}},
        "active_pipeline": "guarded",
        "audio_transport": "audiosocket",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _AllowedThenDisallowedToolLLM()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
        tts_adapter=_SilentTTS(),
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )
    monkeypatch.setattr(
        engine.transport_orchestrator,
        "get_context_config",
        lambda *args, **kwargs: SimpleNamespace(
            prompt=None,
            greeting=None,
            tools=["transcript_tool"],
            in_call_http_tools={},
            disable_global_in_call_tools=[],
        ),
    )
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)

    from src.core.models import CallSession

    call_id = f"call-follow-up-guard-{failure_mode}"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "guarded"
    session.context_name = "tool-context"
    session.allowed_tools = ["transcript_tool"]
    registry = ToolRegistry.isolated()
    registry.register_instance(_TranscriptTool())
    rejected_tool = _ExecutionProbeTool()
    registry.register_instance(rejected_tool)
    session.tool_runtime_registry = registry
    await engine.session_store.upsert_call(session)

    failure_seen = asyncio.Event()

    async def record_tool_result(**kwargs):
        if kwargs.get("result", {}).get("status") == "blocked":
            failure_seen.set()
            if failure_mode == "record":
                raise RuntimeError("audit recording unavailable")
        return None

    monkeypatch.setattr("src.engine.record_in_call_tool_result", record_tool_result)

    if failure_mode == "canonicalize":
        canonicalize = registry.canonicalize_tool_name

        def fail_for_rejected_tool(name):
            if name == "disallowed_tool":
                failure_seen.set()
                raise RuntimeError("canonicalization unavailable")
            return canonicalize(name)

        monkeypatch.setattr(registry, "canonicalize_tool_name", fail_for_rejected_tool)

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("run the allowed tool")
    await asyncio.wait_for(failure_seen.wait(), timeout=2)
    await asyncio.sleep(0.05)

    executed = rejected_tool.executed.is_set()
    await engine._cleanup_call(call_id)
    assert not executed


@pytest.mark.asyncio
async def test_cleanup_cancels_inflight_pipeline_turn_before_bridge_teardown(monkeypatch):
    """A late LLM result must not create playback after call cleanup starts."""
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _BlockingLLM()
    tts = _RecordingTTS()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
        tts_adapter=tts,
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )

    from src.core.models import CallSession

    call_id = "call-cleanup-inflight-llm"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    session.bridge_id = "bridge-cleanup-race"
    await engine.session_store.upsert_call(session)
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)
    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("goodbye this is final")
    await asyncio.wait_for(llm.started.wait(), timeout=2)

    async def release_llm_during_bridge_teardown(_bridge_id):
        llm.release.set()
        await asyncio.sleep(0.05)
        return True

    engine.ari_client.destroy_bridge = AsyncMock(side_effect=release_llm_during_bridge_teardown)
    engine.streaming_playback_manager.start_streaming_playback = AsyncMock(
        return_value="late-stream"
    )

    await engine._cleanup_call(call_id)

    assert llm.cancelled.is_set()
    assert not tts.started.is_set()
    engine.streaming_playback_manager.start_streaming_playback.assert_not_awaited()
    assert call_id not in engine._pipeline_tasks


@pytest.mark.asyncio
async def test_cleanup_suppresses_output_from_cancellation_resistant_llm(monkeypatch):
    """A provider result that survives cancellation must still fail closed."""
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _CancellationResistantLLM()
    tts = _RecordingTTS()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
        tts_adapter=tts,
    )
    monkeypatch.setattr(
        engine.pipeline_orchestrator,
        "get_pipeline",
        lambda *args, **kwargs: resolution,
    )

    from src.core.models import CallSession

    call_id = "call-cleanup-resistant-llm"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine.streaming_playback_manager.start_streaming_playback = AsyncMock(
        return_value="late-stream"
    )

    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    await stt.results.put("explain the project in detail")
    await asyncio.wait_for(llm.started.wait(), timeout=2)

    cleanup_task = asyncio.create_task(engine._cleanup_call(call_id))
    for _ in range(100):
        if session.cleanup_in_progress:
            break
        await asyncio.sleep(0.01)
    assert session.cleanup_in_progress is True
    # Return the provider result only after cleanup has acquired ownership.
    # Whether cancellation has propagated yet is intentionally irrelevant.
    llm.release.set()
    await asyncio.wait_for(cleanup_task, timeout=3)

    assert not tts.started.is_set()
    engine.streaming_playback_manager.start_streaming_playback.assert_not_awaited()
    assert call_id not in engine._pipeline_tasks


@pytest.mark.asyncio
async def test_pipeline_runner_uses_canonical_streaming_stt_audio_contract(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "externalmedia",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _StreamingStubSTT()
    configured_options = {
        "streaming": True,
        "chunk_ms": 80,
        "stream_format": "pcm16_8k",
        "sample_rate": 8000,
        "encoding": "mulaw",
    }
    resolution = _StubResolution(stt_adapter=stt, stt_options=configured_options)
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *args, **kwargs: resolution)

    from src.core.models import CallSession
    call_id = "call-streaming-format"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    await engine._ensure_pipeline_runner(session, forced=True)

    await asyncio.wait_for(stt.started.wait(), timeout=2)
    assert stt.open_options["stream_format"] == "pcm16_16k"
    assert stt.open_options["sample_rate"] == 16000
    assert stt.open_options["encoding"] == "linear16"
    assert stt.start_options == stt.open_options
    assert stt.start_format == (16000, "pcm16_16k")
    assert configured_options["stream_format"] == "pcm16_8k"  # Stored config was not mutated.

    await engine._pipeline_queues[call_id].put(b"\x00\x00" * 1280)  # 80 ms at 16 kHz PCM16.
    await asyncio.wait_for(stt.audio_sent.wait(), timeout=2)
    assert stt.sent[0][1] == "pcm16_16k"
    assert len(stt.sent[0][0]) == 2560

    await engine._cleanup_call(call_id)


@pytest.mark.asyncio
async def test_pipeline_dialog_consumer_restarts_after_unexpected_exit(monkeypatch):
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "externalmedia",
    }
    engine = Engine(AppConfig(**config_data))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _RecordingLLM()
    resolution = _StubResolution(
        stt_adapter=stt,
        stt_options={"streaming": True, "chunk_ms": 80},
        llm_adapter=llm,
    )
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *args, **kwargs: resolution)

    activity_calls = 0

    async def fail_first_activity(*_args, **_kwargs):
        nonlocal activity_calls
        activity_calls += 1
        if activity_calls == 1:
            raise RuntimeError("transient dialog failure")

    monkeypatch.setattr(engine, "_no_input_note_activity", fail_first_activity)

    from src.core.models import CallSession
    call_id = "call-dialog-restart"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    await engine.session_store.upsert_call(session)
    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)

    await stt.results.put("first turn crashes consumer")
    await stt.results.put("second turn survives")
    await asyncio.wait_for(llm.called.wait(), timeout=2)

    assert llm.transcripts == ["second turn survives"]
    await engine._cleanup_call(call_id)


class _ControlledOverlapLLM(LLMComponent):
    supports_streaming = True

    def __init__(self, mode="text"):
        self.mode = mode
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.serial_done = asyncio.Event()
        self.serial_calls = 0
        self.next_token_started = asyncio.Event()
        self.tail_release = asyncio.Event()

    async def generate(self, call_id, transcript, context, options):
        self.serial_calls += 1
        self.serial_done.set()
        return ""

    async def generate_stream(self, call_id, transcript, context, options):
        self.started.set()
        try:
            await self.release.wait()
            if self.mode == "failure":
                raise RuntimeError("synthetic model failure")
            if self.mode in ("text", "tail"):
                yield "Hello. "
            if self.mode == "tail":
                self.next_token_started.set()
                await self.tail_release.wait()
                yield "Done. "
        finally:
            self.closed.set()


class _ControlledOverlapTTS(TTSComponent):
    def __init__(self, chunks=(b"first-real-audio", b"second-real-audio")):
        self.chunks = chunks
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def synthesize(self, call_id, text, options):
        self.started.set()
        try:
            await self.release.wait()
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed.set()


class _OverlapPlaybackProbe:
    """Fake media transport using the real coordinator/session gating contract."""

    def __init__(self, engine, session):
        self.engine = engine
        self.session = session
        self.stream_id = "owned-overlap-stream"
        self.active = False
        self.starts = []
        self.stops = []
        self.drained = []
        self.eos = asyncio.Event()
        self._consumer = None

    def is_stream_active(self, call_id, stream_id=None):
        return (
            call_id == self.session.call_id
            and self.active
            and (stream_id is None or stream_id == self.stream_id)
        )

    async def start_streaming_playback(self, call_id, queue, **kwargs):
        self.starts.append((call_id, list(queue._queue), kwargs))
        self.active = True
        await self.engine.conversation_coordinator.on_tts_start(call_id, self.stream_id)

        async def consume():
            while True:
                chunk = await queue.get()
                if chunk is None:
                    self.eos.set()
                    return
                self.drained.append(chunk)

        self._consumer = asyncio.create_task(consume())
        return self.stream_id

    async def stop_streaming_playback(self, call_id, *, drain=False):
        self.stops.append((call_id, self.stream_id))
        self.active = False
        await self.engine.conversation_coordinator.on_tts_end(call_id, self.stream_id)
        if self._consumer and not self._consumer.done():
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
        return True


async def _start_controlled_overlap(monkeypatch, *, mode="text", chunks=None):
    from src.core.models import CallSession

    engine = Engine(AppConfig(**{
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1", "port": 8088, "username": "u",
            "password": "p", "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "", "prompt": "Synthetic test", "model": "test-model"},
        "pipelines": {"streaming": {}},
        "active_pipeline": "streaming",
        "audio_transport": "audiosocket",
        "downstream_mode": "stream",
        "streaming": {"pipeline_streaming_overlap": True},
    }))
    engine.pipeline_orchestrator._started = True
    stt = _ResultStreamingStubSTT()
    llm = _ControlledOverlapLLM(mode)
    tts = _ControlledOverlapTTS() if chunks is None else _ControlledOverlapTTS(chunks)
    resolution = _StubResolution(
        stt_adapter=stt, stt_options={"streaming": True, "chunk_ms": 160},
        llm_adapter=llm, tts_adapter=tts,
    )
    resolution.llm_options["aggregation_timeout_sec"] = 0.02
    monkeypatch.setattr(engine.pipeline_orchestrator, "get_pipeline", lambda *a, **k: resolution)
    engine.ari_client.set_channel_var = AsyncMock(return_value=True)
    call_id = "call-controlled-overlap-" + uuid.uuid4().hex
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.pipeline_name = "streaming"
    session.audio_capture_enabled = True  # An established call after greeting/bootstrap.
    await engine.session_store.upsert_call(session)
    manager = _OverlapPlaybackProbe(engine, session)
    engine.streaming_playback_manager = manager
    await engine._ensure_pipeline_runner(session, forced=True)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    return engine, session, stt, llm, tts, manager


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_stage", ["model", "tts"])
async def test_overlap_keeps_capture_open_until_real_audio_buffer(monkeypatch, waiting_stage):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch)
    try:
        await stt.results.put("please answer this question")
        await asyncio.wait_for(llm.started.wait(), timeout=2)
        if waiting_stage == "tts":
            llm.release.set()
            await asyncio.wait_for(tts.started.wait(), timeout=2)
        assert manager.starts == []
        assert not session.tts_playing
        assert session.audio_capture_enabled
        # Exercise the actual streaming STT sender while the model/TTS is pending.
        await engine._pipeline_queues[session.call_id].put(b"\x00\x00" * 2560)
        await asyncio.wait_for(stt.audio_sent.wait(), timeout=2)
        llm.release.set()
        tts.release.set()
        await asyncio.wait_for(manager.eos.wait(), timeout=2)
        assert len(manager.starts) == 1
        assert manager.starts[0][1] == [b"first-real-audio"]
        assert manager.starts[0][2]["playback_type"] == "pipeline-tts"
        assert manager.drained == [b"first-real-audio", b"second-real-audio"]
        assert session.tts_playing
        assert not session.audio_capture_enabled
    finally:
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "failure"])
async def test_overlap_empty_or_failed_model_never_starts_phantom_tts(monkeypatch, mode):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch, mode=mode)
    try:
        llm.release.set()
        await stt.results.put("please answer this question")
        await asyncio.wait_for(llm.serial_done.wait(), timeout=2)
        assert manager.starts == []
        assert not tts.started.is_set()
        assert session.audio_capture_enabled and not session.tts_playing
        assert llm.closed.is_set()
        assert llm.serial_calls == 1  # Existing serial fallback, not a new retry policy.
    finally:
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
async def test_overlap_empty_tts_never_closes_capture(monkeypatch):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch, chunks=(b"",))
    try:
        llm.release.set()
        tts.release.set()
        await stt.results.put("please answer this question")
        await asyncio.wait_for(llm.closed.wait(), timeout=2)
        assert tts.closed.is_set()
        assert manager.starts == []
        assert session.audio_capture_enabled and not session.tts_playing
    finally:
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_stage", ["model", "tts"])
@pytest.mark.parametrize("aggregation_flush", [False, True])
async def test_overlap_teardown_settles_model_and_tts_including_flush(
    monkeypatch, waiting_stage, aggregation_flush,
):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch)
    try:
        await stt.results.put("hi" if aggregation_flush else "please answer this question")
        await asyncio.wait_for(llm.started.wait(), timeout=2)
        if waiting_stage == "tts":
            llm.release.set()
            await asyncio.wait_for(tts.started.wait(), timeout=2)
        await engine._cleanup_call(session.call_id)
        assert llm.closed.is_set()
        if waiting_stage == "tts":
            assert tts.closed.is_set()
        assert manager.starts == []
        assert session.call_id not in engine._pipeline_tasks
        llm.release.set()
        tts.release.set()
        await asyncio.sleep(0)
        assert manager.starts == []
    finally:
        llm.release.set()
        tts.release.set()
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
async def test_overlap_interruption_closes_suspended_generators_not_replacement_stream(monkeypatch):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch)
    original_put = engine._put_pipeline_stream_chunk

    async def replace_stream(call_id, stream_id, queue, chunk, **kwargs):
        manager.stream_id = "replacement-stream"
        await original_put(call_id, stream_id, queue, chunk, **kwargs)

    monkeypatch.setattr(engine, "_put_pipeline_stream_chunk", replace_stream)
    try:
        llm.release.set()
        tts.release.set()
        await stt.results.put("please answer this question")
        await asyncio.wait_for(llm.closed.wait(), timeout=2)
        assert tts.closed.is_set()
        assert manager.stops == []
        assert manager.is_stream_active(session.call_id, "replacement-stream")
        assert llm.serial_calls == 0
    finally:
        await engine._cleanup_call(session.call_id)


class _CleanupFailureIterator:
    def __init__(self, stream, component, observed, error):
        self.stream = stream
        self.component = component
        self.observed = observed
        self.error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.stream.__anext__()

    async def aclose(self):
        self.observed.append(self.component)
        await self.stream.aclose()
        if self.error:
            raise self.error("synthetic close failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("close_error", [RuntimeError, asyncio.CancelledError])
async def test_overlap_cleanup_failure_still_closes_llm_and_restores_provider(monkeypatch, close_error):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch)
    old_provider = session.provider_name
    observed = []
    original_llm = llm.generate_stream
    original_tts = tts.synthesize
    monkeypatch.setattr(llm, "generate_stream", lambda *a, **k: _CleanupFailureIterator(
        original_llm(*a, **k), "llm", observed, None,
    ))
    monkeypatch.setattr(tts, "synthesize", lambda *a, **k: _CleanupFailureIterator(
        original_tts(*a, **k), "tts", observed, close_error,
    ))
    try:
        llm.release.set()
        await stt.results.put("please answer this question")
        await asyncio.wait_for(tts.started.wait(), timeout=2)
        await engine._cleanup_call(session.call_id)
        assert observed == ["tts", "llm"]
        assert llm.closed.is_set() and tts.closed.is_set()
        assert session.provider_name == old_provider
        assert llm.serial_calls == 0
        assert manager.starts == []
        assert session.call_id not in engine._pipeline_tasks
    finally:
        tts.release.set()
        await engine._cleanup_call(session.call_id)


class _FailingOverlapTTS(_ControlledOverlapTTS):
    def __init__(self, after_audio):
        super().__init__()
        self.after_audio = after_audio

    async def synthesize(self, call_id, text, options):
        self.started.set()
        try:
            await self.release.wait()
            if self.after_audio:
                yield b"partial-real-audio"
            raise RuntimeError("synthetic TTS failure")
        finally:
            self.closed.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("after_audio", [False, True])
async def test_overlap_tts_failure_never_regenerates_after_partial_audio(monkeypatch, after_audio):
    engine, session, stt, llm, _, manager = await _start_controlled_overlap(monkeypatch)
    old_provider = session.provider_name
    tts = _FailingOverlapTTS(after_audio)
    resolution = engine.pipeline_orchestrator.get_pipeline(session.call_id)
    resolution.tts_adapter = tts
    try:
        llm.release.set()
        tts.release.set()
        await stt.results.put("please answer this question")
        await asyncio.wait_for(llm.closed.wait(), timeout=2)
        if not after_audio:
            await asyncio.wait_for(llm.serial_done.wait(), timeout=2)
        assert llm.serial_calls == (0 if after_audio else 1)
        assert tts.closed.is_set()
        assert len(manager.starts) == (1 if after_audio else 0)
        assert not manager.active
        assert session.provider_name == old_provider
        assert session.audio_capture_enabled and not session.tts_playing
    finally:
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
async def test_overlap_teardown_settles_active_audio_and_pending_next_token(monkeypatch):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch, mode="tail")
    old_provider = session.provider_name
    try:
        llm.release.set()
        tts.release.set()
        await stt.results.put("please answer this question")
        await asyncio.wait_for(llm.next_token_started.wait(), timeout=2)
        assert manager.active
        assert not llm.closed.is_set()
        await engine._cleanup_call(session.call_id)
        assert llm.closed.is_set() and tts.closed.is_set()
        assert not manager.active
        assert session.provider_name == old_provider
        assert all(call_id == session.call_id for call_id, _ in manager.stops)
        assert llm.serial_calls == 0
    finally:
        llm.tail_release.set()
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
async def test_overlap_teardown_settles_already_failed_flush_task(monkeypatch):
    engine, session, stt, llm, tts, manager = await _start_controlled_overlap(monkeypatch)
    old_provider = session.provider_name
    failed = asyncio.Event()
    original_upsert = engine.session_store.upsert_call

    async def fail_history_upsert(call):
        if call.conversation_history and not call.cleanup_in_progress:
            failed.set()
            raise RuntimeError("synthetic failed aggregation task")
        return await original_upsert(call)

    monkeypatch.setattr(engine.session_store, "upsert_call", fail_history_upsert)
    try:
        llm.release.set()
        tts.release.set()
        await stt.results.put("hi")
        await asyncio.wait_for(failed.wait(), timeout=2)
        await asyncio.sleep(0)
        await engine._cleanup_call(session.call_id)
        assert llm.closed.is_set() and tts.closed.is_set()
        assert session.provider_name == old_provider
        assert session.call_id not in engine._pipeline_tasks
        assert not manager.active
    finally:
        await engine._cleanup_call(session.call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["aimee_main", "aimee"])
async def test_initial_350ms_preroll_survives_real_processing_and_frame_output(
    monkeypatch, tmp_path, agent,
):
    from tests import test_local_caller_ingress as ingress
    from src.core.streaming_playback_manager import StreamingPlaybackManager, _JITTER_SENTINEL

    call_id = "preroll-" + uuid.uuid4().hex
    monkeypatch.setattr(ingress, "LOCAL_ID", call_id)
    original_event = ingress.local_event

    def event_for_test(*args, **kwargs):
        kwargs.setdefault("channel_id", call_id)
        return original_event(*args, **kwargs)

    monkeypatch.setattr(ingress, "local_event", event_for_test)
    engine, pipeline, playback, waiting, ready = ingress.timing_engine(
        monkeypatch, tmp_path, agent=agent,
    )
    try:
        await engine._handle_stasis_start(ingress.local_event(channel_id=call_id, agent=agent))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        queue = playback.queues[call_id]
        assert queue.empty()
        session = await engine.session_store.get_by_call_id(call_id)
        session.media_rx_confirmed = True
        await asyncio.wait_for(ready.wait(), timeout=1)
        chunks = []
        while not queue.empty():
            chunks.append(queue.get_nowait())
        assert chunks[:3] == [b"\xff" * 2800, b"first-spoken-chunk", b"second-spoken-chunk"]

        manager = StreamingPlaybackManager(
            session_store=engine.session_store, ari_client=engine.ari_client,
            streaming_config=engine.config.streaming.model_dump(), audio_transport="audiosocket",
        )
        manager.audiosocket_format = "ulaw"
        stream_id = "preroll-output"
        manager.active_streams[call_id] = {
            "stream_id": stream_id, "target_format": "ulaw", "target_sample_rate": 8000,
            "source_encoding": "mulaw", "source_sample_rate": 8000,
            "producer_closed": True,
        }
        emitted = []

        async def send_audio(call, stream, frame, **kwargs):
            assert call == call_id and stream == stream_id
            emitted.append(frame)
            return True

        manager._send_audio_chunk = send_audio
        jitter = asyncio.Queue()
        for chunk in chunks:
            jitter.put_nowait(_JITTER_SENTINEL if chunk is None else chunk)
        for _ in range(64):
            result = await manager._drain_next_frame(call_id, stream_id, jitter)
            if result == "finished":
                break
        else:
            pytest.fail("Native frame drain did not finish")
        output = b"".join(emitted)
        assert output[:2800] == b"\xff" * 2800
        assert any(byte != 0xff for byte in output[2800:])
        assert len(output[:2800]) / 8000 == 0.350
        assert manager.normalizer_enabled  # Includes the existing leading-silence trim branch.
        assert engine._wait_for_initial_media.await_count == 1
        await engine._stream_pipeline_tts_text(call_id, session, pipeline, "next reply")
        assert playback.drained == [b"first-spoken-chunk", b"second-spoken-chunk", None]
        assert engine._wait_for_initial_media.await_count == 1  # Initial speech only.
    finally:
        await engine._cleanup_call(call_id)
