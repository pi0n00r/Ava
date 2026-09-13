from __future__ import annotations

from src.aviation.atis import AtisExtras, generate_atis_text
from src.aviation.metar import parse_metar


def test_generate_atis_lsmp_example_phraseology() -> None:
    metar_raw = "METAR LSMP 171720Z VRB01KT 9999NDV OVC007 03/02 Q1025="
    metar = parse_metar(metar_raw)
    extras = AtisExtras(
        aerodrome_name="Payerne",
        runway_in_use="22",
        afis_frequency_mhz="131.130",
        traffic_advisory="Traffic to report five minutes prior to entering the aerodrome traffic zone",
    )
    text = generate_atis_text(metar, extras)
    assert text == "\n".join(
        [
            "Payerne automatic terminal information service.",
            "Time one seven two zero Zulu.",
            "Runway two two in use.",
            "Wind variable, one knot.",
            "Visibility ten kilometres or more, no directional variation reported.",
            "Clouds overcast seven hundred feet.",
            "Temperature three, dew point two.",
            "QNH one zero two five hectopascals.",
            "No significant weather.",
            "AFIS frequency one three one decimal one three zero.",
            "Traffic to report five minutes prior to entering the aerodrome traffic zone.",
            "This is an automatic service.",
        ]
    )


def test_parse_altimeter_a2992_converts_to_qnh_hpa() -> None:
    metar = parse_metar("METAR KJFK 171651Z 18010KT 10SM CLR 12/M01 A2992=")
    assert metar.qnh_hpa == 1013


def test_rvr_is_parsed_and_spoken() -> None:
    metar = parse_metar("METAR EGLL 171650Z 24008KT 0600 R27R/0600U FG 05/05 Q1010=")
    text = generate_atis_text(metar, AtisExtras(aerodrome_name="Heathrow"))
    assert "Runway two seven right visual range six hundred metres, increasing." in text.splitlines()


def test_missing_extras_are_explicit_not_available() -> None:
    metar = parse_metar("METAR LSMP 171720Z VRB01KT 9999NDV OVC007 03/02 Q1025=")
    text = generate_atis_text(metar, AtisExtras(aerodrome_name="Payerne", explicit_not_available=True))
    lines = text.splitlines()
    assert "Runway in use information not available." in lines
    assert "Information frequency not available." in lines
    assert "Traffic reporting instructions not available." in lines
