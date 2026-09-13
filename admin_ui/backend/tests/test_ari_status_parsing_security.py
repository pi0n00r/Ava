import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import system


def test_extract_endpoint_rejects_untrusted_tech() -> None:
    tech, resource = system._extract_endpoint(
        dial_string="",
        device_state_tech="http://evil.example",
        extension_key="2765",
    )
    assert tech == ""
    assert resource == ""


def test_extract_endpoint_requires_numeric_extension() -> None:
    tech, resource = system._extract_endpoint(
        dial_string="",
        device_state_tech="pjsip",
        extension_key="2765/../../x",
    )
    assert tech == ""
    assert resource == ""


def test_extract_device_state_id_requires_allowlisted_tech_and_numeric_extension() -> None:
    assert (
        system._extract_device_state_id(
            dial_string="",
            device_state_tech="pjsip",
            extension_key="2765",
        )
        == "PJSIP/2765"
    )
    assert (
        system._extract_device_state_id(
            dial_string="",
            device_state_tech="pjsip",
            extension_key="0012",
        )
        == "PJSIP/0012"
    )
    assert (
        system._extract_device_state_id(
            dial_string="",
            device_state_tech="evil/tech",
            extension_key="2765",
        )
        == ""
    )
    assert (
        system._extract_device_state_id(
            dial_string="",
            device_state_tech="pjsip",
            extension_key="2765%2fetc",
        )
        == ""
    )
