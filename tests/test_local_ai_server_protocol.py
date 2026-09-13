#!/usr/bin/env python3
"""
Protocol-level tests for Local AI Server against the current contract in
local_ai_server/main.py and docs/local-ai-server/PROTOCOL.md.

These tests assume the server is reachable at ws://127.0.0.1:8765.
"""

import asyncio
import base64
import json
import os
import sys
import logging
from typing import Optional

import websockets
import pytest
import socket

WS_URL = os.getenv("LOCAL_WS_URL", "ws://127.0.0.1:8765")


def _server_available(url: str) -> bool:
    try:
        host, port = url.replace("ws://", "").replace("wss://", "").split(":")
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except Exception:
        return False

# Mark as integration: requires a running local-ai-server WebSocket
pytestmark = pytest.mark.skipif(
    not _server_available(WS_URL),
    reason="Requires local AI server at 127.0.0.1:8765. Start local_ai_server to enable.",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_tts_roundtrip() -> bool:
    async with websockets.connect(WS_URL, max_size=None) as ws:
        req = {
            "type": "tts_request",
            "text": "Hello from protocol test.",
            "call_id": "test-call",
            "request_id": "t1",
        }
        await ws.send(json.dumps(req))
        meta = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
        assert meta["type"] == "tts_audio"
        assert meta["encoding"] == "mulaw"
        # Next message should be binary μ-law bytes
        pcm = await asyncio.wait_for(ws.recv(), timeout=10.0)
        assert isinstance(pcm, (bytes, bytearray))
        logger.info("Received TTS audio bytes: %s", len(pcm))
        return len(pcm) > 0


async def test_stt_binary_flow() -> bool:
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "set_mode", "mode": "stt", "call_id": "demo"}))
        # mode_ready is optional; server may not echo. Continue regardless.
        try:
            _ = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception:
            pass
        # Send 1s of silence PCM16@16k
        pcm_silence = b"\x00\x00" * 16000
        await ws.send(pcm_silence)
        # Expect one or more stt_result payloads; final may be empty depending on silence
        for _ in range(3):
            msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            if isinstance(msg, (bytes, bytearray)):
                # ignore any unexpected binary frames
                continue
            evt = json.loads(msg)
            if evt.get("type") == "stt_result" and evt.get("is_final"):
                logger.info("Final STT text: '%s'", evt.get("text", ""))
                return True
        return False


async def test_full_audio_frame() -> bool:
    async with websockets.connect(WS_URL, max_size=None) as ws:
        # Send JSON audio frame (silence) in full mode
        pcm = b"\x00\x00" * 16000
        req = {
            "type": "audio",
            "mode": "full",
            "rate": 16000,
            "call_id": "full-test",
            "request_id": "r1",
            "data": base64.b64encode(pcm).decode("utf-8"),
        }
        await ws.send(json.dumps(req))
        # Expect stt_result partials/final then llm_response then tts_audio + binary
        saw_final = False
        saw_llm = False
        saw_tts_meta = False
        for _ in range(10):
            msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if isinstance(msg, (bytes, bytearray)):
                if saw_tts_meta:
                    logger.info("Received TTS audio bytes: %s", len(msg))
                    return True
                continue
            evt = json.loads(msg)
            if evt.get("type") == "stt_result" and evt.get("is_final"):
                saw_final = True
            elif evt.get("type") == "llm_response":
                saw_llm = True
            elif evt.get("type") == "tts_audio":
                saw_tts_meta = True
        return saw_final and saw_llm and saw_tts_meta


async def main() -> None:
    ok1 = await test_tts_roundtrip()
    ok2 = await test_stt_binary_flow()
    ok3 = await test_full_audio_frame()
    total = sum([ok1, ok2, ok3])
    print(f"Local AI Server protocol tests passed: {total}/3")
    sys.exit(0 if total == 3 else 1)


if __name__ == "__main__":
    asyncio.run(main())
