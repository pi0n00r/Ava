"""
Unit tests for TransferToQueueTool.

Tests queue transfer functionality including:
- Queue name resolution
- Queue status checking
- ARI operations (channel variables, continue in dialplan)
- Session state updates
- Error handling (queue not found, disabled tool)
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from src.tools.telephony.queue_transfer import TransferToQueueTool


class TestTransferToQueueTool:
    """Test suite for queue transfer tool."""
    
    @pytest.fixture
    def queue_tool(self):
        """Create TransferToQueueTool instance."""
        return TransferToQueueTool()
    
    @pytest.fixture
    def queue_enabled_config(self, tool_config):
        """Configure queue transfer tool in config."""
        if "tools" not in tool_config:
            tool_config["tools"] = {}
        
        tool_config["tools"]["transfer_to_queue"] = {
            "enabled": True,
            "queues": {
                "sales": {
                    "asterisk_queue": "sales-queue",
                    "description": "Sales team",
                    "max_wait_time": 300,
                    "announce_position": True
                },
                "support": {
                    "asterisk_queue": "tech-support-queue",
                    "description": "Technical support",
                    "max_wait_time": 600,
                    "announce_position": True
                },
                "billing": {
                    "asterisk_queue": "billing-queue",
                    "description": "Billing department",
                    "max_wait_time": 180,
                    "announce_position": False
                }
            }
        }
        return tool_config
    
    # ==================== Definition Tests ====================
    
    def test_definition(self, queue_tool):
        """Test tool definition is valid."""
        definition = queue_tool.definition
        
        assert definition.name == "transfer_to_queue"
        assert definition.category.value == "telephony"
        assert definition.requires_channel is True
        assert definition.max_execution_time == 30
        
        # Check parameters
        assert len(definition.parameters) == 1
        
        queue_param = definition.parameters[0]
        assert queue_param.name == "queue"
        assert queue_param.required is True
        assert queue_param.type == "string"
    
    # ==================== Queue Resolution Tests ====================
    
    @pytest.mark.asyncio
    async def test_transfer_to_sales_queue(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test transfer to sales queue."""
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["queue"] == "sales"
        assert result["asterisk_queue"] == "sales-queue"
        assert "Sales team" in result["message"]
        
        # Verify dialplan continuation via send_command
        mock_ari_client.send_command.assert_called_once_with(
            method="POST",
            resource=f"channels/{tool_context.caller_channel_id}/continue",
            params={
                "context": "ext-queues",
                "extension": "sales-queue",
                "priority": 1
            }
        )
    
    @pytest.mark.asyncio
    async def test_transfer_to_support_queue(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test transfer to support queue."""
        result = await queue_tool.execute(
            parameters={"queue": "support"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["queue"] == "support"
        assert result["asterisk_queue"] == "tech-support-queue"
        assert "Technical support" in result["message"]
    
    @pytest.mark.asyncio
    async def test_transfer_to_billing_queue(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test transfer to billing queue."""
        result = await queue_tool.execute(
            parameters={"queue": "billing"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["queue"] == "billing"
        assert result["asterisk_queue"] == "billing-queue"
        assert "Billing department" in result["message"]
    
    @pytest.mark.asyncio
    async def test_queue_name_case_insensitive(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test queue resolution is case-insensitive."""
        # Test different cases
        for queue_name in ["Sales", "SALES", "sAlEs"]:
            result = await queue_tool.execute(
                parameters={"queue": queue_name},
                context=tool_context
            )
            
            assert result["status"] == "success"
            assert result["queue"] == queue_name.lower()
            assert result["asterisk_queue"] == "sales-queue"
    
    @pytest.mark.asyncio
    async def test_queue_name_with_whitespace(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test queue names with leading/trailing whitespace are handled."""
        result = await queue_tool.execute(
            parameters={"queue": "  sales  "},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["queue"] == "sales"
    
    # ==================== Error Handling Tests ====================
    
    @pytest.mark.asyncio
    async def test_invalid_queue_name(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test error handling for invalid queue names."""
        result = await queue_tool.execute(
            parameters={"queue": "nonexistent"},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "couldn't find" in result["message"].lower()
        assert "nonexistent" in result["message"].lower()
        assert result["ai_should_speak"] is True
        
        # Verify no ARI operations were attempted
        mock_ari_client.send_command.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_tool_not_configured(
        self, queue_tool, tool_context, mock_ari_client
    ):
        """Test error when tool is not configured."""
        # Remove queue config
        if tool_context.config and "transfer_to_queue" in tool_context.config.get("tools", {}):
            del tool_context.config["tools"]["transfer_to_queue"]
        
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "couldn't find" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_tool_disabled(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test error when tool is disabled."""
        tool_context.config["tools"]["transfer_to_queue"]["enabled"] = False
        
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert result["status"] == "error"
    
    @pytest.mark.asyncio
    async def test_ari_client_error_on_set_variable(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test handling of ARI client errors when setting channel variable."""
        mock_ari_client.send_command.side_effect = Exception("Channel not found")
        
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "encountered an error" in result["message"].lower()
        assert result["ai_should_speak"] is True
    
    @pytest.mark.asyncio
    async def test_ari_client_error_on_continue_dialplan(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test handling of ARI client errors when continuing in dialplan."""
        mock_ari_client.send_command.side_effect = Exception("Context not found")
        
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "encountered an error" in result["message"].lower()
    
    # ==================== Session State Tests ====================
    
    @pytest.mark.asyncio
    async def test_session_state_updated(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test that session state is updated correctly."""
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        
        # Verify session state was updated
        session = await tool_context.get_session()
        assert session.transfer_state == "in_queue"
        assert session.transfer_target == "sales"
        assert session.transfer_active is True
    
    # ==================== Message Formatting Tests ====================
    
    @pytest.mark.asyncio
    async def test_message_includes_queue_description(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test that success message includes queue description."""
        result = await queue_tool.execute(
            parameters={"queue": "support"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert "Technical support" in result["message"]
    
    def test_format_queue_message_basic(self, queue_tool):
        """Test basic queue message formatting."""
        message = queue_tool._format_queue_message(
            description="Sales team",
            queue_status={},
            max_wait_time=300
        )
        
        assert "Sales team" in message
        assert "transferring" in message.lower()
    
    def test_format_queue_message_with_position(self, queue_tool):
        """Test queue message with position information."""
        message = queue_tool._format_queue_message(
            description="Sales team",
            queue_status={"position": 3},
            max_wait_time=300
        )
        
        assert "Sales team" in message
        assert "number 3" in message.lower()
    
    def test_format_queue_message_with_wait_time(self, queue_tool):
        """Test queue message with estimated wait time."""
        message = queue_tool._format_queue_message(
            description="Sales team",
            queue_status={"estimated_wait_seconds": 120},
            max_wait_time=300
        )
        
        assert "Sales team" in message
        assert "2 minute" in message.lower()
    
    def test_format_queue_message_with_position_and_wait(self, queue_tool):
        """Test queue message with both position and wait time."""
        message = queue_tool._format_queue_message(
            description="Sales team",
            queue_status={
                "position": 5,
                "estimated_wait_seconds": 300
            },
            max_wait_time=600
        )
        
        assert "Sales team" in message
        assert "number 5" in message.lower()
        assert "5 minute" in message.lower()
    
    # ==================== Configuration Tests ====================
    
    def test_resolve_queue_returns_config(
        self, queue_tool, tool_context, queue_enabled_config
    ):
        """Test that _resolve_queue returns correct configuration."""
        queue_config_result = queue_tool._resolve_queue("sales", tool_context)
        
        assert queue_config_result is not None
        assert queue_config_result["asterisk_queue"] == "sales-queue"
        assert queue_config_result["description"] == "Sales team"
        assert queue_config_result["max_wait_time"] == 300
    
    def test_resolve_queue_returns_none_for_invalid(
        self, queue_tool, tool_context, queue_enabled_config
    ):
        """Test that _resolve_queue returns None for invalid queue names."""
        result = queue_tool._resolve_queue("invalid_queue", tool_context)
        
        assert result is None
    
    # ==================== Return Value Tests ====================
    
    @pytest.mark.asyncio
    async def test_return_includes_all_fields(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test that return value includes all required fields."""
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        assert "status" in result
        assert "message" in result
        assert "queue" in result
        assert "asterisk_queue" in result
        assert "ai_should_speak" in result
        
        assert result["ai_should_speak"] is True
    
    @pytest.mark.asyncio
    async def test_return_includes_optional_fields(
        self, queue_tool, tool_context, mock_ari_client, queue_enabled_config
    ):
        """Test that return value includes optional fields."""
        result = await queue_tool.execute(
            parameters={"queue": "sales"},
            context=tool_context
        )
        
        # These can be None if queue status not available
        assert "position" in result
        assert "estimated_wait_seconds" in result
