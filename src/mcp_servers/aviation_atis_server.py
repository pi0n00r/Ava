from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml

from src.aviation.atis import AtisExtras, generate_atis_text
from src.aviation.metar import parse_metar
from src.aviation.metno import MetNoMetarClient
from src.aviation.awc import fetch_airport_info, get_primary_atis_frequency, get_runway_list_spoken, AirportInfo
from src.mcp.stdio_framing import decode_frame, encode_message


@dataclass(frozen=True)
class AerodromeConfig:
    aerodrome_name: Optional[str] = None
    runway_in_use: Optional[str] = None
    afis_frequency_mhz: Optional[str] = None
    frequency_label: Optional[str] = None
    traffic_advisory: Optional[str] = None
    explicit_not_available: Optional[bool] = None


@dataclass(frozen=True)
class ServerConfig:
    metno_user_agent: str
    metno_timeout_seconds: float
    cache_ttl_seconds: int
    awc_user_agent: Optional[str]
    awc_timeout_seconds: float
    awc_cache_ttl_seconds: int
    explicit_not_available_default: bool
    aerodromes: Dict[str, AerodromeConfig]


def _load_config(path: Optional[str], *, user_agent: Optional[str], cache_ttl_seconds: Optional[int]) -> ServerConfig:
    raw: Dict[str, Any] = {}
    if path:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    metno = raw.get("metno") if isinstance(raw.get("metno"), dict) else {}
    awc = raw.get("awc") if isinstance(raw.get("awc"), dict) else {}
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    aerodromes_raw = raw.get("aerodromes") if isinstance(raw.get("aerodromes"), dict) else {}
    aerodromes: Dict[str, AerodromeConfig] = {}
    for icao, cfg in aerodromes_raw.items():
        if not isinstance(cfg, dict):
            continue
        aerodromes[str(icao).strip().upper()] = AerodromeConfig(
            aerodrome_name=(cfg.get("aerodrome_name") or cfg.get("name")),
            runway_in_use=cfg.get("runway_in_use"),
            afis_frequency_mhz=cfg.get("afis_frequency_mhz"),
            frequency_label=cfg.get("frequency_label"),
            traffic_advisory=cfg.get("traffic_advisory"),
            explicit_not_available=cfg.get("explicit_not_available"),
        )

    ua = (user_agent or metno.get("user_agent") or os.getenv("METNO_USER_AGENT") or "").strip()
    if not ua:
        raise ValueError(
            "met.no requires an identifying User-Agent. Provide --user-agent, set metno.user_agent in config, or set METNO_USER_AGENT."
        )

    ttl = cache_ttl_seconds if cache_ttl_seconds is not None else (metno.get("cache_ttl_seconds") or os.getenv("METNO_CACHE_TTL_SECONDS"))
    try:
        ttl_i = int(ttl) if ttl is not None else 300
    except Exception:
        ttl_i = 300
    ttl_i = max(0, ttl_i)

    timeout = metno.get("timeout_seconds")
    try:
        timeout_f = float(timeout) if timeout is not None else 10.0
    except Exception:
        timeout_f = 10.0
    timeout_f = max(1.0, timeout_f)

    explicit_not_available_default = bool(defaults.get("explicit_not_available", False))

    awc_ua = (awc.get("user_agent") or os.getenv("AWC_USER_AGENT") or "").strip() or None
    # If not provided, reuse met.no UA (already identifying) to avoid missing UA in deployments.
    if awc_ua is None:
        awc_ua = ua
    awc_timeout = awc.get("timeout_seconds")
    try:
        awc_timeout_f = float(awc_timeout) if awc_timeout is not None else 5.0
    except Exception:
        awc_timeout_f = 5.0
    awc_timeout_f = max(1.0, awc_timeout_f)
    awc_ttl = awc.get("cache_ttl_seconds")
    try:
        awc_ttl_i = int(awc_ttl) if awc_ttl is not None else 300
    except Exception:
        awc_ttl_i = 300
    awc_ttl_i = max(0, awc_ttl_i)

    return ServerConfig(
        metno_user_agent=ua,
        metno_timeout_seconds=timeout_f,
        cache_ttl_seconds=ttl_i,
        awc_user_agent=awc_ua,
        awc_timeout_seconds=awc_timeout_f,
        awc_cache_ttl_seconds=awc_ttl_i,
        explicit_not_available_default=explicit_not_available_default,
        aerodromes=aerodromes,
    )


def _write(payload: Dict[str, Any]) -> None:
    sys.stdout.buffer.write(encode_message(payload))
    sys.stdout.buffer.flush()


def _error(request_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": int(code), "message": str(message)}})


def _result(request_id: Any, result: Dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": request_id, "result": result})


def _tools_list() -> Dict[str, Any]:
    return {
        "tools": [
            {
                "name": "get_atis",
                "description": "Generate a deterministic spoken ATIS from the latest METAR for an ICAO station (4 letters, e.g., KJFK, KLAX, EGLL, KSJC). US airports start with K (e.g., San Jose = KSJC, JFK = KJFK). UK airports start with EG (e.g., Heathrow = EGLL).",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "icao": {"type": "string", "description": "4-letter ICAO station code. US airports start with K (KJFK, KLAX, KSJC, KORD). UK airports start with EG (EGLL)."},
                        "metar_raw": {"type": "string", "description": "Optional raw METAR string for testing; skips met.no fetch."},
                    },
                    "required": ["icao"],
                },
            }
        ]
    }


def _handle_get_atis(
    *,
    cfg: ServerConfig,
    client: MetNoMetarClient,
    tracked_icaos: Optional[set[str]] = None,
    tracked_lock: Optional[threading.Lock] = None,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    metar_raw = (arguments.get("metar_raw") or "").strip()
    icao = (arguments.get("icao") or "").strip().upper()

    if not metar_raw and not icao:
        raise ValueError("Provide either 'icao' or 'metar_raw'")

    meta: Dict[str, Any] = {}
    if not metar_raw:
        try:
            metar_raw, meta = client.get_latest_metar(icao)
        except Exception:
            # Fail with a caller-safe message (deterministic; no stale/weather guessing).
            raise RuntimeError("METAR data is temporarily unavailable. Please try again in a moment.") from None

    metar = parse_metar(metar_raw)
    station = (metar.station or icao or "").strip().upper()
    if tracked_icaos is not None and tracked_lock is not None and station:
        with tracked_lock:
            tracked_icaos.add(station)

    # Try to get enhanced airport data from aviationweather.gov
    awc_info: Optional[AirportInfo] = None
    try:
        awc_info = fetch_airport_info(
            station,
            timeout_seconds=cfg.awc_timeout_seconds,
            user_agent=cfg.awc_user_agent,
            cache_ttl_seconds=cfg.awc_cache_ttl_seconds,
        )
    except Exception:
        pass  # Fallback to config-only data

    aerodrome_cfg = cfg.aerodromes.get(station)
    explicit_na = cfg.explicit_not_available_default
    if aerodrome_cfg and aerodrome_cfg.explicit_not_available is not None:
        explicit_na = bool(aerodrome_cfg.explicit_not_available)

    # Build extras: prefer config, fallback to AWC data
    aerodrome_name = (aerodrome_cfg.aerodrome_name if aerodrome_cfg else None)
    if not aerodrome_name and awc_info and awc_info.name:
        aerodrome_name = awc_info.name

    freq_mhz = (aerodrome_cfg.afis_frequency_mhz if aerodrome_cfg else None)
    freq_label = (aerodrome_cfg.frequency_label if aerodrome_cfg else None)
    if not freq_mhz and awc_info:
        # AWC provides ATIS frequencies; do not label them as AFIS.
        freq_mhz = get_primary_atis_frequency(awc_info)
        if freq_mhz:
            freq_label = "ATIS"
    if freq_mhz and not freq_label:
        # Default label: if the frequency was configured, it's usually AFIS for uncontrolled aerodromes.
        freq_label = "AFIS"

    # Runway in use: only from config (AWC doesn't have "in use" info)
    runway_in_use = (aerodrome_cfg.runway_in_use if aerodrome_cfg else None)

    # Traffic advisory: only from config
    traffic_advisory = (aerodrome_cfg.traffic_advisory if aerodrome_cfg else None)

    extras = AtisExtras(
        aerodrome_name=aerodrome_name,
        runway_in_use=runway_in_use,
        afis_frequency_mhz=freq_mhz,
        frequency_label=freq_label,
        traffic_advisory=traffic_advisory,
        speak_icao_when_no_name=True,
        explicit_not_available=explicit_na,
    )

    atis_text = generate_atis_text(metar, extras)

    # Add available runways info if we have AWC data but no runway in use
    if not runway_in_use and awc_info and awc_info.runways:
        runway_info = get_runway_list_spoken(awc_info)
        if runway_info:
            # Insert before "This is an automatic service" line
            lines = atis_text.split("\n")
            insert_idx = len(lines) - 1  # Before last line
            for i, line in enumerate(lines):
                if "automatic service" in line.lower():
                    insert_idx = i
                    break
            lines.insert(insert_idx, runway_info)
            atis_text = "\n".join(lines)

    structured: Dict[str, Any] = {
        "atis_text": atis_text,
        "icao": station,
        "metar_raw": metar.raw,
        "parsed": {
            "time": {"day": metar.day, "hour": metar.hour, "minute": metar.minute},
            "wind": metar.wind.__dict__ if metar.wind else None,
            "visibility": metar.visibility.__dict__ if metar.visibility else None,
            "rvr": [r.__dict__ for r in (getattr(metar, "rvr", []) or [])],
            "weather_tokens": list(metar.weather or []),
            "clouds": [c.__dict__ for c in (metar.clouds or [])],
            "temperature_c": metar.temperature_c,
            "dewpoint_c": metar.dewpoint_c,
            "qnh_hpa": metar.qnh_hpa,
            "cavok": metar.cavok,
            "nosig": metar.nosig,
        },
        "extras": {
            "aerodrome_name": extras.aerodrome_name,
            "runway_in_use": extras.runway_in_use,
            "afis_frequency_mhz": extras.afis_frequency_mhz,
            "frequency_label": extras.frequency_label,
            "traffic_advisory": extras.traffic_advisory,
        },
        "awc_data": {
            "available": awc_info is not None,
            "iata": awc_info.iata if awc_info else None,
            "country": awc_info.country if awc_info else None,
            "atis_frequencies": awc_info.atis_frequencies if awc_info else [],
            "tower_frequencies": awc_info.tower_frequencies if awc_info else [],
            "runways": [{"id": r.id, "length_ft": r.length_ft} for r in (awc_info.runways if awc_info else [])],
        },
        "source": meta,
    }

    return {
        "content": [{"type": "text", "text": atis_text}],
        "structured": structured,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Aviation ATIS MCP Server (stdio).")
    parser.add_argument("--config", help="Path to aviation ATIS YAML config (per-aerodrome extras)", default=None)
    parser.add_argument("--user-agent", help="met.no User-Agent (must identify your app)", default=None)
    parser.add_argument("--cache-ttl-seconds", type=int, default=None, help="METAR cache TTL (default 300)")
    args = parser.parse_args(argv)

    cfg_lock = threading.Lock()
    cfg = _load_config(args.config, user_agent=args.user_agent, cache_ttl_seconds=args.cache_ttl_seconds)
    cfg_path = args.config
    cfg_mtime = None
    if cfg_path and os.path.exists(cfg_path):
        try:
            cfg_mtime = os.path.getmtime(cfg_path)
        except Exception:
            cfg_mtime = None

    client = MetNoMetarClient(
        user_agent=cfg.metno_user_agent,
        timeout_seconds=cfg.metno_timeout_seconds,
        cache_ttl_seconds=cfg.cache_ttl_seconds,
    )

    tracked_lock = threading.Lock()
    tracked_icaos: set[str] = set(cfg.aerodromes.keys())

    def _maybe_reload_cfg() -> ServerConfig:
        nonlocal cfg, cfg_mtime
        if not cfg_path:
            return cfg
        try:
            mtime = os.path.getmtime(cfg_path)
        except Exception:
            return cfg
        if cfg_mtime is not None and mtime == cfg_mtime:
            return cfg
        with cfg_lock:
            try:
                mtime2 = os.path.getmtime(cfg_path)
            except Exception:
                return cfg
            if cfg_mtime is not None and mtime2 == cfg_mtime:
                return cfg
            new_cfg = _load_config(cfg_path, user_agent=args.user_agent, cache_ttl_seconds=args.cache_ttl_seconds)
            cfg = new_cfg
            cfg_mtime = mtime2
            # Expand tracked set (do not remove entries to avoid thrash).
            with tracked_lock:
                tracked_icaos.update(set(new_cfg.aerodromes.keys()))
            return cfg

    # Optional background refresh: keeps a warm cache so callers don't wait on network fetch.
    # Default cadence is cache_ttl_seconds (e.g., 5 minutes).
    stop_event = threading.Event()
    if cfg.cache_ttl_seconds > 0:
        def _refresh_loop() -> None:
            # Stagger refreshes in case many ICAOs are configured.
            interval = max(60, int(cfg.cache_ttl_seconds))
            while not stop_event.is_set():
                with tracked_lock:
                    targets = list(tracked_icaos)
                for icao in targets:
                    if stop_event.is_set():
                        break
                    try:
                        client.refresh_latest_metar(icao)
                    except Exception:
                        # Keep deterministic tool behavior; refresh failures should not kill server.
                        pass
                    time.sleep(0.25)
                time.sleep(interval)

        t = threading.Thread(target=_refresh_loop, name="metno-refresh", daemon=True)
        t.start()

    buf = bytearray()
    stdin = sys.stdin.buffer
    while True:
        # Read one byte at a time to avoid blocking on large reads
        chunk = stdin.read(1)
        if not chunk:
            stop_event.set()
            return 0
        buf.extend(chunk)
        while True:
            msg, consumed = decode_frame(buf)
            if msg is None:
                break
            del buf[:consumed]
            try:
                if not isinstance(msg, dict):
                    continue
                if "method" not in msg:
                    continue
                method = msg.get("method")
                req_id = msg.get("id")
                params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

                if method == "initialize":
                    _result(
                        req_id,
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "aviation_atis", "version": "0.1"},
                        },
                    )
                elif method == "tools/list":
                    _result(req_id, _tools_list())
                elif method == "tools/call":
                    name = (params.get("name") or "").strip()
                    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                    if name != "get_atis":
                        _error(req_id, -32601, f"Unknown tool: {name}")
                        continue
                    result = _handle_get_atis(
                        cfg=_maybe_reload_cfg(),
                        client=client,
                        tracked_icaos=tracked_icaos,
                        tracked_lock=tracked_lock,
                        arguments=arguments,
                    )
                    _result(req_id, result)
                else:
                    # Notifications / unsupported methods
                    if req_id is not None:
                        _error(req_id, -32601, f"Method not found: {method}")
            except Exception as exc:
                # JSON-RPC error
                req_id = msg.get("id") if isinstance(msg, dict) else None
                if req_id is not None:
                    _error(req_id, -32000, str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
