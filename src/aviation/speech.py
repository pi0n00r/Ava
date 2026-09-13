from __future__ import annotations

from dataclasses import dataclass


_NUM_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "one zero",
    11: "one one",
    12: "one two",
    13: "one three",
    14: "one four",
    15: "one five",
    16: "one six",
    17: "one seven",
    18: "one eight",
    19: "one nine",
}

_CARDINAL_0_19 = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}

_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}

_ICAO_PHONETIC = {
    "A": "alpha",
    "B": "bravo",
    "C": "charlie",
    "D": "delta",
    "E": "echo",
    "F": "foxtrot",
    "G": "golf",
    "H": "hotel",
    "I": "india",
    "J": "juliett",
    "K": "kilo",
    "L": "lima",
    "M": "mike",
    "N": "november",
    "O": "oscar",
    "P": "papa",
    "Q": "quebec",
    "R": "romeo",
    "S": "sierra",
    "T": "tango",
    "U": "uniform",
    "V": "victor",
    "W": "whiskey",
    "X": "x-ray",
    "Y": "yankee",
    "Z": "zulu",
}


def speak_digits(value: str) -> str:
    return " ".join(_NUM_WORDS.get(int(ch), ch) if ch.isdigit() else ch for ch in str(value))


def speak_number(value: int) -> str:
    if value < 0:
        return f"minus {speak_number(-value)}"
    if value in _NUM_WORDS and value <= 19:
        return _NUM_WORDS[value]
    return " ".join(_NUM_WORDS.get(int(ch), ch) for ch in str(value))


def speak_cardinal(value: int) -> str:
    """Small cardinal number words for natural phrases (e.g., "six hundred")."""
    if value < 0:
        return f"minus {speak_cardinal(-value)}"
    if value <= 19:
        return _CARDINAL_0_19.get(value, str(value))
    if value < 100:
        tens = (value // 10) * 10
        ones = value % 10
        if ones == 0:
            return _TENS.get(tens, str(value))
        return f"{_TENS.get(tens, str(tens))} {_CARDINAL_0_19.get(ones, str(ones))}"
    if value < 1000:
        hundreds = value // 100
        rem = value % 100
        if rem == 0:
            return f"{_CARDINAL_0_19.get(hundreds, str(hundreds))} hundred"
        return f"{_CARDINAL_0_19.get(hundreds, str(hundreds))} hundred {speak_cardinal(rem)}"
    if value < 10000:
        thousands = value // 1000
        rem = value % 1000
        if rem == 0:
            return f"{_CARDINAL_0_19.get(thousands, str(thousands))} thousand"
        return f"{_CARDINAL_0_19.get(thousands, str(thousands))} thousand {speak_cardinal(rem)}"
    return speak_digits(str(value))


def speak_feet_height(height_ft: int) -> str:
    # Cloud bases and RVR are naturally spoken as cardinal hundreds/meters.
    return speak_cardinal(int(height_ft))


def speak_metres_distance(meters: int) -> str:
    return speak_cardinal(int(meters))


def speak_hhmm_zulu(hh: int, mm: int) -> str:
    hhmm = f"{hh:02d}{mm:02d}"
    return f"{speak_digits(hhmm)} Zulu"


def speak_qnh_hpa(qnh: int) -> str:
    return f"QNH {speak_digits(f'{qnh:04d}')} hectopascals"


def speak_frequency_mhz(freq: str) -> str:
    # Expect e.g. "131.130" or "118.700"
    f = (freq or "").strip()
    if not f:
        return ""
    if "." in f:
        whole, frac = f.split(".", 1)
        frac = frac.rstrip()  # keep zeros; caller can provide full
        # preserve trailing zeros (important for spoken)
        return f"{speak_digits(whole)} decimal {speak_digits(frac)}"
    return speak_digits(f)


def speak_runway(runway: str) -> str:
    r = (runway or "").strip().upper()
    if not r:
        return ""
    # Accept "22", "04", "22L", "04R", "18C"
    suffix = ""
    if r.endswith(("L", "R", "C")):
        suffix = r[-1]
        r = r[:-1]
    try:
        n = int(r)
    except Exception:
        return runway
    n2 = f"{n:02d}"
    spoken = speak_digits(n2)
    if suffix == "L":
        spoken += " left"
    elif suffix == "R":
        spoken += " right"
    elif suffix == "C":
        spoken += " centre"
    return spoken


def speak_icao_station(station: str) -> str:
    s = (station or "").strip().upper()
    if len(s) != 4:
        return s
    parts = []
    for ch in s:
        parts.append(_ICAO_PHONETIC.get(ch, ch.lower()))
    return " ".join(parts)
