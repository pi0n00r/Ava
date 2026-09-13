import pytest


@pytest.mark.unit
def test_make_exposed_tool_name_sanitizes():
    from src.mcp.naming import make_exposed_tool_name, is_provider_safe_tool_name

    name = make_exposed_tool_name("aviation-atis", "get.atis")
    assert name == "mcp_aviation_atis_get_atis"
    assert is_provider_safe_tool_name(name)


@pytest.mark.unit
def test_make_exposed_tool_name_caps_length():
    from src.mcp.naming import make_exposed_tool_name

    long_server = "s" * 200
    long_tool = "t" * 200
    name = make_exposed_tool_name(long_server, long_tool, max_len=64)
    assert len(name) <= 64

