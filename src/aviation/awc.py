"""Aviation Weather Center (aviationweather.gov) API client.

Provides airport data including:
- Airport name
- ATIS/Tower frequencies
- Runway information
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json
import threading
import time

logger = logging.getLogger(__name__)

AWC_AIRPORT_API = "https://aviationweather.gov/api/data/airport"
AWC_USER_AGENT = "Asterisk-AI-Voice-Agent (+https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk)"

_CACHE_LOCK = threading.Lock()
_AIRPORT_CACHE: Dict[str, Tuple[float, Optional["AirportInfo"]]] = {}


@dataclass
class Runway:
    """Runway information from AWC."""
    id: str  # e.g., "04L/22R"
    length_ft: Optional[int] = None
    width_ft: Optional[int] = None
    surface: Optional[str] = None  # H=hard, A=asphalt, C=concrete
    alignment: Optional[int] = None  # magnetic heading


@dataclass
class AirportInfo:
    """Airport information from AWC."""
    icao: str
    iata: Optional[str] = None
    name: Optional[str] = None
    country: Optional[str] = None
    elevation_ft: Optional[int] = None
    atis_frequencies: List[str] = field(default_factory=list)
    tower_frequencies: List[str] = field(default_factory=list)
    runways: List[Runway] = field(default_factory=list)
    raw_data: Optional[Dict[str, Any]] = None


def _parse_frequencies(freq_str: Optional[str]) -> Tuple[List[str], List[str]]:
    """Parse frequency string like 'D-ATIS,128.725;LCL/P,119.1;TWR,118.5'.
    
    Returns (atis_frequencies, tower_frequencies).
    """
    atis_freqs = []
    tower_freqs = []
    
    if not freq_str:
        return atis_freqs, tower_freqs
    
    for item in freq_str.split(";"):
        item = item.strip()
        if not item:
            continue
        parts = item.split(",")
        if len(parts) >= 2:
            freq_type = parts[0].upper()
            freq_value = parts[1].strip()
            
            # ATIS frequencies
            if "ATIS" in freq_type:
                if freq_value and freq_value not in atis_freqs:
                    atis_freqs.append(freq_value)
            # Tower frequencies
            elif freq_type in ("TWR", "LCL/P", "LCL", "TWR/P"):
                if freq_value and freq_value not in tower_freqs:
                    tower_freqs.append(freq_value)
    
    return atis_freqs, tower_freqs


def _parse_dimension(dim_str: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """Parse dimension string like '12079x200' to (length, width)."""
    if not dim_str:
        return None, None
    match = re.match(r"(\d+)x(\d+)", dim_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def _parse_runways(runway_list: Optional[List[Dict[str, Any]]]) -> List[Runway]:
    """Parse runway list from AWC response."""
    runways = []
    if not runway_list:
        return runways
    
    for rwy in runway_list:
        length, width = _parse_dimension(rwy.get("dimension"))
        runways.append(Runway(
            id=rwy.get("id", ""),
            length_ft=length,
            width_ft=width,
            surface=rwy.get("surface"),
            alignment=rwy.get("alignment"),
        ))
    
    return runways


def fetch_airport_info(
    icao: str,
    timeout_seconds: float = 10.0,
    *,
    user_agent: Optional[str] = None,
    cache_ttl_seconds: int = 300,
) -> Optional[AirportInfo]:
    """Fetch airport information from aviationweather.gov.
    
    Args:
        icao: ICAO airport code (e.g., KJFK, EGLL)
        timeout_seconds: Request timeout
        user_agent: Optional User-Agent override (else uses AWC_USER_AGENT or env AWC_USER_AGENT)
        cache_ttl_seconds: Cache TTL (seconds). 0 disables caching.
        
    Returns:
        AirportInfo if found, None otherwise
    """
    icao = icao.strip().upper()
    if not icao or len(icao) < 3:
        return None

    ttl = max(0, int(cache_ttl_seconds))
    if ttl > 0:
        with _CACHE_LOCK:
            cached = _AIRPORT_CACHE.get(icao)
        if cached:
            cached_at, cached_val = cached
            if time.time() - cached_at < ttl:
                return cached_val
    
    url = f"{AWC_AIRPORT_API}?ids={icao}&format=json"
    ua = (user_agent or os.getenv("AWC_USER_AGENT") or AWC_USER_AGENT).strip()
    
    try:
        req = Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
        with urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        
        if not data or not isinstance(data, list) or len(data) == 0:
            logger.debug(f"No airport data found for {icao}")
            if ttl > 0:
                with _CACHE_LOCK:
                    _AIRPORT_CACHE[icao] = (time.time(), None)
            return None
        
        airport = data[0]
        
        # Parse frequencies
        atis_freqs, tower_freqs = _parse_frequencies(airport.get("freqs"))
        
        # Parse runways
        runways = _parse_runways(airport.get("runways"))
        
        # Clean airport name
        name = airport.get("name", "").strip()
        if name:
            # Remove trailing whitespace and normalize
            name = " ".join(name.split())
        
        info = AirportInfo(
            icao=airport.get("icaoId", icao),
            iata=airport.get("iataId"),
            name=name or None,
            country=airport.get("country"),
            elevation_ft=airport.get("elev"),
            atis_frequencies=atis_freqs,
            tower_frequencies=tower_freqs,
            runways=runways,
            raw_data=airport,
        )
        if ttl > 0:
            with _CACHE_LOCK:
                _AIRPORT_CACHE[icao] = (time.time(), info)
        return info
        
    except HTTPError as e:
        logger.warning(f"HTTP error fetching airport {icao}: {e.code}")
        if ttl > 0:
            with _CACHE_LOCK:
                _AIRPORT_CACHE[icao] = (time.time(), None)
        return None
    except URLError as e:
        logger.warning(f"URL error fetching airport {icao}: {e.reason}")
        if ttl > 0:
            with _CACHE_LOCK:
                _AIRPORT_CACHE[icao] = (time.time(), None)
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error for airport {icao}: {e}")
        if ttl > 0:
            with _CACHE_LOCK:
                _AIRPORT_CACHE[icao] = (time.time(), None)
        return None
    except Exception as e:
        logger.warning(f"Error fetching airport {icao}: {e}")
        if ttl > 0:
            with _CACHE_LOCK:
                _AIRPORT_CACHE[icao] = (time.time(), None)
        return None


def get_primary_atis_frequency(airport: AirportInfo) -> Optional[str]:
    """Get the primary ATIS frequency for an airport.
    
    Prefers D-ATIS frequencies (usually VHF) over others.
    """
    if not airport.atis_frequencies:
        return None
    
    # Return the first ATIS frequency (usually the primary one)
    return airport.atis_frequencies[0]


def get_runway_list_spoken(airport: AirportInfo) -> Optional[str]:
    """Get a spoken list of available runways.
    
    Example: "Runways zero four left, zero four right, one three left, one three right"
    """
    if not airport.runways:
        return None
    
    from .speech import speak_runway
    
    # Get unique runway designators (just the first part of each pair)
    designators = set()
    for rwy in airport.runways:
        if rwy.id:
            # Split "04L/22R" into "04L" and "22R", take first
            parts = rwy.id.split("/")
            if parts:
                designators.add(parts[0])
    
    if not designators:
        return None
    
    # Sort designators numerically
    sorted_designators = sorted(designators, key=lambda x: (int(re.sub(r'\D', '', x) or '0'), x))
    
    spoken_runways = [speak_runway(d) for d in sorted_designators]
    
    if len(spoken_runways) == 1:
        return f"Runway {spoken_runways[0]} available."
    else:
        return f"Runways {', '.join(spoken_runways)} available."
