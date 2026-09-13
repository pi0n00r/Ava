from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .metar import Metar, CloudLayer, Wind, Visibility
from .speech import (
    speak_digits,
    speak_hhmm_zulu,
    speak_number,
    speak_cardinal,
    speak_qnh_hpa,
    speak_frequency_mhz,
    speak_runway,
    speak_icao_station,
    speak_feet_height,
    speak_metres_distance,
)


_CLOUD_WORDS = {
    "FEW": "few",
    "SCT": "scattered",
    "BKN": "broken",
    "OVC": "overcast",
    "VV": "vertical visibility",
}

_WX_DESC = {
    "NSW": "No significant weather",
    "RA": "rain",
    "DZ": "drizzle",
    "SN": "snow",
    "SG": "snow grains",
    "PL": "ice pellets",
    "GR": "hail",
    "GS": "small hail",
    "BR": "mist",
    "FG": "fog",
    "HZ": "haze",
    "TS": "thunderstorm",
    "SH": "showers",
    "FZ": "freezing",
    "VC": "in the vicinity",
}


@dataclass(frozen=True)
class AtisExtras:
    aerodrome_name: Optional[str] = None
    runway_in_use: Optional[str] = None
    afis_frequency_mhz: Optional[str] = None
    frequency_label: Optional[str] = None  # "ATIS", "AFIS", etc.
    traffic_advisory: Optional[str] = None
    # When True, and aerodrome_name not set, speak ICAO code via phonetics.
    speak_icao_when_no_name: bool = True
    # When True, include explicit "not available" lines for missing aerodrome extras.
    explicit_not_available: bool = False


def generate_atis_text(metar: Metar, extras: AtisExtras) -> str:
    station = (metar.station or "").strip().upper()
    if extras.aerodrome_name and extras.aerodrome_name.strip():
        aerodrome = extras.aerodrome_name.strip()
    else:
        aerodrome = speak_icao_station(station) if extras.speak_icao_when_no_name else station

    lines: List[str] = []
    lines.append(f"{aerodrome} automatic terminal information service.")

    if metar.hour is not None and metar.minute is not None:
        lines.append(f"Time {speak_hhmm_zulu(metar.hour, metar.minute)}.")

    if extras.runway_in_use:
        lines.append(f"Runway {speak_runway(extras.runway_in_use)} in use.")
    elif extras.explicit_not_available:
        lines.append("Runway in use information not available.")

    lines.extend(_wind_lines(metar.wind))
    lines.extend(_visibility_lines(metar.visibility, metar.cavok))
    lines.extend(_rvr_lines(getattr(metar, "rvr", []) or []))
    lines.extend(_cloud_lines(metar.clouds, metar.cavok))
    lines.extend(_temperature_lines(metar.temperature_c, metar.dewpoint_c))

    if metar.qnh_hpa:
        lines.append(f"{speak_qnh_hpa(metar.qnh_hpa)}.")

    # Weather: explicit "No significant weather" if no tokens.
    wx_line = _weather_line(metar.weather, metar.cavok)
    if wx_line:
        lines.append(f"{wx_line}.")

    if extras.afis_frequency_mhz:
        label = (extras.frequency_label or "AFIS").strip() or "AFIS"
        lines.append(f"{label} frequency {speak_frequency_mhz(extras.afis_frequency_mhz)}.")
    elif extras.explicit_not_available:
        lines.append("Information frequency not available.")

    if extras.traffic_advisory and extras.traffic_advisory.strip():
        # Expect caller to provide final text; keep as-is (deterministic/no hallucination).
        lines.append(extras.traffic_advisory.strip().rstrip(".") + ".")
    elif extras.explicit_not_available:
        lines.append("Traffic reporting instructions not available.")

    lines.append("This is an automatic service.")
    return "\n".join(lines)


def _wind_lines(wind: Optional[Wind]) -> List[str]:
    if not wind or wind.speed_kt is None:
        return []
    if wind.direction_deg == 0 and wind.speed_kt == 0 and not wind.variable:
        return ["Wind calm."]
    if wind.variable and wind.speed_kt is not None:
        spd = speak_number(wind.speed_kt) if wind.speed_kt < 10 else speak_digits(f"{wind.speed_kt:02d}")
        unit = "knot" if wind.speed_kt == 1 else "knots"
        return [f"Wind variable, {spd} {unit}."]
    if wind.direction_deg is not None and wind.speed_kt is not None:
        dir_spoken = speak_digits(f"{wind.direction_deg:03d}")
        spd = speak_digits(f"{wind.speed_kt:02d}") if wind.speed_kt >= 10 else speak_number(wind.speed_kt)
        unit = "knot" if wind.speed_kt == 1 else "knots"
        line = f"Wind {dir_spoken} degrees, {spd} {unit}"
        if wind.gust_kt:
            g = speak_digits(f"{wind.gust_kt:02d}") if wind.gust_kt >= 10 else speak_number(wind.gust_kt)
            line += f", gusting {g} knots"
        line += "."
        out = [line]
        if wind.var_from_deg is not None and wind.var_to_deg is not None:
            out.append(
                f"Wind varying between {speak_digits(f'{wind.var_from_deg:03d}')} and {speak_digits(f'{wind.var_to_deg:03d}')} degrees."
            )
        return out
    return []


def _visibility_lines(vis: Optional[Visibility], cavok: bool) -> List[str]:
    if cavok:
        return ["Visibility ten kilometres or more."]
    if not vis or vis.meters is None:
        return []
    m = vis.meters
    if m >= 10000 or m == 9999 or (getattr(vis, "greater_than", False) and m >= 9000):
        line = "Visibility ten kilometres or more"
    elif m % 1000 == 0:
        line = f"Visibility {speak_cardinal(m // 1000)} kilometres"
    else:
        line = f"Visibility {speak_metres_distance(m)} metres"
    if vis.ndv:
        line += ", no directional variation reported"
    return [line + "."]


def _rvr_lines(rvr_list: List[Any]) -> List[str]:
    out: List[str] = []
    for rvr in rvr_list:
        try:
            runway = getattr(rvr, "runway", None) or ""
            min_m = getattr(rvr, "min_m", None)
            max_m = getattr(rvr, "max_m", None)
            gt = bool(getattr(rvr, "greater_than", False))
            lt = bool(getattr(rvr, "less_than", False))
            trend = getattr(rvr, "trend", None)
        except Exception:
            continue

        if not runway or min_m is None:
            continue

        if max_m is not None and max_m != min_m:
            rng = f"{speak_metres_distance(min_m)} to {speak_metres_distance(max_m)} metres"
        else:
            rng = f"{speak_metres_distance(min_m)} metres"

        prefix = ""
        if gt:
            prefix = "more than "
        elif lt:
            prefix = "less than "

        line = f"Runway {speak_runway(runway)} visual range {prefix}{rng}"
        if trend == "U":
            line += ", increasing"
        elif trend == "D":
            line += ", decreasing"
        elif trend == "N":
            line += ", no change"
        out.append(line + ".")
    return out


def _cloud_lines(clouds: List[CloudLayer], cavok: bool) -> List[str]:
    if cavok:
        return ["No cloud below five thousand feet."]
    if not clouds:
        return []
    # If special "no cloud" markers present, prefer a single deterministic phrase.
    for c in clouds:
        if c.amount in ("NSC", "NCD", "SKC", "CLR"):
            return ["No significant clouds."]
    parts: List[str] = []
    for c in clouds:
        if c.amount == "VV":
            if c.height_ft is not None:
                parts.append(f"{_CLOUD_WORDS['VV']} {speak_feet_height(c.height_ft)} feet")
            else:
                parts.append("vertical visibility not reported")
            continue
        word = _CLOUD_WORDS.get(c.amount, c.amount.lower())
        if c.height_ft is not None:
            seg = f"{word} {speak_feet_height(c.height_ft)} feet"
        else:
            seg = f"{word} cloud not reported"
        if c.cloud_type:
            if c.cloud_type == "CB":
                seg += ", cumulonimbus"
            elif c.cloud_type == "TCU":
                seg += ", towering cumulus"
        parts.append(seg)
    if not parts:
        return []
    return [f"Clouds {', '.join(parts)}."]


def _temperature_lines(temp_c: Optional[int], dew_c: Optional[int]) -> List[str]:
    if temp_c is None and dew_c is None:
        return []
    if temp_c is None:
        return [f"Dew point {speak_number(dew_c or 0)}."]
    if dew_c is None:
        return [f"Temperature {speak_number(temp_c)}."]
    return [f"Temperature {speak_number(temp_c)}, dew point {speak_number(dew_c)}."]


def _weather_line(weather_tokens: List[str], cavok: bool) -> str:
    if cavok:
        return "No significant weather"
    if not weather_tokens:
        return "No significant weather"
    # NSW overrides other tokens.
    if any(t.upper() == "NSW" for t in weather_tokens):
        return "No significant weather"
    descs: List[str] = []
    for t in weather_tokens:
        d = _describe_weather_token(t)
        if d:
            descs.append(d)
    if not descs:
        return "No significant weather"
    # Keep concise: join with commas.
    return ", ".join(descs).capitalize()


def _describe_weather_token(token: str) -> str:
    t = (token or "").strip().upper()
    if not t:
        return ""
    if t == "NSW":
        return "no significant weather"
    intensity = ""
    vicinity = False
    if t.startswith("+"):
        intensity = "heavy "
        t = t[1:]
    elif t.startswith("-"):
        intensity = "light "
        t = t[1:]
    if t.startswith("VC"):
        vicinity = True
        t = t[2:]

    # Handle common composites like SHRA, TSRA, FZRA, etc.
    out_parts: List[str] = []
    # Known modifiers (two-letter chunks) appear at start
    # Order matters: FZ then SH then TS
    for mod in ("FZ", "TS", "SH"):
        if t.startswith(mod):
            out_parts.append(_WX_DESC.get(mod, "").strip())
            t = t[len(mod) :]
    # Remaining is phenomenon code(s); keep first match.
    phen = ""
    for code in ("DZ", "RA", "SN", "SG", "PL", "GR", "GS", "BR", "FG", "HZ", "DU", "SA", "FU", "VA", "PO", "SQ", "FC", "SS", "DS"):
        if code in t:
            phen = _WX_DESC.get(code, code.lower())
            break
    if phen:
        out_parts.append(phen)
    text = " ".join([p for p in out_parts if p])
    if not text:
        return ""
    if vicinity:
        text += " in the vicinity"
    return intensity + text
