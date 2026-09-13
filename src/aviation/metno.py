from __future__ import annotations

import email.utils
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from typing import Dict, Optional, Tuple


_METNO_BASE = "https://api.met.no/weatherapi/tafmetar/1.0/metar.txt"
_RE_ICAO = re.compile(r"^[A-Z]{4}$")


@dataclass
class _CacheEntry:
    metar_raw: str
    fetched_at: float
    expires_at: float
    last_modified: Optional[str] = None


class MetNoMetarClient:
    """Fetches latest METAR from met.no tafmetar feed with small, polite caching.

    met.no requires an identifying User-Agent and encourages caching with Expires / If-Modified-Since.
    """

    def __init__(self, *, user_agent: str, timeout_seconds: float = 10.0, cache_ttl_seconds: int = 300):
        self.user_agent = (user_agent or "").strip()
        if not self.user_agent:
            raise ValueError("met.no User-Agent is required (must identify your app)")
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self._lock = threading.Lock()
        self._cache: Dict[str, _CacheEntry] = {}

    def get_latest_metar(self, icao: str) -> Tuple[str, Dict[str, object]]:
        station = (icao or "").strip().upper()
        if not _RE_ICAO.match(station):
            raise ValueError(f"Invalid ICAO: {icao!r}")

        now = time.time()
        with self._lock:
            entry = self._cache.get(station)
            if entry and now < entry.expires_at:
                return entry.metar_raw, {
                    "provider": "met.no",
                    "icao": station,
                    "cached": True,
                    "fetched_at_unix": entry.fetched_at,
                }

        metar_raw, last_modified, expires_at = self._fetch_and_parse(
            station, last_modified=(entry.last_modified if entry else None)
        )
        fetched_at = now
        with self._lock:
            self._cache[station] = _CacheEntry(
                metar_raw=metar_raw,
                fetched_at=fetched_at,
                expires_at=expires_at,
                last_modified=last_modified,
            )
        return metar_raw, {
            "provider": "met.no",
            "icao": station,
            "cached": False,
            "fetched_at_unix": fetched_at,
        }

    def refresh_latest_metar(self, icao: str) -> Tuple[str, Dict[str, object]]:
        """Force-refresh a station, updating cache even if within TTL."""
        station = (icao or "").strip().upper()
        if not _RE_ICAO.match(station):
            raise ValueError(f"Invalid ICAO: {icao!r}")
        with self._lock:
            entry = self._cache.get(station)
            last_modified = entry.last_modified if entry else None
        metar_raw, last_modified, expires_at = self._fetch_and_parse(station, last_modified=last_modified)
        fetched_at = time.time()
        with self._lock:
            self._cache[station] = _CacheEntry(
                metar_raw=metar_raw,
                fetched_at=fetched_at,
                expires_at=expires_at,
                last_modified=last_modified,
            )
        return metar_raw, {"provider": "met.no", "icao": station, "cached": False, "fetched_at_unix": fetched_at}

    def _fetch_and_parse(self, station: str, *, last_modified: Optional[str]) -> Tuple[str, Optional[str], float]:
        url = f"{_METNO_BASE}?icao={station}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", self.user_agent)
        req.add_header("Accept", "text/plain")
        if last_modified:
            req.add_header("If-Modified-Since", last_modified)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                headers = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                # Not modified; extend expiry (local TTL-based) without changing cached body.
                return self._not_modified_fallback(station, last_modified=e.headers.get("Last-Modified"), expires=e.headers.get("Expires"))
            raise RuntimeError(f"met.no HTTP error {e.code}: {e.reason}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to fetch METAR from met.no: {e}") from e

        if status not in (200, 204):
            raise RuntimeError(f"met.no unexpected status: {status}")

        metar = _extract_latest_metar_line(body, station)
        if not metar:
            raise RuntimeError(f"No METAR found for {station} in met.no response")

        last_mod = headers.get("last-modified")
        expires = headers.get("expires")
        expires_at = _compute_expires_at(expires, ttl_seconds=self.cache_ttl_seconds)
        return metar, last_mod, expires_at

    def _not_modified_fallback(self, station: str, *, last_modified: Optional[str], expires: Optional[str]) -> Tuple[str, Optional[str], float]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(station)
            if not entry:
                raise RuntimeError("met.no returned 304 but no cached entry exists")
            expires_at = _compute_expires_at(expires, ttl_seconds=self.cache_ttl_seconds)
            # Ensure we don't shorten below a minimal next-check window unless Expires forces it.
            if expires_at <= now and self.cache_ttl_seconds > 0:
                expires_at = now + self.cache_ttl_seconds
            return entry.metar_raw, (last_modified or entry.last_modified), expires_at


def _compute_expires_at(expires_header: Optional[str], *, ttl_seconds: int) -> float:
    now = time.time()
    # Default TTL (user requirement: e.g., 300s).
    default_exp = now + max(0, int(ttl_seconds))
    if not expires_header:
        return default_exp
    try:
        dt = email.utils.parsedate_to_datetime(expires_header)
        if dt is None:
            return default_exp
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        expires_at = dt.timestamp()
        # Respect both met.no Expires and our own TTL cap.
        return min(expires_at, default_exp)
    except Exception:
        return default_exp


def _extract_latest_metar_line(body: str, station: str) -> str:
    # The endpoint usually returns one station, but we still defensively scan.
    lines = []
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if station not in line:
            continue
        if line.startswith(("METAR ", "SPECI ")) and f" {station} " in f" {line} ":
            lines.append(line.rstrip("="))
    if not lines:
        # Fallback: any line containing the station.
        for raw_line in (body or "").splitlines():
            line = raw_line.strip()
            if station in line:
                lines.append(line.rstrip("="))
    return lines[-1] if lines else ""
