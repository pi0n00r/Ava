import asyncio
import hashlib
import time
import wave
import random
from itertools import islice
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


def test_default_wait_frames_are_byte_identical_to_retained_loop():
    manager, _, _ = _manager()
    asset = manager._load_caller_wait_ambience()
    frames = list(islice(manager._caller_wait_ambience_frames(asset), 600))
    assert b"".join(frames) == asset * 3


def test_varied_wait_frames_have_bounded_smoothed_bursts_gain_and_rests():
    manager, _, _ = _manager()
    manager.attack_ms = 20
    asset = (10000).to_bytes(2, "little", signed=True) * 32000
    frames = list(islice(manager._caller_wait_ambience_frames(asset, varied=True, rng=random.Random(7)), 1200))
    assert all(len(frame) == 320 for frame in frames)
    runs = []
    for frame in frames:
        silent = not any(frame)
        if runs and runs[-1][0] == silent:
            runs[-1][1].append(frame)
        else:
            runs.append([silent, [frame]])
    bursts = [run for silent, run in runs[:-1] if not silent]
    rests = [run for silent, run in runs[:-1] if silent]
    assert len(bursts) > 5
    assert len({len(run) for run in bursts}) > 1
    assert len({run[1] for run in bursts}) > 1
    assert all(40 <= len(run) <= 110 for run in bursts)
    assert all(15 <= len(run) <= 55 for run in rests)
    for run in bursts:
        assert run[0][:2] == b"\x00\x00"
        assert run[-1][-2:] == b"\x00\x00"
        assert 5500 <= int.from_bytes(run[1][:2], "little", signed=True) <= 9000


def test_varied_wait_rng_and_cursor_are_call_local():
    manager, _, _ = _manager()
    asset = manager._load_caller_wait_ambience()
    first = manager._caller_wait_ambience_frames(asset, varied=True, rng=random.Random(19))
    interleaved = manager._caller_wait_ambience_frames(asset, varied=True, rng=random.Random(19))
    other = manager._caller_wait_ambience_frames(asset, varied=True, rng=random.Random(3))
    expected = [next(first) for _ in range(400)]
    actual = []
    for _ in range(400):
        next(other)
        actual.append(next(interleaved))
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("context,varied", [("aimee_main", True), ("aimee", False), (None, False)])
async def test_only_existing_main_model_wait_entry_selects_variation(context, varied):
    manager, _, session = _manager()
    session.context_name = context
    engine = Engine.__new__(Engine)
    engine.streaming_playback_manager = manager
    engine.session_store = manager.session_store
    native = manager.start_caller_wait_ambience
    seen = []
    async def start(call, **kwargs):
        seen.append(kwargs)
        return await native(call, **kwargs)
    manager.start_caller_wait_ambience = start
    owner = await engine._start_pipeline_model_wait("call", {"call_id_header_enabled": True, "session_user_from_call_id": True})
    assert owner is not None
    assert seen == ([{"varied": True}] if varied else [{}])
    await engine._stop_pipeline_model_wait("call", owner)
    assert owner.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("varied", [False, True])
async def test_wait_frames_pause_for_real_tts_and_resume_without_duplicate_owner(varied):
    manager, server, session = _manager()
    assert await manager.start_caller_wait_ambience("call", varied=varied)
    owner = manager._caller_wait_ambience_tasks["call"]
    await asyncio.sleep(0.08)
    manager.active_streams["call"] = {"stream_id": "real-tts"}
    await asyncio.sleep(0.06)
    count = len(server.frames)
    await asyncio.sleep(0.06)
    assert len(server.frames) == count
    assert not await manager.start_caller_wait_ambience("call", varied=varied)
    manager.active_streams.clear()
    await asyncio.sleep(0.08)
    assert len(server.frames) > count
    assert manager._caller_wait_ambience_tasks == {"call": owner}
    assert session.audio_capture_enabled
    assert manager.conversation_coordinator.on_tts_start.await_count == 0
    await manager.stop_caller_wait_ambience("call")
    assert owner.done()


@pytest.mark.asyncio
async def test_varied_wait_stop_cancel_and_two_call_isolation():
    manager, server, session = _manager()
    second = SimpleNamespace(audiosocket_conn_id="other", audio_capture_enabled=True)
    sessions = {"call": session, "other": second}
    manager.session_store.get_by_call_id = AsyncMock(side_effect=sessions.get)
    counts = {"conn": 0, "other": 0}
    native_send = server.send_audio
    async def send(connection, payload, **kwargs):
        counts[connection] += 1
        return await native_send(connection, payload, **kwargs)
    server.send_audio = send
    assert await manager.start_caller_wait_ambience("call", varied=True)
    assert await manager.start_caller_wait_ambience("other", varied=True)
    second_owner = manager._caller_wait_ambience_tasks["other"]
    await asyncio.sleep(0.08)
    await manager.stop_caller_wait_ambience("call")
    stopped = counts["conn"]
    continuing = counts["other"]
    await asyncio.sleep(0.06)
    assert counts["conn"] == stopped
    assert counts["other"] > continuing
    assert manager._caller_wait_ambience_tasks == {"other": second_owner}
    second_owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await manager.stop_caller_wait_ambience("other")
    assert second_owner.done()
    assert not manager._caller_wait_ambience_tasks
    assert not manager._caller_wait_ambience_stops
    assert session.audio_capture_enabled and second.audio_capture_enabled


@pytest.mark.asyncio
async def test_varied_wait_failed_transport_settles_without_retry_or_gate():
    manager, server, session = _manager()
    async def failed_send(*args, **kwargs):
        return False
    server.send_audio = failed_send
    assert await manager.start_caller_wait_ambience("call", varied=True)
    owner = manager._caller_wait_ambience_tasks["call"]
    await asyncio.wait_for(asyncio.shield(owner), 1)
    assert owner.done()
    assert await manager.stop_caller_wait_ambience("call")
    assert not manager._caller_wait_ambience_tasks
    assert not manager._caller_wait_ambience_stops
    assert not manager.active_streams
    assert session.audio_capture_enabled
    assert manager.conversation_coordinator.on_tts_start.await_count == 0


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
