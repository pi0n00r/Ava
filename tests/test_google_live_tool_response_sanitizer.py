import pytest


@pytest.mark.unit
def test_google_live_tool_response_payload_drops_large_fields():
    from src.providers.google_live import GoogleLiveProvider
    from src.config import GoogleProviderConfig

    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)

    huge = {"nested": {"x": "y" * 20000}}
    result = {
        "status": "success",
        "message": "ok",
        "data": huge,
        "mcp": {"server": "s", "tool": "t"},
    }
    payload = provider._build_tool_response_payload("mcp_tool", result)
    assert "data" not in payload
    assert "mcp" not in payload
    assert payload["status"] == "success"

