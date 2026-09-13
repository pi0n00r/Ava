#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _http_json(
    method: str,
    url: str,
    payload: Optional[dict] = None,
    timeout: float = 30.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=headers, method=method)
    else:
        req = Request(url, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:  # nosec - local admin endpoint
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            data = json.loads(raw) if raw else {"error": raw}
        except Exception:
            data = {"error": str(e)}
        return e.code, data
    except URLError as e:
        return 0, {"error": f"URLError: {e}"}


@dataclass
class SwitchResult:
    ok: bool
    message: str
    elapsed_sec: float


def _switch(
    base: str,
    model_type: str,
    backend: str,
    model_path: str,
    extra: Optional[dict] = None,
    auth_headers: Optional[Dict[str, str]] = None,
) -> SwitchResult:
    url = f"{base}/api/local-ai/switch"
    payload: Dict[str, Any] = {
        "model_type": model_type,
        "backend": backend,
        "model_path": model_path,
    }
    if extra:
        payload.update(extra)

    t0 = time.time()
    status, data = _http_json("POST", url, payload=payload, timeout=90.0, extra_headers=auth_headers)
    dt = time.time() - t0

    if status != 200 or not isinstance(data, dict):
        return SwitchResult(False, f"HTTP {status}: {data}", dt)
    ok = bool(data.get("success"))
    msg = str(data.get("message") or "")
    return SwitchResult(ok, msg, dt)


def _get(base: str, path: str, timeout: float = 30.0, auth_headers: Optional[Dict[str, str]] = None) -> Any:
    status, data = _http_json("GET", f"{base}{path}", timeout=timeout, extra_headers=auth_headers)
    if status != 200:
        raise RuntimeError(f"GET {path} failed: HTTP {status}: {data}")
    return data


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _mint_jwt(secret: str, username: str, exp_seconds: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": username, "exp": int(time.time()) + int(exp_seconds)}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    ).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return signing_input.decode("utf-8") + "." + _b64url(sig)


def _read_env_file_value(env_path: str, key: str) -> str:
    if not env_path or not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    except Exception:
        return ""
    return ""


def _login_and_get_token(base: str, username: str, password: str, timeout: float = 15.0) -> str:
    url = f"{base}/api/auth/login"
    body = urlencode({"username": username, "password": password}).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:  # nosec - local admin endpoint
        raw = resp.read().decode("utf-8") or "{}"
        data = json.loads(raw)
    token = str((data or {}).get("access_token") or "")
    if not token:
        raise RuntimeError(f"Login did not return access_token: {data}")
    return token


def main() -> int:
    ap = argparse.ArgumentParser(description="Model switch matrix test via Admin UI endpoints.")
    ap.add_argument("--base", default="http://127.0.0.1:3003", help="Admin UI base URL (default: http://127.0.0.1:3003)")
    ap.add_argument("--token", default="", help="Admin UI Bearer token (JWT). If set, overrides all other auth options.")
    ap.add_argument("--jwt-secret", default="", help="JWT secret used by Admin UI to sign tokens. If set, mints a token locally.")
    ap.add_argument("--jwt-user", default="admin", help="Username (sub) for minted JWT (default: admin)")
    ap.add_argument("--username", default="", help="Admin UI username to login with (alternative to --jwt-secret/--token).")
    ap.add_argument("--password", default="", help="Admin UI password to login with (alternative to --jwt-secret/--token).")
    ap.add_argument("--env-path", default=".env", help="Path to .env for reading JWT_SECRET (default: .env)")
    ap.add_argument("--all-stt", action="store_true", help="Test all installed STT models per backend (default: first only)")
    ap.add_argument("--all-tts", action="store_true", help="Test all installed TTS models per backend (default: all piper + all kokoro voices)")
    ap.add_argument("--no-revert", action="store_true", help="Do not revert to the starting models at the end")
    ap.add_argument("--json-out", default="", help="Write JSON results to this path")
    args = ap.parse_args()

    base = args.base.rstrip("/")

    token = (args.token or "").strip()
    if not token:
        jwt_secret = (args.jwt_secret or "").strip() or (os.getenv("JWT_SECRET", "") or "").strip()
        if not jwt_secret:
            jwt_secret = _read_env_file_value(args.env_path, "JWT_SECRET")
        if jwt_secret:
            token = _mint_jwt(jwt_secret, args.jwt_user)
        elif args.username and args.password:
            token = _login_and_get_token(base, args.username, args.password)

    auth_headers = {"Authorization": f"Bearer {token}"} if token else None

    health = _get(base, "/api/system/health", timeout=15.0, auth_headers=auth_headers)
    local_ai = (health or {}).get("local_ai_server") or {}
    if local_ai.get("status") != "connected":
        raise SystemExit(f"Local AI is not connected: {_pretty(local_ai)}")
    start_details = local_ai.get("details") or {}
    start_stt_backend = (start_details.get("stt_backend") or "").strip()
    start_tts_backend = (start_details.get("tts_backend") or "").strip()
    start_llm_path = ((start_details.get("models") or {}).get("llm") or {}).get("path") or ""
    start_stt_path = ((start_details.get("models") or {}).get("stt") or {}).get("path") or ""
    start_tts_path = ((start_details.get("models") or {}).get("tts") or {}).get("path") or ""

    models = _get(base, "/api/local-ai/models", timeout=30.0, auth_headers=auth_headers)
    caps = _get(base, "/api/local-ai/capabilities", timeout=15.0, auth_headers=auth_headers)

    results: List[Dict[str, Any]] = []

    def record(kind: str, backend: str, model: str, res: SwitchResult) -> None:
        results.append(
            {
                "kind": kind,
                "backend": backend,
                "model": model,
                "ok": res.ok,
                "elapsed_sec": round(res.elapsed_sec, 3),
                "message": res.message,
            }
        )

    stt_models: Dict[str, List[dict]] = (models or {}).get("stt") or {}
    tts_models: Dict[str, List[dict]] = (models or {}).get("tts") or {}

    # ---- STT matrix ----
    stt_plan: List[Tuple[str, str]] = []
    if stt_models.get("vosk"):
        vosk_items = stt_models["vosk"]
        vosk_to_test = vosk_items if args.all_stt else vosk_items[:1]
        for item in vosk_to_test:
            stt_plan.append(("vosk", item["path"]))
    if stt_models.get("sherpa"):
        sherpa_items = stt_models["sherpa"]
        sherpa_to_test = sherpa_items if args.all_stt else sherpa_items[:1]
        for item in sherpa_to_test:
            stt_plan.append(("sherpa", item["path"]))

    if ((caps or {}).get("stt") or {}).get("faster_whisper", {}).get("available"):
        stt_plan.extend([("faster_whisper", "base"), ("faster_whisper", "small"), ("faster_whisper", "medium")])
    else:
        results.append(
            {
                "kind": "stt",
                "backend": "faster_whisper",
                "model": "base/small/medium",
                "ok": False,
                "elapsed_sec": 0.0,
                "message": f"skipped: {(caps or {}).get('stt', {}).get('faster_whisper', {}).get('reason') or 'not available'}",
            }
        )

    if ((caps or {}).get("stt") or {}).get("whisper_cpp", {}).get("available"):
        whispercpp_items = stt_models.get("whisper_cpp") or []
        if whispercpp_items:
            to_test = whispercpp_items if args.all_stt else whispercpp_items[:1]
            for item in to_test:
                stt_plan.append(("whisper_cpp", item["path"]))
        else:
            results.append(
                {
                    "kind": "stt",
                    "backend": "whisper_cpp",
                    "model": "installed",
                    "ok": False,
                    "elapsed_sec": 0.0,
                    "message": "skipped: whisper.cpp available but no ggml .bin model files found under models/stt",
                }
            )
    else:
        results.append(
            {
                "kind": "stt",
                "backend": "whisper_cpp",
                "model": "installed",
                "ok": False,
                "elapsed_sec": 0.0,
                "message": f"skipped: {(caps or {}).get('stt', {}).get('whisper_cpp', {}).get('reason') or 'not available'}",
            }
        )

    kroko_caps = ((caps or {}).get("stt") or {}).get("kroko_embedded") or {}
    if kroko_caps.get("available") and stt_models.get("kroko"):
        kroko_items = stt_models["kroko"]
        kroko_to_test = kroko_items if args.all_stt else kroko_items[:1]
        for item in kroko_to_test:
            stt_plan.append(("kroko", item["path"]))
    elif stt_models.get("kroko"):
        results.append(
            {
                "kind": "stt",
                "backend": "kroko",
                "model": "installed",
                "ok": False,
                "elapsed_sec": 0.0,
                "message": f"skipped: {kroko_caps.get('reason') or 'kroko_embedded not available'}",
            }
        )

    for backend, model_path in stt_plan:
        extra: Optional[dict] = None
        if backend == "kroko":
            extra = {"kroko_embedded": True}
        res = _switch(base, "stt", backend, model_path, extra=extra, auth_headers=auth_headers)
        record("stt", backend, model_path, res)

    # ---- TTS matrix ----
    # Piper: test all installed voices by default (usually fast).
    piper_items = tts_models.get("piper") or []
    if piper_items:
        piper_to_test = piper_items if args.all_tts else piper_items[:1]
        for item in piper_to_test:
            res = _switch(base, "tts", "piper", item["path"], auth_headers=auth_headers)
            record("tts", "piper", item["path"], res)

    # Kokoro: test each voice file found under the installed model dir.
    kokoro_items = tts_models.get("kokoro") or []
    if kokoro_items:
        kokoro = kokoro_items[0]
        voices = list((kokoro.get("voice_files") or {}).keys())
        voices = voices if args.all_tts else voices[:1]
        if not voices:
            results.append(
                {
                    "kind": "tts",
                    "backend": "kokoro",
                    "model": kokoro.get("path") or "/app/models/tts/kokoro",
                    "ok": False,
                    "elapsed_sec": 0.0,
                    "message": "skipped: no voice files detected under models/tts/kokoro/voices",
                }
            )
        for voice in voices:
            res = _switch(
                base,
                "tts",
                "kokoro",
                kokoro.get("path") or "/app/models/tts/kokoro",
                extra={"voice": voice, "kokoro_mode": "local"},
                auth_headers=auth_headers,
            )
            record("tts", "kokoro", voice, res)

    # MeloTTS: optional
    if ((caps or {}).get("tts") or {}).get("melotts", {}).get("available"):
        for voice in ("EN-US", "EN-BR", "EN-AU"):
            res = _switch(base, "tts", "melotts", voice, auth_headers=auth_headers)
            record("tts", "melotts", voice, res)
    else:
        results.append(
            {
                "kind": "tts",
                "backend": "melotts",
                "model": "EN-US/EN-BR/EN-AU",
                "ok": False,
                "elapsed_sec": 0.0,
                "message": f"skipped: {(caps or {}).get('tts', {}).get('melotts', {}).get('reason') or 'not available'}",
            }
        )

    # ---- Revert ----
    if not args.no_revert:
        if start_llm_path:
            _switch(base, "llm", "", start_llm_path, auth_headers=auth_headers)
        if start_stt_backend and start_stt_path:
            _switch(base, "stt", start_stt_backend, start_stt_path, auth_headers=auth_headers)
        if start_tts_backend and start_tts_path:
            _switch(base, "tts", start_tts_backend, start_tts_path, auth_headers=auth_headers)

    ok_count = sum(1 for r in results if r.get("ok") is True)
    fail_count = sum(1 for r in results if r.get("ok") is False)

    report = {
        "started": {
            "stt_backend": start_stt_backend,
            "stt_path": start_stt_path,
            "tts_backend": start_tts_backend,
            "tts_path": start_tts_path,
            "llm_path": start_llm_path,
        },
        "capabilities": caps,
        "results": results,
        "summary": {"ok": ok_count, "failed_or_skipped": fail_count, "total": len(results)},
    }

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)

    print(_pretty(report["summary"]))
    # Print only failures for quick scanning
    failures = [r for r in results if not r.get("ok")]
    if failures:
        print("\nFailures / skipped:")
        for r in failures:
            print(f"- {r['kind']} {r['backend']} {r['model']}: {r['message']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
