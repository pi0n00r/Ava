from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Wind:
    direction_deg: Optional[int] = None  # 0-360, None when VRB
    variable: bool = False
    speed_kt: Optional[int] = None
    gust_kt: Optional[int] = None
    var_from_deg: Optional[int] = None
    var_to_deg: Optional[int] = None


@dataclass(frozen=True)
class Visibility:
    meters: Optional[int] = None
    ndv: bool = False
    cavok: bool = False
    greater_than: bool = False
    raw: Optional[str] = None


@dataclass(frozen=True)
class CloudLayer:
    amount: str  # FEW/SCT/BKN/OVC/VV/NSC/SKC/CLR/NCD
    height_ft: Optional[int] = None
    cloud_type: Optional[str] = None  # CB/TCU


@dataclass(frozen=True)
class Metar:
    raw: str
    station: Optional[str] = None
    day: Optional[int] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    wind: Optional[Wind] = None
    visibility: Optional[Visibility] = None
    weather: List[str] = field(default_factory=list)  # raw weather tokens
    rvr: List["RVR"] = field(default_factory=list)
    clouds: List[CloudLayer] = field(default_factory=list)
    temperature_c: Optional[int] = None
    dewpoint_c: Optional[int] = None
    qnh_hpa: Optional[int] = None
    cavok: bool = False
    nosig: bool = False
    trend_tokens: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RVR:
    runway: str
    min_m: Optional[int] = None
    max_m: Optional[int] = None
    greater_than: bool = False
    less_than: bool = False
    trend: Optional[str] = None  # U/D/N


_RE_STATION = re.compile(r"^[A-Z]{4}$")
_RE_TIME = re.compile(r"^(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})Z$")
_RE_WIND = re.compile(r"^(?P<dir>\d{3}|VRB)(?P<spd>\d{2,3})(G(?P<gst>\d{2,3}))?(?P<unit>KT|MPS)$")
_RE_VAR_WIND = re.compile(r"^(?P<from>\d{3})V(?P<to>\d{3})$")
_RE_VIS = re.compile(r"^(?P<vis>\d{4})(?P<ndv>NDV)?$")
_RE_RVR = re.compile(r"^R(?P<rwy>\d{2}[LRC]?)/(?P<val>[MP]?\d{4})(V[MP]?\d{4})?(?P<trend>[UDN])?$")
_RE_CLOUD = re.compile(r"^(?P<amt>FEW|SCT|BKN|OVC)(?P<hgt>\d{3}|///)(?P<type>CB|TCU)?$")
_RE_VV = re.compile(r"^VV(?P<hgt>\d{3}|///)$")
_RE_TEMP_DEW = re.compile(r"^(?P<t>M?\d{2})/(?P<d>M?\d{2}|//)$")
_RE_QNH = re.compile(r"^Q(?P<qnh>\d{4})$")
_RE_ALTIMETER = re.compile(r"^A(?P<inhg>\d{4})$")
_RE_VIS_SM = re.compile(r"^(?P<prefix>P)?(?P<whole>\d+)?(?P<fraction>\d/\d)?SM$")


def parse_metar(raw: str) -> Metar:
    text = (raw or "").strip()
    text = text.rstrip("=")
    tokens = [t for t in text.split() if t]
    metar = Metar(raw=text)
    if not tokens:
        return metar

    idx = 0
    if tokens[idx] in ("METAR", "SPECI"):
        idx += 1

    if idx < len(tokens) and _RE_STATION.match(tokens[idx]):
        metar = _replace(metar, station=tokens[idx])
        idx += 1

    if idx < len(tokens):
        m = _RE_TIME.match(tokens[idx])
        if m:
            metar = _replace(
                metar,
                day=int(m.group("dd")),
                hour=int(m.group("hh")),
                minute=int(m.group("mm")),
            )
            idx += 1

    wind: Optional[Wind] = None
    vis: Optional[Visibility] = None
    weather: List[str] = []
    rvr: List[RVR] = []
    clouds: List[CloudLayer] = []
    cavok = False
    nosig = False
    trend_tokens: List[str] = []

    # Parse sequentially until RMK; collect remaining as trend tokens.
    while idx < len(tokens):
        t = tokens[idx]
        if t == "RMK":
            break

        if t == "CAVOK":
            cavok = True
            vis = Visibility(meters=10000, ndv=False, cavok=True)
            idx += 1
            continue

        m = _RE_WIND.match(t)
        if m and wind is None:
            d = m.group("dir")
            spd = int(m.group("spd"))
            gst = int(m.group("gst")) if m.group("gst") else None
            unit = (m.group("unit") or "KT").upper()
            if unit == "MPS":
                spd = int(round(spd * 1.94384))
                if gst is not None:
                    gst = int(round(gst * 1.94384))
            if d == "VRB":
                wind = Wind(direction_deg=None, variable=True, speed_kt=spd, gust_kt=gst)
            else:
                wind = Wind(direction_deg=int(d), variable=False, speed_kt=spd, gust_kt=gst)
            idx += 1
            # Optional variable wind range token
            if idx < len(tokens):
                mv = _RE_VAR_WIND.match(tokens[idx])
                if mv and wind:
                    wind = Wind(
                        direction_deg=wind.direction_deg,
                        variable=wind.variable,
                        speed_kt=wind.speed_kt,
                        gust_kt=wind.gust_kt,
                        var_from_deg=int(mv.group("from")),
                        var_to_deg=int(mv.group("to")),
                    )
                    idx += 1
            continue

        m = _RE_VIS.match(t)
        if m and vis is None:
            vis_m = int(m.group("vis"))
            ndv = bool(m.group("ndv"))
            vis = Visibility(meters=vis_m, ndv=ndv, cavok=False, raw=t)
            idx += 1
            continue

        m = _RE_VIS_SM.match(t)
        if m and vis is None:
            # Convert statute miles to meters (ICAO units for output).
            whole = int(m.group("whole") or 0)
            frac = 0.0
            if m.group("fraction"):
                num_s, den_s = m.group("fraction").split("/", 1)
                try:
                    frac = float(int(num_s)) / float(int(den_s))
                except Exception:
                    frac = 0.0
            sm = float(whole) + float(frac)
            meters = int(round(sm * 1609.34))
            vis = Visibility(meters=meters, ndv=False, cavok=False, greater_than=bool(m.group("prefix")), raw=t)
            idx += 1
            continue

        m = _RE_RVR.match(t)
        if m:
            runway = m.group("rwy")
            val = m.group("val")
            trend = m.group("trend")
            v_from = _parse_rvr_val(val)
            v_to = None
            greater, less = _rvr_flags(val)
            # Variable range token: ...Vxxxx
            if "V" in t:
                try:
                    after = t.split("/", 1)[1]
                    if "V" in after:
                        _, second = after.split("V", 1)
                        v_to = _parse_rvr_val(second[:5])  # includes optional M/P
                        if v_to is not None:
                            # If second has flags, merge them (conservative)
                            greater2, less2 = _rvr_flags(second[:5])
                            greater = greater or greater2
                            less = less or less2
                except Exception:
                    v_to = None
            rvr.append(
                RVR(
                    runway=runway,
                    min_m=v_from,
                    max_m=v_to,
                    greater_than=greater,
                    less_than=less,
                    trend=trend,
                )
            )
            idx += 1
            continue

        # Clouds
        m = _RE_CLOUD.match(t)
        if m:
            amt = m.group("amt")
            hgt = m.group("hgt")
            ctype = m.group("type")
            height_ft = None
            if hgt and hgt.isdigit():
                height_ft = int(hgt) * 100
            clouds.append(CloudLayer(amount=amt, height_ft=height_ft, cloud_type=ctype))
            idx += 1
            continue

        m = _RE_VV.match(t)
        if m:
            hgt = m.group("hgt")
            height_ft = None
            if hgt and hgt.isdigit():
                height_ft = int(hgt) * 100
            clouds.append(CloudLayer(amount="VV", height_ft=height_ft, cloud_type=None))
            idx += 1
            continue

        if t in ("NSC", "NCD", "SKC", "CLR"):
            clouds.append(CloudLayer(amount=t, height_ft=None, cloud_type=None))
            idx += 1
            continue

        m = _RE_TEMP_DEW.match(t)
        if m and metar.temperature_c is None:
            temp = _parse_signed_int(m.group("t"))
            dew = _parse_signed_int(m.group("d")) if m.group("d") != "//" else None
            metar = _replace(metar, temperature_c=temp, dewpoint_c=dew)
            idx += 1
            continue

        m = _RE_QNH.match(t)
        if m and metar.qnh_hpa is None:
            metar = _replace(metar, qnh_hpa=int(m.group("qnh")))
            idx += 1
            continue

        m = _RE_ALTIMETER.match(t)
        if m and metar.qnh_hpa is None:
            # A2992 => inches of mercury * 33.8639 => hPa (rounded)
            try:
                inhg = int(m.group("inhg")) / 100.0
                qnh = int(round(inhg * 33.8639))
                metar = _replace(metar, qnh_hpa=qnh)
            except Exception:
                pass
            idx += 1
            continue

        if t == "NOSIG":
            nosig = True
            idx += 1
            continue

        # Trend groups: treat TEMPO/BECMG as trend start, store and stop detailed parsing.
        if t in ("TEMPO", "BECMG"):
            trend_tokens.extend(tokens[idx:])
            break

        # Weather tokens: accept common shapes (+/-/VC prefixes etc.) and store raw.
        if _looks_like_weather_token(t):
            weather.append(t)
            idx += 1
            continue

        # Ignore other tokens (e.g., AUTO, COR, remarks fields before RMK).
        idx += 1

    metar = _replace(
        metar,
        wind=wind,
        visibility=vis,
        weather=weather,
        rvr=rvr,
        clouds=clouds,
        cavok=cavok,
        nosig=nosig,
        trend_tokens=trend_tokens,
    )
    return metar


def _parse_signed_int(token: str) -> Optional[int]:
    if not token or token == "//":
        return None
    t = token.strip().upper()
    neg = t.startswith("M")
    if neg:
        t = t[1:]
    try:
        v = int(t)
    except Exception:
        return None
    return -v if neg else v


_WX_CODES = {
    "DZ",
    "RA",
    "SN",
    "SG",
    "IC",
    "PL",
    "GR",
    "GS",
    "UP",
    "BR",
    "FG",
    "FU",
    "VA",
    "DU",
    "SA",
    "HZ",
    "PY",
    "PO",
    "SQ",
    "FC",
    "SS",
    "DS",
    "TS",
    "SH",
    "FZ",
    "MI",
    "BC",
    "PR",
    "DR",
    "BL",
    "VC",
}


def _looks_like_weather_token(token: str) -> bool:
    t = (token or "").strip().upper()
    if not t:
        return False
    if t == "NSW":
        return True
    # Allow prefixes
    if t.startswith(("+", "-", "VC")):
        # +RA, -SHRA, VCTS
        pass
    # Heuristic: token contains at least one known code
    for code in _WX_CODES:
        if code in t:
            return True
    return False


def _replace(m: Metar, **kwargs) -> Metar:
    return Metar(**{**m.__dict__, **kwargs})


def _parse_rvr_val(token: str) -> Optional[int]:
    t = (token or "").strip().upper()
    if not t:
        return None
    if t.startswith(("P", "M")):
        t = t[1:]
    if not t.isdigit():
        return None
    return int(t)


def _rvr_flags(token: str) -> tuple[bool, bool]:
    t = (token or "").strip().upper()
    return (t.startswith("P"), t.startswith("M"))
