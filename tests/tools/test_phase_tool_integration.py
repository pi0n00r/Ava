"""
Integration tests for pre-call and post-call tool execution flow.

Tests the coordination between tool registry, contexts, and execution.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import json

from src.tools.base import ToolPhase, ToolCategory, ToolDefinition, PreCallTool, PostCallTool
from src.tools.context import PreCallContext, PostCallContext
from src.tools.http.generic_lookup import GenericHTTPLookupTool, HTTPLookupConfig, create_http_lookup_tool
from src.tools.http.generic_webhook import GenericWebhookTool, WebhookConfig, create_webhook_tool


def _make_content(chunks):
    class _Content:
        def __init__(self, parts):
            self._parts = list(parts)

        async def iter_chunked(self, _size):
            for part in self._parts:
                yield part

    return _Content(chunks)


# --- Pre-Call Tool Execution Integration ---

class TestPreCallToolExecution:
    """Integration tests for pre-call tool execution flow."""
    
    @pytest.fixture
    def precall_context(self):
        """Context for pre-call tool execution."""
        return PreCallContext(
            call_id="integration_test_call",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="Integration Test",
            context_name="test_context",
        )
    
    @pytest.fixture
    def mock_http_response(self):
        """Mock successful HTTP response."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": "100"}
        payload = {
            "firstName": "John",
            "lastName": "Doe",
            "email": "john@test.com",
            "company": "Test Corp",
        }
        mock_response.content = _make_content([json.dumps(payload).encode("utf-8")])
        mock_response.charset = "utf-8"
        return mock_response
    
    @pytest.mark.asyncio
    async def test_precall_tool_returns_output_variables(self, precall_context, mock_http_response):
        """Test that pre-call tool returns output variables for prompt injection."""
        tool = create_http_lookup_tool("crm_lookup", {
            "enabled": True,
            "url": "https://api.example.com/contacts",
            "output_variables": {
                "customer_name": "firstName",
                "customer_email": "email",
            },
        })
        
        # Setup mock
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_http_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute(precall_context)
        
        # Verify output variables match definition
        assert set(result.keys()) == set(tool.definition.output_variables)
        assert result["customer_name"] == "John"
        assert result["customer_email"] == "john@test.com"
    
    @pytest.mark.asyncio
    async def test_precall_tool_timeout_returns_empty(self, precall_context):
        """Test that timeout returns empty values gracefully."""
        tool = create_http_lookup_tool("slow_lookup", {
            "enabled": True,
            "url": "https://slow.example.com/lookup",
            "timeout_ms": 100,  # Very short timeout
            "output_variables": {
                "customer_name": "name",
            },
        })
        
        # Simulate timeout
        import aiohttp
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("Timeout"))
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute(precall_context)
        
        # Should return empty string, not crash
        assert result == {"customer_name": ""}
    
    @pytest.mark.asyncio
    async def test_multiple_precall_tools_execute_independently(self, precall_context, mock_http_response):
        """Test that multiple pre-call tools can execute independently."""
        tool1 = create_http_lookup_tool("lookup1", {
            "enabled": True,
            "url": "https://api1.example.com",
            "output_variables": {"var1": "firstName"},
        })
        
        tool2 = create_http_lookup_tool("lookup2", {
            "enabled": True,
            "url": "https://api2.example.com",
            "output_variables": {"var2": "email"},
        })
        
        # Setup mock
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_http_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result1 = await tool1.execute(precall_context)
            result2 = await tool2.execute(precall_context)
        
        # Each tool returns its own output variables
        assert "var1" in result1
        assert "var2" in result2
        assert result1["var1"] == "John"
        assert result2["var2"] == "john@test.com"


# --- Post-Call Tool Execution Integration ---

class TestPostCallToolExecution:
    """Integration tests for post-call tool execution flow."""
    
    @pytest.fixture
    def postcall_context(self):
        """Context for post-call tool execution."""
        return PostCallContext(
            call_id="integration_test_call",
            caller_number="+1234567890",
            called_number="+0987654321",
            caller_name="Integration Test",
            context_name="test_context",
            provider="deepgram",
            call_duration_seconds=120,
            call_outcome="completed",
            call_start_time="2024-01-01T10:00:00Z",
            call_end_time="2024-01-01T10:02:00Z",
            conversation_history=[
                {"role": "user", "content": "Hello, I need help"},
                {"role": "assistant", "content": "Hi! How can I help you today?"},
            ],
        )
    
    @pytest.mark.asyncio
    async def test_postcall_webhook_sends_payload(self, postcall_context):
        """Test that post-call webhook sends correct payload."""
        tool = create_webhook_tool("test_webhook", {
            "enabled": True,
            "url": "https://webhook.example.com/call",
            "payload_template": '{"id": "{call_id}", "duration": {call_duration}}',
        })
        
        captured_data = None
        
        mock_response = AsyncMock()
        mock_response.status = 200
        
        # Create proper async context manager for request
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        def capture_request(*_args, **kwargs):
            nonlocal captured_data
            captured_data = kwargs.get("data")
            return mock_request_cm
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(side_effect=capture_request)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            await tool.execute(postcall_context)
        
        # Verify payload was sent
        assert captured_data is not None
        import json
        payload = json.loads(captured_data)
        assert payload["id"] == "integration_test_call"
        assert payload["duration"] == 120
    
    @pytest.mark.asyncio
    async def test_postcall_webhook_failure_does_not_raise(self, postcall_context):
        """Test that webhook failure doesn't raise (fire-and-forget)."""
        tool = create_webhook_tool("failing_webhook", {
            "enabled": True,
            "url": "https://failing.example.com/webhook",
        })
        
        # Simulate network error
        import aiohttp
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            # Should not raise
            await tool.execute(postcall_context)
    
    @pytest.mark.asyncio
    async def test_postcall_context_has_all_required_data(self, postcall_context):
        """Test that PostCallContext contains all data needed for webhooks."""
        payload = postcall_context.to_payload_dict()
        
        # Required fields for external systems
        required_fields = [
            "call_id",
            "caller_number",
            "called_number",
            "caller_name",
            "context_name",
            "provider",
            "call_duration",
            "call_outcome",
            "call_start_time",
            "call_end_time",
            "transcript_json",
        ]
        
        for field in required_fields:
            assert field in payload, f"Missing required field: {field}"
    
    @pytest.mark.asyncio
    async def test_global_postcall_tool_definition(self):
        """Test that global post-call tools are properly configured."""
        tool = create_webhook_tool("global_webhook", {
            "enabled": True,
            "is_global": True,
            "url": "https://webhook.example.com/global",
        })
        
        assert tool.definition.is_global is True
        assert tool.definition.phase == ToolPhase.POST_CALL


# --- Tool Registry Integration ---

class TestToolRegistryIntegration:
    """Tests for tool registration and filtering by phase."""
    
    def test_tool_phases_are_distinct(self):
        """Test that tool phases are properly distinguished."""
        precall_tool = create_http_lookup_tool("precall", {"output_variables": {"x": "y"}})
        postcall_tool = create_webhook_tool("postcall", {"url": "https://test.com"})
        
        assert precall_tool.definition.phase == ToolPhase.PRE_CALL
        assert postcall_tool.definition.phase == ToolPhase.POST_CALL
        assert precall_tool.definition.phase != postcall_tool.definition.phase
    
    def test_tool_categories_for_http_tools(self):
        """Test that HTTP tools have BUSINESS category."""
        lookup = create_http_lookup_tool("lookup", {})
        webhook = create_webhook_tool("webhook", {})
        
        assert lookup.definition.category == ToolCategory.BUSINESS
        assert webhook.definition.category == ToolCategory.BUSINESS
    
    def test_tool_timeout_configuration(self):
        """Test that tools respect timeout configuration."""
        lookup = create_http_lookup_tool("lookup", {"timeout_ms": 3000})
        webhook = create_webhook_tool("webhook", {"timeout_ms": 10000})
        
        assert lookup.definition.timeout_ms == 3000
        assert webhook.definition.timeout_ms == 10000


# --- Variable Injection Integration ---

class TestVariableInjectionIntegration:
    """Tests for pre-call output variable injection flow."""
    
    def test_output_variables_format_for_prompt_injection(self):
        """Test that output variables are in correct format for prompt injection."""
        tool = create_http_lookup_tool("crm_lookup", {
            "output_variables": {
                "customer_name": "contact.firstName",
                "customer_email": "contact.email",
                "account_type": "account.type",
            },
        })
        
        # Output variables should be in definition
        assert "customer_name" in tool.definition.output_variables
        assert "customer_email" in tool.definition.output_variables
        assert "account_type" in tool.definition.output_variables
    
    @pytest.mark.asyncio
    async def test_missing_data_returns_empty_string(self):
        """Test that missing API data returns empty string (not None)."""
        tool = create_http_lookup_tool("partial_lookup", {
            "enabled": True,
            "url": "https://api.example.com/partial",
            "output_variables": {
                "name": "firstName",
                "email": "nonexistent.field",  # Will be missing
            },
        })
        
        # Mock response with partial data
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}
        payload = {"firstName": "John"}
        mock_response.content = _make_content([json.dumps(payload).encode("utf-8")])
        mock_response.charset = "utf-8"
        
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_request_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_request_cm)
        
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)
        
        context = PreCallContext(
            call_id="test",
            caller_number="+1234567890",
        )
        
        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            result = await tool.execute(context)
        
        assert result["name"] == "John"
        assert result["email"] == ""  # Empty string, not None
        assert isinstance(result["email"], str)
