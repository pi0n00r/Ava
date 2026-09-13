import asyncio
import hashlib
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.streaming_playback_manager import (
    StreamingPlaybackManager,
    _CALLER_WAIT_AMBIENCE_PATH,
    _CALLER_WAIT_AMBIENCE_SHA256,
)
from src.engine import Engine
from src.tools.http.in_call_lookup import create_in_call_http_tool


class _AudioSocketFake:
    def __init__(self) -> None:
        self.frames: list[tuple[float, bytes, str, int]] = []

    async def send_audio(
        self,
        _conn_id: str,
        payload: bytes,
        *,
        encoding: str,
        sample_rate: int,
    ) -> bool:
        self.frames.append((time.monotonic(), bytes(payload), encoding, sample_rate))
        return True


def _manager() -> tuple[StreamingPlaybackManager, _AudioSocketFake, SimpleNamespace]:
    session = SimpleNamespace(
        audiosocket_conn_id="conn",
        audio_capture_enabled=True,
    )
    store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=session))
    server = _AudioSocketFake()
    coordinator = SimpleNamespace(
        on_tts_start=AsyncMock(),
        on_tts_end=AsyncMock(),
        note_audio_during_tts=AsyncMock(),
    )
    manager = StreamingPlaybackManager(
        store,
        ari_client=SimpleNamespace(),
        conversation_coordinator=coordinator,
        streaming_config={"sample_rate": 8000, "chunk_size_ms": 20},
        audio_transport="audiosocket",
        audiosocket_server=server,
    )
    return manager, server, session


def test_receptionist_typing_asset_is_hash_bound_pcm16_cc0() -> None:
    asset = Path(_CALLER_WAIT_AMBIENCE_PATH)
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == _CALLER_WAIT_AMBIENCE_SHA256
    assert Path(f"{asset.with_suffix('')}.LICENSE").read_text().startswith(
        "SPDX-License-Identifier: CC0-1.0"
    )
    with wave.open(str(asset), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 8000
        assert source.getnframes() == 32000
    assert StreamingPlaybackManager._load_caller_wait_ambience()


def test_managed_in_call_http_tool_requires_explicit_ambience_opt_in() -> None:
    plain = create_in_call_http_tool("plain", {"url": "https://example.test"})
    opted_in = create_in_call_http_tool(
        "deposit",
        {"url": "https://example.test", "caller_wait_ambience": True},
    )
    assert plain.caller_wait_ambience is False
    assert opted_in.caller_wait_ambience is True


@pytest.mark.asyncio
async def test_audiosocket_wait_ambience_is_non_gating_singleton_and_stops() -> None:
    manager, server, session = _manager()

    assert await manager.start_caller_wait_ambience("call") is True
    assert await manager.start_caller_wait_ambience("call") is False
    await asyncio.sleep(0.18)
    assert await manager.stop_caller_wait_ambience("call") is True

    assert server.frames
    assert any(any(frame[1]) for frame in server.frames)
    assert all(len(frame[1]) == 320 for frame in server.frames)
    assert all(frame[2:] == ("slin", 8000) for frame in server.frames)
    assert manager.active_streams == {}
    assert session.audio_capture_enabled is True
    assert not manager.conversation_coordinator.on_tts_start.await_count
    assert not manager.conversation_coordinator.on_tts_end.await_count
    assert not manager.conversation_coordinator.note_audio_during_tts.await_count

    stopped_count = len(server.frames)
    await asyncio.sleep(0.06)
    assert len(server.frames) == stopped_count


class _DelayedTool:
    def __init__(self, *, opted_in: bool, fail: bool = False) -> None:
        self.caller_wait_ambience = opted_in
        self.fail = fail

    async def execute(self, _parameters, _context):
        await asyncio.sleep(0.16)
        if self.fail:
            raise RuntimeError("bounded fake failure")
        return {"status": "success", "_direct_response_text": "Done."}


@pytest.mark.asyncio
async def test_delayed_tool_owns_ambience_until_before_direct_response() -> None:
    manager, server, session = _manager()
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = manager

    result = await engine._execute_pipeline_tool_with_wait_ambience(
        "call", _DelayedTool(opted_in=True), {}, SimpleNamespace()
    )
    returned_at = time.monotonic()

    assert result["_direct_response_text"] == "Done."
    assert server.frames
    assert max(frame[0] for frame in server.frames) <= returned_at
    assert not manager._caller_wait_ambience_tasks
    assert not manager._caller_wait_ambience_stops

    frames_before_response = len(server.frames)
    engine.session_store = SimpleNamespace(upsert_call=AsyncMock())
    engine._pipeline_output_allowed = lambda *_args, **_kwargs: True
    engine._pipeline_tts_uses_streaming = lambda _pipeline: True

    async def _assert_ambience_stopped_before_response(*_args, **_kwargs) -> None:
        assert not manager._caller_wait_ambience_tasks
        assert not manager._caller_wait_ambience_stops
        assert len(server.frames) == frames_before_response

    engine._stream_pipeline_tts_text = _assert_ambience_stopped_before_response
    conversation_history = []
    assert await engine._maybe_speak_direct_pipeline_tool_result(
        "call",
        session,
        SimpleNamespace(),
        conversation_history,
        result,
        tool_name="deposit",
    ) is True


@pytest.mark.asyncio
async def test_wait_ambience_cleans_up_on_failure_and_cancellation() -> None:
    manager, server, _session = _manager()
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = manager

    with pytest.raises(RuntimeError, match="bounded fake failure"):
        await engine._execute_pipeline_tool_with_wait_ambience(
            "failure", _DelayedTool(opted_in=True, fail=True), {}, SimpleNamespace()
        )
    assert not manager._caller_wait_ambience_tasks

    task = asyncio.create_task(
        engine._execute_pipeline_tool_with_wait_ambience(
            "cancel", _DelayedTool(opted_in=True), {}, SimpleNamespace()
        )
    )
    await asyncio.sleep(0.06)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager._caller_wait_ambience_tasks
    stopped_count = len(server.frames)
    await asyncio.sleep(0.06)
    assert len(server.frames) == stopped_count


@pytest.mark.asyncio
async def test_unconfigured_tool_does_not_touch_audio_output() -> None:
    manager, server, _session = _manager()
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = manager

    result = await engine._execute_pipeline_tool_with_wait_ambience(
        "plain", _DelayedTool(opted_in=False), {}, SimpleNamespace()
    )

    assert result["status"] == "success"
    assert server.frames == []
    assert not manager._caller_wait_ambience_tasks
