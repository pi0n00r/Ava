import asyncio
import socket

import pytest
from aiohttp import web


@pytest.mark.asyncio
async def test_aiohttp_localhost_accepts_both_loopback_families_on_fixed_port():
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()

    app = web.Application()
    app.router.add_get("/health", lambda _request: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()
    try:
        families = {sock.family for sock in site._server.sockets}
        assert socket.AF_INET in families
        assert socket.AF_INET6 in families
        for host in ("127.0.0.1", "::1"):
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            assert b"200 OK" in await reader.readuntil(b"\r\n\r\n")
            writer.close()
            await writer.wait_closed()
    finally:
        await runner.cleanup()
