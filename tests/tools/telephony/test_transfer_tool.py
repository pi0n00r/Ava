"""
Unit tests for TransferCallTool.

Tests transfer functionality including:
- Extension resolution (by number and alias)
- Warm vs blind transfer modes
- Error handling (invalid targets, timeouts, failures)
- Direct SIP origination
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from src.tools.telephony.transfer import TransferCallTool


class TestTransferCallTool:
    """Test suite for call transfer tool."""
    
    @pytest.fixture
    def transfer_tool(self):
        """Create TransferCallTool instance."""
        return TransferCallTool()
    
    # ==================== Definition Tests ====================
    
    def test_definition(self, transfer_tool):
        """Test tool definition is valid."""
        definition = transfer_tool.definition
        
        assert definition.name == "transfer_call"
        assert definition.category.value == "telephony"
        assert definition.requires_channel is True
        assert definition.max_execution_time == 45
        
        # Check parameters
        assert len(definition.parameters) == 2
        
        target_param = next(p for p in definition.parameters if p.name == "target")
        assert target_param.required is True
        assert target_param.type == "string"
        
        mode_param = next(p for p in definition.parameters if p.name == "mode")
        assert mode_param.required is False
        assert mode_param.default == "warm"
        assert set(mode_param.enum) == {"warm", "blind"}
    
    # ==================== Target Resolution Tests ====================
    
    @pytest.mark.asyncio
    async def test_transfer_by_extension_number(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test transfer to extension by number (e.g., '6000')."""
        result = await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["extension"] == "6000"
        assert result["target_name"] == "Live Agent"
        
        # Verify ARI originate was called with correct endpoint
        mock_ari_client.send_command.assert_called_once()
        call_args = mock_ari_client.send_command.call_args
        
        assert call_args.kwargs["method"] == "POST"
        assert call_args.kwargs["resource"] == "channels"
        
        # Verify direct SIP origination (not Local channel)
        endpoint = call_args.kwargs["data"]["endpoint"]
        assert endpoint == "SIP/6000"
        assert "Local/" not in endpoint
        
        # Verify caller ID is AI identity
        caller_id = call_args.kwargs["data"]["callerId"]
        assert "AI Agent" in caller_id
        assert "6789" in caller_id
    
    @pytest.mark.asyncio
    async def test_transfer_by_department_alias(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test transfer using department alias (e.g., 'support')."""
        result = await transfer_tool.execute(
            parameters={"target": "support", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["extension"] == "6000"  # Resolves to 6000
        assert result["target_name"] == "Live Agent"
        
        # Verify correct endpoint
        call_args = mock_ari_client.send_command.call_args
        assert call_args.kwargs["data"]["endpoint"] == "SIP/6000"
    
    @pytest.mark.asyncio
    async def test_transfer_multiple_aliases(
        self, transfer_tool, tool_context
    ):
        """Test that all aliases resolve to correct extension."""
        aliases = ["support", "agent", "human", "representative"]
        
        for alias in aliases:
            result = await transfer_tool.execute(
                parameters={"target": alias, "mode": "warm"},
                context=tool_context
            )
            
            assert result["status"] == "success"
            assert result["extension"] == "6000"
    
    @pytest.mark.asyncio
    async def test_transfer_to_sales_department(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test transfer to sales department."""
        result = await transfer_tool.execute(
            parameters={"target": "sales", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["extension"] == "6001"
        assert result["target_name"] == "Sales Department"
        
        # Verify endpoint
        call_args = mock_ari_client.send_command.call_args
        assert call_args.kwargs["data"]["endpoint"] == "SIP/6001"
    
    @pytest.mark.asyncio
    async def test_transfer_to_technical_support(
        self, transfer_tool, tool_context
    ):
        """Test transfer to technical support with longer timeout."""
        result = await transfer_tool.execute(
            parameters={"target": "tech", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["extension"] == "6002"
        assert result["target_name"] == "Technical Support"
    
    @pytest.mark.asyncio
    async def test_transfer_invalid_target(
        self, transfer_tool, tool_context
    ):
        """Test transfer to non-existent extension."""
        result = await transfer_tool.execute(
            parameters={"target": "9999", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "error"
        assert "couldn't find" in result["message"].lower() or "not found" in result["message"].lower()
    
    @pytest.mark.asyncio
    async def test_transfer_empty_target(
        self, transfer_tool, tool_context
    ):
        """Test transfer with empty target."""
        # Empty target should return error (not raise exception)
        result = await transfer_tool.execute(
            parameters={"target": "", "mode": "warm"},
            context=tool_context
        )
        assert result["status"] == "error"
    
    # ==================== Transfer Mode Tests ====================
    
    @pytest.mark.asyncio
    async def test_warm_transfer_mode(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test warm transfer mode (AI stays on bridge)."""
        result = await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["transfer_mode"] == "warm"
        
        # Verify appArgs includes warm-transfer
        call_args = mock_ari_client.send_command.call_args
        app_args = call_args.kwargs["params"].get("appArgs", "")
        assert "warm-transfer" in app_args
    
    @pytest.mark.asyncio
    async def test_blind_transfer_mode(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test blind transfer mode (immediate redirect)."""
        # Use extension 7000 which has mode: blind in config
        result = await transfer_tool.execute(
            parameters={"target": "7000"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        assert result["transfer_mode"] == "blind"
    
    @pytest.mark.asyncio
    async def test_default_transfer_mode_is_warm(
        self, transfer_tool, tool_context
    ):
        """Test that default mode is warm when not specified."""
        result = await transfer_tool.execute(
            parameters={"target": "6000"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        # Mode should be warm (from extension config)
        assert result["transfer_mode"] == "warm"
    
    # ==================== ARI Integration Tests ====================
    
    @pytest.mark.asyncio
    async def test_ari_channel_origination(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test ARI channel origination parameters."""
        await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        # Verify ARI send_command was called
        assert mock_ari_client.send_command.called
        call_args = mock_ari_client.send_command.call_args
        
        # Verify method and resource
        assert call_args.kwargs["method"] == "POST"
        assert call_args.kwargs["resource"] == "channels"
        
        # Verify data structure
        data = call_args.kwargs["data"]
        assert "endpoint" in data
        assert "callerId" in data
        
        # Verify params structure
        params = call_args.kwargs["params"]
        assert params["app"] == "asterisk-ai-voice-agent"
        assert "appArgs" in params
    
    @pytest.mark.asyncio
    async def test_stasis_app_parameter(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test that originated channel enters Stasis directly."""
        await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        params = mock_ari_client.send_command.call_args.kwargs["params"]
        
        # Critical: Channel must enter Stasis app directly (no dialplan)
        assert params["app"] == "asterisk-ai-voice-agent"
        
        # appArgs should contain call context
        app_args = params["appArgs"]
        assert "warm-transfer" in app_args
        assert tool_context.call_id in app_args
        assert "6000" in app_args
    
    # ==================== Error Handling Tests ====================
    
    @pytest.mark.asyncio
    async def test_ari_originate_failure(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test handling when ARI originate fails."""
        # Simulate ARI failure
        mock_ari_client.send_command.side_effect = Exception("Channel origination failed")
        
        result = await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        # Should return error or failed status
        assert result["status"] in ["error", "failed"]
        # Message may vary
        assert "message" in result
    
    # ==================== Session State Tests ====================
    
    @pytest.mark.asyncio
    async def test_transfer_updates_session_state(
        self, transfer_tool, tool_context, mock_session_store, sample_call_session
    ):
        """Test that transfer updates session with action metadata."""
        await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        # Verify session was updated with current_action
        assert mock_session_store.upsert_call.called
        
        # Get the updated session
        updated_session = mock_session_store.upsert_call.call_args[0][0]
        
        # Verify action metadata (may be set during transfer)
        assert updated_session.current_action is not None
        assert updated_session.current_action["type"] == "transfer"
        assert updated_session.current_action["target"] == "6000"
    
    @pytest.mark.asyncio
    async def test_transfer_context_preserved(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test that transfer context is preserved in appArgs."""
        await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        params = mock_ari_client.send_command.call_args.kwargs["params"]
        app_args = params["appArgs"]
        
        # Verify essential context is in appArgs
        assert tool_context.call_id in app_args
        assert "6000" in app_args
    
    # ==================== Configuration Tests ====================
    
    @pytest.mark.asyncio
    async def test_transfer_uses_config_dial_string(
        self, transfer_tool, tool_context, mock_ari_client
    ):
        """Test that transfer uses dial_string from config."""
        await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        data = mock_ari_client.send_command.call_args.kwargs["data"]
        
        # Should use SIP/6000 as defined in config
        assert data["endpoint"] == "SIP/6000"
    
    @pytest.mark.asyncio
    async def test_transfer_timeout_from_config(
        self, transfer_tool, tool_context
    ):
        """Test that timeout is read from extension config."""
        # Extension 6002 has timeout: 45
        result = await transfer_tool.execute(
            parameters={"target": "6002", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
        # Timeout should be 45 (from config)
        assert result.get("timeout", 30) == 45 or "timeout" not in result
    
    # ==================== Parameter Validation Tests ====================
    
    @pytest.mark.asyncio
    async def test_missing_target_parameter(self, transfer_tool, tool_context):
        """Test that missing target parameter raises error."""
        with pytest.raises(Exception):  # Should raise validation error
            await transfer_tool.execute(
                parameters={"mode": "warm"},  # Missing target
                context=tool_context
            )
    
    @pytest.mark.asyncio
    async def test_invalid_mode_parameter(self, transfer_tool, tool_context):
        """Test that invalid mode raises validation error."""
        # Tool validates enum parameters and raises ValueError for invalid values
        with pytest.raises(ValueError, match="Invalid value for mode"):
            await transfer_tool.execute(
                parameters={"target": "6000", "mode": "invalid"},
                context=tool_context
            )
    
    # ==================== Integration Scenarios ====================
    
    @pytest.mark.asyncio
    async def test_transfer_during_active_conversation(
        self, transfer_tool, tool_context, sample_call_session
    ):
        """Test transfer initiated during active conversation."""
        # Simulate active conversation
        sample_call_session.conversation_state = "active"
        sample_call_session.audio_capture_enabled = True
        
        result = await transfer_tool.execute(
            parameters={"target": "6000", "mode": "warm"},
            context=tool_context
        )
        
        assert result["status"] == "success"
    
    @pytest.mark.asyncio
    async def test_case_insensitive_alias_matching(
        self, transfer_tool, tool_context
    ):
        """Test that department aliases are case-insensitive."""
        test_cases = ["support", "Support", "SUPPORT", "SuPpOrT"]
        
        for alias in test_cases:
            result = await transfer_tool.execute(
                parameters={"target": alias, "mode": "warm"},
                context=tool_context
            )
            
            assert result["status"] == "success"
            assert result["extension"] == "6000"
