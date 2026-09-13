import pytest


@pytest.mark.unit
def test_tooldefinition_openai_schema_uses_input_schema():
    from src.tools.base import ToolDefinition, ToolCategory

    td = ToolDefinition(
        name="mcp_test_tool",
        description="Test tool",
        category=ToolCategory.BUSINESS,
        input_schema={
            "type": "object",
            "properties": {"icao": {"type": "string", "description": "Airport code"}},
            "required": ["icao"],
        },
    )

    schema = td.to_openai_schema()
    params = schema["function"]["parameters"]
    assert params["properties"]["icao"]["type"] == "string"
    assert "icao" in params.get("required", [])


@pytest.mark.unit
def test_tooldefinition_deepgram_strips_defaults_in_input_schema():
    from src.tools.base import ToolDefinition, ToolCategory

    td = ToolDefinition(
        name="mcp_test_tool",
        description="Test tool",
        category=ToolCategory.BUSINESS,
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string", "default": "y"}},
        },
    )

    schema = td.to_deepgram_schema()
    assert "default" not in schema["parameters"]["properties"]["x"]

