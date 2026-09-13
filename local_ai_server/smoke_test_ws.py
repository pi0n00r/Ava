from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict, Optional

import websockets

from protocol_contract import validate_payload


async def _recv_json(ws, timeout: float = 5.0) -> Dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raise RuntimeError(f"Expected JSON message, got binary payload ({len(raw)} bytes)")
    data = json.loads(raw)
    validate_payload(data)
    return data


async def _send_json(ws, payload: Dict[str, Any]) -> None:
    validate_payload(payload)
    await ws.send(json.dumps(payload))


async def run(url: str, auth_token: Optional[str], *, verbose: bool) -> None:
    async with websockets.connect(url, open_timeout=5, max_size=None) as ws:
        if verbose:
            print(f"connected: {url}")

        async def ensure_authed() -> None:
            nonlocal auth_token
            if not auth_token:
                auth_token = (os.getenv("LOCAL_WS_AUTH_TOKEN") or "").strip() or None
            if not auth_token:
                raise RuntimeError(
                    "Server requires auth but no token provided. Use --auth-token or set LOCAL_WS_AUTH_TOKEN."
                )
            await _send_json(ws, {"type": "auth", "auth_token": auth_token})
            resp = await _recv_json(ws)
            if resp.get("type") != "auth_response" or resp.get("status") != "ok":
                raise RuntimeError(f"Auth failed: {resp}")
            if verbose:
                print("auth: ok")

        # 1) status
        await _send_json(ws, {"type": "status"})
        msg = await _recv_json(ws)
        if msg.get("type") == "auth_response" and msg.get("status") == "error":
            if msg.get("message") != "authentication_required":
                raise RuntimeError(f"Unexpected auth_response: {msg}")
            if verbose:
                print("auth: required")
            await ensure_authed()
            await _send_json(ws, {"type": "status"})
            msg = await _recv_json(ws)

        if msg.get("type") != "status_response":
            raise RuntimeError(f"Expected status_response, got: {msg}")
        if verbose:
            print("status: ok")

        before_voice = ((msg.get("kokoro") or {}).get("voice") or "").strip()

        # 2) capabilities
        await _send_json(ws, {"type": "capabilities"})
        caps = await _recv_json(ws)
        if caps.get("type") != "capabilities_response":
            raise RuntimeError(f"Expected capabilities_response, got: {caps}")
        if verbose:
            print("capabilities: ok")

        # 3) switch_model dry-run (should update config without reload)
        new_voice = "af_heart" if before_voice != "af_heart" else "af_sky"
        await _send_json(ws, {"type": "switch_model", "dry_run": True, "kokoro_voice": new_voice})
        sw = await _recv_json(ws, timeout=10.0)
        if sw.get("type") != "switch_response":
            raise RuntimeError(f"Expected switch_response, got: {sw}")
        if sw.get("status") not in ("success", "no_change"):
            raise RuntimeError(f"Unexpected switch_response.status: {sw}")
        if verbose:
            print(f"switch_model(dry_run): {sw.get('status')}")

        # 4) status again, verify switch took effect (at least config reflection)
        await _send_json(ws, {"type": "status"})
        after = await _recv_json(ws)
        if after.get("type") != "status_response":
            raise RuntimeError(f"Expected status_response, got: {after}")

        after_voice = ((after.get("kokoro") or {}).get("voice") or "").strip()
        if after_voice != new_voice:
            raise RuntimeError(f"Expected kokoro.voice={new_voice!r}, got {after_voice!r}")
        if verbose:
            print("status(after switch): ok")

        print("OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local AI Server WS smoke test")
    parser.add_argument("--url", default=os.getenv("LOCAL_WS_URL", "ws://127.0.0.1:8765"))
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    asyncio.run(run(args.url, args.auth_token, verbose=args.verbose))


if __name__ == "__main__":
    main()

