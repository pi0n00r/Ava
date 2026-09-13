import pytest


@pytest.mark.unit
def test_tool_registry_filtered_schema():
    from src.tools.base import Tool, ToolCategory, ToolDefinition
    from src.tools.registry import tool_registry

    class ToolA(Tool):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name="tool_a",
                description="A",
                category=ToolCategory.BUSINESS,
            )

        async def execute(self, parameters, context):
            return {"status": "success"}

    class ToolB(Tool):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name="tool_b",
                description="B",
                category=ToolCategory.BUSINESS,
            )

        async def execute(self, parameters, context):
            return {"status": "success"}

    tool_registry.clear()
    tool_registry.register(ToolA)
    tool_registry.register(ToolB)

    schemas = tool_registry.to_openai_realtime_schema_filtered(["tool_b"])
    assert len(schemas) == 1
    assert schemas[0]["name"] == "tool_b"

    tool_registry.clear()

