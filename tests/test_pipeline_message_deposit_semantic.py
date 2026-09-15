import pytest

from src.core.pipeline_message_deposit import PipelineMessageDepositGuard


def test_semantic_draft_requires_fresh_confirmation_and_preserves_caller_words():
    guard = PipelineMessageDepositGuard()
    assert guard.decide("call-a", "The sky is blue.", enabled=True).kind == "pass"
    decision = guard.propose_caller_message(
        "call-a", {"target": "Gary", "message": "the sky is blue"},
    )
    assert decision.text == "For Gary. I have: “The sky is blue.” Is that right?"
    with pytest.raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-a", {})
    guard.decide("call-a", "Yes.", enabled=True, caller_controls=True)
    assert guard.consume_confirmed_tool_parameters("call-a", {}) == {
        "target": "Gary", "message": "The sky is blue.",
    }
    with pytest.raises(ValueError, match="already_consumed"):
        guard.consume_confirmed_tool_parameters("call-a", {})


@pytest.mark.parametrize("utterance", ["Take a memo.", "Please take down a note.", "I need to dictate something."])
def test_semantic_start_needs_no_literal_trigger_or_ai_after_selection(utterance):
    guard = PipelineMessageDepositGuard()
    guard.decide("call-a", utterance, enabled=True)
    draft = guard.propose_caller_message("call-a", {"target": "Gary", "message": ""})
    assert draft.kind == "speak"
    assert guard.snapshot("call-a")["phase"] == "awaiting_message"
    with pytest.raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-a", {})
    readback = guard.decide("call-a", "The sky is blue.", enabled=True)
    assert readback.text == "For Gary. I have: “The sky is blue.” Is that right?"
    guard.decide("call-a", "Yes.", enabled=True)
    assert guard.consume_confirmed_tool_parameters("call-a", {})["message"] == "The sky is blue."


@pytest.mark.parametrize("utterance", ["Yes.", "Okay, thanks.", "No.", "Cancel."])
def test_courtesy_or_cancellation_cannot_become_semantic_draft(utterance):
    guard = PipelineMessageDepositGuard()
    guard.decide("call-a", utterance, enabled=True)
    with pytest.raises(ValueError, match="no_caller_draft"):
        guard.propose_caller_message("call-a", {"target": "Gary", "message": utterance})
    assert guard.snapshot("call-a") is None


def test_semantic_draft_rejects_cross_call_stale_and_rewritten_words():
    now = [100.0]
    guard = PipelineMessageDepositGuard(clock=lambda: now[0])
    guard.decide("call-a", "The sky is blue.", enabled=True)
    with pytest.raises(ValueError, match="no_caller_draft"):
        guard.propose_caller_message("call-b", {"target": "Gary", "message": "The sky is blue."})
    with pytest.raises(ValueError, match="draft_mismatch"):
        guard.propose_caller_message("call-a", {"target": "Gary", "message": "The sky is green."})
    now[0] += 181
    with pytest.raises(ValueError, match="draft_expired"):
        guard.propose_caller_message("call-a", {"target": "Gary", "message": "The sky is blue."})
    guard.cleanup("call-a")
    with pytest.raises(ValueError, match="no_caller_draft"):
        guard.propose_caller_message("call-a", {"target": "Gary", "message": "The sky is blue."})


def test_semantic_draft_cannot_replace_pending_confirmed_or_inflight_attempt():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-a", "Leave a message for Gary.", enabled=True)
    guard.decide("call-a", "The sky is blue.", enabled=True)
    assert guard.propose_caller_message("call-a", {"target": "Other", "message": "rewrite"}) is None
    guard.decide("call-a", "Yes.", enabled=True)
    assert guard.propose_caller_message("call-a", {"target": "Other", "message": "rewrite"}) is None
    assert guard.consume_confirmed_tool_parameters("call-a", {})["message"] == "The sky is blue."
    assert guard.propose_caller_message("call-a", {"target": "Other", "message": "rewrite"}) is None


def test_semantic_recipient_stays_audible_through_correction_and_cancel():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-a", "Take a memo.", enabled=True)
    prompt = guard.propose_caller_message("call-a", {"target": "Gary", "message": ""})
    assert "for Gary" in prompt.text
    guard.decide("call-a", "The sky is blue.", enabled=True)
    corrected = guard.decide("call-a", "Change it to the sky is green.", enabled=True)
    assert corrected.text == "For Gary. I have: “the sky is green.” Is that right?"
    guard.decide("call-a", "Cancel.", enabled=True)
    assert guard.snapshot("call-a") is None
    with pytest.raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-a", {})
