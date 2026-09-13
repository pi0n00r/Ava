import asyncio
import socket

import pytest

from src.audio.audiosocket_server import AudioSocketServer


async def _accept_uuid(_call_id: str, _connection_id: str) -> bool:
    return True


async def _accept_audio(_call_id: str, _frame) -> None:
    return None


@pytest.mark.asyncio
async def test_empty_host_accepts_ipv4_and_ipv6_on_one_fixed_port():
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()

    server = AudioSocketServer(
        "", port, on_uuid=_accept_uuid, on_audio=_accept_audio
    )
    await server.start()
    try:
        families = {sock.family for sock in server._server.sockets}
        assert socket.AF_INET in families
        assert socket.AF_INET6 in families

        for host in ("127.0.0.1", "::1"):
            _reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()
