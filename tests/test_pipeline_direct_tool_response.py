from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine import Engine
from src.core.pipeline_message_deposit import PipelineMessageDepositGuard


def test_engine_binds_only_the_confirmed_one_use_deposit_payload():
    engine = object.__new__(Engine)
    engine._pipeline_message_deposit_guard_state = PipelineMessageDepositGuard()
    guard = engine._pipeline_message_deposit_guard_state
    guard.decide("call-bind", "Leave a message for Gary.", enabled=True)
    guard.decide("call-bind", "The sky is blue.", enabled=True)
    guard.decide("call-bind", "Yes.", enabled=True)

    assert engine._bind_pipeline_tool_parameters(
        "call-bind",
        "pbx_message_deposit",
        {"target": "Geary", "message": "The sky is green."},
    ) == {"target": "Gary", "message": "The sky is blue."}
    with pytest.raises(ValueError, match="already_consumed"):
        engine._bind_pipeline_tool_parameters(
            "call-bind",
            "pbx_message_deposit",
            {"target": "Gary", "message": "The sky is blue."},
        )


def test_engine_rejects_deposit_without_confirmation_and_leaves_other_tools_alone():
    engine = object.__new__(Engine)
    engine._pipeline_message_deposit_guard_state = PipelineMessageDepositGuard()
    with pytest.raises(ValueError, match="not_confirmed"):
        engine._bind_pipeline_tool_parameters(
            "missing",
            "pbx_message_deposit",
            {"target": "Gary", "message": "The sky is blue."},
        )
    ordinary = {"query": "status"}
    assert engine._bind_pipeline_tool_parameters(
        "missing", "ordinary_tool", ordinary
    ) is ordinary


@pytest.mark.asyncio
async def test_direct_tool_phrase_is_spoken_without_llm_continuation():
    engine = object.__new__(Engine)
    engine.session_store = SimpleNamespace(upsert_call=AsyncMock())
    engine._pipeline_output_allowed = MagicMock(return_value=True)
    engine._pipeline_tts_uses_streaming = MagicMock(return_value=True)
    engine._stream_pipeline_tts_text = AsyncMock()

    llm_generate = AsyncMock()
    pipeline = SimpleNamespace(
        llm_adapter=SimpleNamespace(generate=llm_generate),
        tts_options={},
    )
    session = SimpleNamespace(conversation_history=[])
    history = []
    result = {
        "status": "success",
        "_direct_response_text": "Thank you. I have your message for Gary.",
        "data": {"token": "must-not-be-spoken", "call_id": "secret-id"},
    }

    handled = await engine._maybe_speak_direct_pipeline_tool_result(
        "call-1", session, pipeline, history, result
    )

    assert handled is True
    llm_generate.assert_not_awaited()
    engine._stream_pipeline_tts_text.assert_awaited_once_with(
        "call-1", session, pipeline, "Thank you. I have your message for Gary."
    )
    assert history[-1]["content"] == "Thank you. I have your message for Gary."
    assert "must-not-be-spoken" not in history[-1]["content"]
    assert "secret-id" not in history[-1]["content"]


@pytest.mark.asyncio
async def test_default_tool_result_does_not_enter_direct_path():
    engine = object.__new__(Engine)
    engine.session_store = SimpleNamespace(upsert_call=AsyncMock())
    engine._stream_pipeline_tts_text = AsyncMock()
    llm_generate = AsyncMock()
    pipeline = SimpleNamespace(llm_adapter=SimpleNamespace(generate=llm_generate))
    history = []

    handled = await engine._maybe_speak_direct_pipeline_tool_result(
        "call-2",
        SimpleNamespace(conversation_history=[]),
        pipeline,
        history,
        {"status": "success", "message": "ordinary tool result"},
    )

    assert handled is False
    llm_generate.assert_not_awaited()
    engine._stream_pipeline_tts_text.assert_not_awaited()
    assert history == []


@pytest.mark.asyncio
async def test_successful_message_deposit_enters_terminal_acknowledgement_state():
    engine = object.__new__(Engine)
    engine.session_store = SimpleNamespace(upsert_call=AsyncMock())
    engine._pipeline_output_allowed = MagicMock(return_value=True)
    engine._pipeline_tts_uses_streaming = MagicMock(return_value=True)
    engine._stream_pipeline_tts_text = AsyncMock()
    engine._pipeline_message_deposit_guard_state = PipelineMessageDepositGuard()
    guard = engine._pipeline_message_deposit_guard_state

    guard.decide("call-deposit", "Leave a message for Priya.", enabled=True)
    guard.decide("call-deposit", "The sky is blue.", enabled=True)
    guard.decide("call-deposit", "Yes.", enabled=True)
    guard.consume_confirmed_tool_parameters("call-deposit", {})

    history = []
    handled = await engine._maybe_speak_direct_pipeline_tool_result(
        "call-deposit",
        SimpleNamespace(conversation_history=[]),
        SimpleNamespace(tts_options={}),
        history,
        {
            "status": "success",
            "_direct_response_text": "I'll make sure they get it.",
            "data": {"request_id": "must-not-be-spoken"},
        },
        tool_name="pbx_message_deposit",
    )

    assert handled is True
    assert guard.snapshot("call-deposit")["phase"] == "acknowledged"
    assert guard.decide("call-deposit", "Yeah I would.", enabled=True).kind == "suppress"
    engine._stream_pipeline_tts_text.assert_awaited_once()
    assert history[-1]["content"] == "I'll make sure they get it."
    assert "must-not-be-spoken" not in history[-1]["content"]


@pytest.mark.asyncio
async def test_failed_message_deposit_returns_to_confirmation_state():
    engine = object.__new__(Engine)
    engine.session_store = SimpleNamespace(upsert_call=AsyncMock())
    engine._pipeline_output_allowed = MagicMock(return_value=True)
    engine._pipeline_tts_uses_streaming = MagicMock(return_value=True)
    engine._stream_pipeline_tts_text = AsyncMock()
    engine._pipeline_message_deposit_guard_state = PipelineMessageDepositGuard()
    guard = engine._pipeline_message_deposit_guard_state

    guard.decide("call-failure", "Leave a message for Priya.", enabled=True)
    guard.decide("call-failure", "Please call tomorrow.", enabled=True)
    guard.decide("call-failure", "Yes please.", enabled=True)
    guard.consume_confirmed_tool_parameters("call-failure", {})

    handled = await engine._maybe_speak_direct_pipeline_tool_result(
        "call-failure",
        SimpleNamespace(conversation_history=[]),
        SimpleNamespace(tts_options={}),
        [],
        {
            "status": "error",
            "_direct_response_text": "I am sorry, I lost that at my end.",
        },
        tool_name="pbx_message_deposit",
    )

    assert handled is True
    assert guard.snapshot("call-failure")["phase"] == "awaiting_confirmation"
