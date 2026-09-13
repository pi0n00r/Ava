import asyncio
import socket
import struct
import time

import pytest

from src.rtp_server import RTPServer, RTPSession


def _build_rtp_packet(*, ssrc: int, seq: int = 1, ts: int = 1, payload: bytes) -> bytes:
    # Minimal RTP header: V=2, PT=0 (ignored by receiver), no extensions.
    header = struct.pack("!BBHII", 0x80, 0, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
    return header + payload


@pytest.mark.asyncio
async def test_rtp_server_engine_callback_includes_call_id():
    captured = []

    async def cb(call_id: str, ssrc: int, pcm: bytes) -> None:
        captured.append((call_id, ssrc, pcm))

    server = RTPServer(
        host="127.0.0.1",
        port=18080,
        engine_callback=cb,
        codec="slin16",
        sample_rate=8000,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        session = RTPSession(
            call_id="call-1",
            local_port=9999,
            socket=sock,
            created_at=time.time(),
            last_packet_at=time.time(),
        )
        payload = b"\x00\x00" * 160  # 20ms PCM16 @ 8kHz
        await server._handle_inbound_packet(session, 1, 1, payload, 1234)  # type: ignore[attr-defined]

        assert captured, "Expected engine callback to be invoked"
        call_id, ssrc, pcm = captured[0]
        assert call_id == "call-1"
        assert ssrc == 1234
        assert isinstance(pcm, (bytes, bytearray)) and len(pcm) > 0
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_rtp_server_lock_remote_endpoint_drops_mismatched_source_port():
    captured = []

    async def cb(call_id: str, ssrc: int, pcm: bytes) -> None:
        captured.append((call_id, ssrc, pcm))

    server = RTPServer(
        host="127.0.0.1",
        port=18080,
        engine_callback=cb,
        codec="slin16",
        sample_rate=8000,
        lock_remote_endpoint=True,
    )
    await server.start()

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    recv_sock.setblocking(False)
    port = recv_sock.getsockname()[1]

    session = RTPSession(
        call_id="call-locked",
        local_port=port,
        socket=recv_sock,
        created_at=time.time(),
        last_packet_at=time.time(),
    )
    server.sessions[session.call_id] = session

    task = asyncio.create_task(server._rtp_receiver_loop(session))  # type: ignore[attr-defined]

    sender1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender1.bind(("127.0.0.1", 0))
    sender2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender2.bind(("127.0.0.1", 0))

    try:
        pkt = _build_rtp_packet(ssrc=111, payload=b"\x00\x00" * 160)

        sender1.sendto(pkt, ("127.0.0.1", port))
        await asyncio.sleep(0.05)
        assert session.remote_port == sender1.getsockname()[1]
        assert len(captured) == 1

        sender2.sendto(pkt, ("127.0.0.1", port))
        await asyncio.sleep(0.05)
        assert session.remote_port == sender1.getsockname()[1], "Remote endpoint should remain locked"
        assert len(captured) == 1, "Second packet should be dropped under lock"
    finally:
        sender1.close()
        sender2.close()
        server.sessions.pop(session.call_id, None)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        recv_sock.close()
        await server.stop()


@pytest.mark.asyncio
async def test_rtp_server_allowed_remote_hosts_rejects_first_packet():
    captured = []

    async def cb(call_id: str, ssrc: int, pcm: bytes) -> None:
        captured.append((call_id, ssrc, pcm))

    server = RTPServer(
        host="127.0.0.1",
        port=18080,
        engine_callback=cb,
        codec="slin16",
        sample_rate=8000,
        allowed_remote_hosts={"203.0.113.1"},
        lock_remote_endpoint=True,
    )
    await server.start()

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    recv_sock.setblocking(False)
    port = recv_sock.getsockname()[1]

    session = RTPSession(
        call_id="call-allowlist",
        local_port=port,
        socket=recv_sock,
        created_at=time.time(),
        last_packet_at=time.time(),
    )
    server.sessions[session.call_id] = session
    task = asyncio.create_task(server._rtp_receiver_loop(session))  # type: ignore[attr-defined]

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.bind(("127.0.0.1", 0))
    try:
        pkt = _build_rtp_packet(ssrc=222, payload=b"\x00\x00" * 160)
        sender.sendto(pkt, ("127.0.0.1", port))
        await asyncio.sleep(0.05)

        assert session.remote_host is None
        assert captured == []
    finally:
        sender.close()
        server.sessions.pop(session.call_id, None)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        recv_sock.close()
        await server.stop()
