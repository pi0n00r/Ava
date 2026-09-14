# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava

from src.core.pipeline_message_deposit import PipelineMessageDepositGuard


def test_exact_failed_call_timeline_gets_readback_and_one_success_acknowledgement():
    guard = PipelineMessageDepositGuard()

    ask = guard.decide(
        "1786902401.184",
        "I'd like to leave a message for Gary, please.",
        enabled=True,
    )
    assert ask.kind == "speak"
    assert ask.text == "Of course. What would you like me to tell Gary?"

    readback = guard.decide(
        "1786902401.184",
        "The sky is blue.",
        enabled=True,
    )
    assert readback.kind == "speak"
    assert readback.text == "I have: “The sky is blue.” Is that right?"

    confirmed = guard.decide("1786902401.184", "Yes.", enabled=True)
    assert confirmed.kind == "pass"
    assert guard.snapshot("1786902401.184") == {
        "phase": "depositing",
        "has_target": True,
        "has_message": True,
    }

    assert guard.consume_confirmed_tool_parameters(
        "1786902401.184",
        {"target": "wrong", "message": "wrong"},
    ) == {"target": "Gary", "message": "The sky is blue."}
    guard.note_tool_result("1786902401.184", success=True)
    residual = guard.decide("1786902401.184", "Yes please.", enabled=True)
    assert residual.kind == "suppress"
    assert guard.snapshot("1786902401.184")["phase"] == "acknowledged"


def test_recipient_is_generic_and_explicit_new_message_survives_terminal_state():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-1", "Could I leave a message for Priya, please?", enabled=True)
    guard.decide("call-1", "Please call tomorrow.", enabled=True)
    guard.decide("call-1", "Yeah I would.", enabled=True)
    guard.consume_confirmed_tool_parameters("call-1", {})
    guard.note_tool_result("call-1", success=True)

    decision = guard.decide(
        "call-1",
        "I'd like to leave a message for Dr. O'Connor-Smythe, please.",
        enabled=True,
    )
    assert decision.kind == "speak"
    assert decision.text == (
        "Of course. What would you like me to tell Dr. O'Connor-Smythe?"
    )
    assert guard.snapshot("call-1") == {
        "phase": "awaiting_message",
        "has_target": True,
        "has_message": False,
    }


def test_no_requests_a_correction_and_cancel_does_not_deposit():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-2", "Leave a message for Priya.", enabled=True)
    guard.decide("call-2", "Call tomorrow.", enabled=True)

    correction = guard.decide("call-2", "No thanks.", enabled=True)
    assert correction.kind == "speak"
    assert correction.text == "All right. What would you like me to say instead?"
    assert guard.snapshot("call-2")["phase"] == "awaiting_message"

    cancelled = guard.decide("call-2", "Never mind.", enabled=True)
    assert cancelled.kind == "speak"
    assert cancelled.text == "Of course."
    assert guard.snapshot("call-2") is None


def test_ambiguous_confirmation_neither_deposits_nor_restarts_the_message():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-3", "Take a message to Geary.", enabled=True)
    guard.decide("call-3", "The sky is blue.", enabled=True)

    decision = guard.decide("call-3", "Maybe later.", enabled=True)
    assert decision.kind == "speak"
    assert decision.text == "Was that right? You can say yes, or tell me what to change."
    assert guard.snapshot("call-3")["phase"] == "awaiting_confirmation"


def test_message_is_bounded_and_caller_quotes_cannot_break_the_readback():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-4", "Give a message to Alex.", enabled=True)

    too_long = guard.decide("call-4", "x" * 301, enabled=True)
    assert too_long.kind == "speak"
    assert "too long" in too_long.text
    assert guard.snapshot("call-4")["has_message"] is False

    readback = guard.decide(
        "call-4",
        "He said “hello”\nthen left.",
        enabled=True,
    )
    assert readback.text == 'I have: “He said "hello" then left.” Is that right?'


def test_failure_returns_to_confirmation_and_cleanup_discards_payload():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-5", "Leave a message for Alex.", enabled=True)
    guard.decide("call-5", "Call tomorrow.", enabled=True)
    guard.decide("call-5", "Yes please.", enabled=True)

    guard.consume_confirmed_tool_parameters("call-5", {})
    guard.note_tool_result("call-5", success=False)
    assert guard.snapshot("call-5")["phase"] == "awaiting_confirmation"

    guard.cleanup("call-5")
    assert guard.snapshot("call-5") is None


def test_disabled_tool_does_not_intercept_ordinary_conversation():
    guard = PipelineMessageDepositGuard()
    decision = guard.decide(
        "call-6",
        "I'd like to leave a message for Alex.",
        enabled=False,
    )
    assert decision.kind == "pass"
    assert guard.snapshot("call-6") is None


def test_confirmed_payload_is_one_use_and_model_values_cannot_rewrite_it():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-bind", "Leave a message for Gary.", enabled=True)
    guard.decide("call-bind", "The sky is blue.", enabled=True)
    guard.decide("call-bind", "Yes.", enabled=True)

    assert guard.consume_confirmed_tool_parameters(
        "call-bind",
        {"target": "Geary", "message": "The sky is green.", "extra": "drop me"},
    ) == {"target": "Gary", "message": "The sky is blue."}

    try:
        guard.consume_confirmed_tool_parameters("call-bind", {})
    except ValueError as exc:
        assert str(exc) == "message_deposit_not_confirmed_or_already_consumed"
    else:
        raise AssertionError("duplicate confirmed deposit was not rejected")


def test_deposit_without_confirmed_state_fails_closed():
    guard = PipelineMessageDepositGuard()
    try:
        guard.consume_confirmed_tool_parameters(
            "missing",
            {"target": "Gary", "message": "The sky is blue."},
        )
    except ValueError as exc:
        assert str(exc) == "message_deposit_not_confirmed_or_already_consumed"
    else:
        raise AssertionError("unconfirmed deposit was not rejected")


def test_confirmed_deposit_execution_window_expires_before_consume():
    now = [200.0]
    guard = PipelineMessageDepositGuard(
        clock=lambda: now[0],
        confirmed_execution_window_sec=5.0,
    )
    guard.decide("call-confirm-expiry", "Leave a message for Gary.", enabled=True)
    guard.decide("call-confirm-expiry", "The sky is blue.", enabled=True)
    guard.decide("call-confirm-expiry", "Yes.", enabled=True)

    now[0] = 205.01
    try:
        guard.consume_confirmed_tool_parameters("call-confirm-expiry", {})
    except ValueError as exc:
        assert str(exc) == "message_deposit_confirmation_expired"
    else:
        raise AssertionError("expired confirmation was consumed")
    assert guard.snapshot("call-confirm-expiry")["phase"] == "awaiting_confirmation"


def test_substantive_turn_revokes_unspent_deposit_confirmation():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-intervening", "Leave a message for Gary.", enabled=True)
    guard.decide("call-intervening", "The sky is blue.", enabled=True)
    guard.decide("call-intervening", "Yes.", enabled=True)

    decision = guard.decide(
        "call-intervening",
        "Actually, what time is it?",
        enabled=True,
    )
    assert decision.kind == "pass"
    assert guard.snapshot("call-intervening")["phase"] == "awaiting_confirmation"
    try:
        guard.consume_confirmed_tool_parameters("call-intervening", {})
    except ValueError as exc:
        assert str(exc) == "message_deposit_not_confirmed_or_already_consumed"
    else:
        raise AssertionError("revoked confirmation was consumed")


def test_residual_affirmation_suppression_expires_monotonically():
    now = [100.0]
    guard = PipelineMessageDepositGuard(
        clock=lambda: now[0],
        residual_affirmation_window_sec=1.25,
    )
    guard.decide("call-expiry", "Leave a message for Gary.", enabled=True)
    guard.decide("call-expiry", "The sky is blue.", enabled=True)
    guard.decide("call-expiry", "Yes.", enabled=True)
    guard.consume_confirmed_tool_parameters("call-expiry", {})
    guard.note_tool_result("call-expiry", success=True)

    now[0] = 101.0
    assert guard.decide("call-expiry", "Yes please.", enabled=True).kind == "suppress"
    now[0] = 101.26
    assert guard.decide("call-expiry", "Yes please.", enabled=True).kind == "pass"
    assert guard.snapshot("call-expiry") is None


def test_explicit_dictation_preserves_a_short_polite_message():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-polite-content", "Leave a message.", enabled=True)
    guard.decide("call-polite-content", "Gary.", enabled=True)

    readback = guard.decide(
        "call-polite-content",
        "Tell Gary: Okay thanks",
        enabled=True,
    )
    assert readback.text == "I have: “Okay thanks” Is that right?"
    guard.decide("call-polite-content", "Yes.", enabled=True)
    assert guard.consume_confirmed_tool_parameters("call-polite-content", {}) == {
        "target": "Gary",
        "message": "Okay thanks",
    }


def test_optional_structured_default_target_skips_only_the_recipient_question():
    guard = PipelineMessageDepositGuard()
    ask = guard.decide(
        "call-default",
        "I like to leave a message.",
        enabled=True,
        default_target="Gary",
    )
    assert ask.text == "Of course. What would you like me to tell Gary?"
    assert guard.snapshot("call-default") == {
        "phase": "awaiting_message",
        "has_target": True,
        "has_message": False,
    }


def test_observed_truncated_request_is_exact_and_does_not_broaden_message_intent():
    guard = PipelineMessageDepositGuard()

    observed = guard.decide(
        "call-observed-asr",
        "elect to leave a message.",
        enabled=True,
        default_target="Gary",
    )
    assert observed.text == "Of course. What would you like me to tell Gary?"
    assert guard.snapshot("call-observed-asr") == {
        "phase": "awaiting_message",
        "has_target": True,
        "has_message": False,
    }

    for value in (
        "elect to leave this message",
        "select a message",
        "message recorded",
    ):
        assert guard.decide(f"near-miss-{value}", value, enabled=True).kind == "pass"


def test_targetless_intent_without_default_asks_for_recipient_first():
    guard = PipelineMessageDepositGuard()

    who = guard.decide("call-who", "May I leave a message please?", enabled=True)
    assert who.text == "Of course. Who would you like me to leave the message for?"
    assert guard.snapshot("call-who")["phase"] == "awaiting_target"

    assert guard.decide("call-who", "Okay.", enabled=True).text == (
        "Who would you like me to leave the message for?"
    )
    target = guard.decide("call-who", "For Priya, please.", enabled=True)
    assert target.text == "What would you like me to tell Priya?"
    assert guard.snapshot("call-who")["phase"] == "awaiting_message"


def test_full_named_request_while_awaiting_target_or_content_rebinds_recipient():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-rebind", "Leave a message.", enabled=True)

    named = guard.decide(
        "call-rebind",
        "I'd like to leave a message for Gary, please.",
        enabled=True,
    )
    assert named.text == "What would you like me to tell Gary?"

    replacement = guard.decide(
        "call-rebind",
        "Actually, leave a message for Priya.",
        enabled=True,
    )
    assert replacement.text == "What would you like me to tell Priya?"
    assert guard.snapshot("call-rebind") == {
        "phase": "awaiting_message",
        "has_target": True,
        "has_message": False,
    }
    readback = guard.decide("call-rebind", "Call tomorrow.", enabled=True)
    assert readback.text == "I have: “Call tomorrow.” Is that right?"


def test_explicit_correction_replaces_content_and_requires_fresh_confirmation():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-correction", "Leave a message for Gary.", enabled=True)
    guard.decide("call-correction", "The sky is green.", enabled=True)

    correction = guard.decide(
        "call-correction",
        "No, change it to the sky is blue.",
        enabled=True,
    )
    assert correction.text == "I have: “the sky is blue.” Is that right?"
    assert guard.snapshot("call-correction")["phase"] == "awaiting_confirmation"
    with __import__("pytest").raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-correction", {})

    guard.decide("call-correction", "Yes.", enabled=True)
    assert guard.consume_confirmed_tool_parameters("call-correction", {}) == {
        "target": "Gary",
        "message": "the sky is blue.",
    }


def test_targetless_state_is_call_scoped_and_cleanup_revokes_it():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-a", "Leave a message.", enabled=True)
    assert guard.decide("call-b", "The sky is blue.", enabled=True).kind == "pass"
    assert guard.snapshot("call-b") is None
    guard.cleanup("call-a")
    assert guard.snapshot("call-a") is None


def test_explicit_that_message_reference_uses_only_fresh_same_call_prior_words():
    now = [100.0]
    guard = PipelineMessageDepositGuard(clock=lambda: now[0])
    assert guard.decide(
        "call-reference", "The blue notebook is on the desk.", enabled=True,
    ).kind == "pass"

    readback = guard.decide(
        "call-reference", "Leave that message for Gary.", enabled=True,
    )
    assert readback.text == "I have: “The blue notebook is on the desk.” Is that right?"
    with __import__("pytest").raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-reference", {})

    guard.decide("call-reference", "Yes.", enabled=True)
    assert guard.consume_confirmed_tool_parameters("call-reference", {}) == {
        "target": "Gary",
        "message": "The blue notebook is on the desk.",
    }


def test_that_message_without_safe_prior_asks_for_content_and_never_uses_courtesy():
    guard = PipelineMessageDepositGuard()
    missing = guard.decide(
        "call-missing", "Leave that message for Gary.", enabled=True,
    )
    assert missing.text == "What would you like me to tell Gary?"
    assert guard.snapshot("call-missing") == {
        "phase": "awaiting_message", "has_target": True, "has_message": False,
    }

    assert guard.decide("call-courtesy", "Okay thanks.", enabled=True).kind == "pass"
    courtesy = guard.decide(
        "call-courtesy", "Leave that message for Gary.", enabled=True,
    )
    assert courtesy.text == "What would you like me to tell Gary?"
    assert guard.snapshot("call-courtesy")["has_message"] is False


def test_generic_request_never_reuses_prior_words_and_explicit_dictation_still_works():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-generic", "The blue notebook is on the desk.", enabled=True)
    ask = guard.decide("call-generic", "Leave a message for Gary.", enabled=True)
    assert ask.text == "Of course. What would you like me to tell Gary?"
    assert guard.snapshot("call-generic")["has_message"] is False

    readback = guard.decide(
        "call-generic", "Tell Gary: Okay thanks", enabled=True,
    )
    assert readback.text == "I have: “Okay thanks” Is that right?"


def test_prior_reference_is_call_scoped_expires_and_cleanup_revokes_it():
    now = [100.0]
    guard = PipelineMessageDepositGuard(
        clock=lambda: now[0], prior_utterance_window_sec=180.0,
    )
    guard.decide("call-a", "The blue notebook is on the desk.", enabled=True)
    cross_call = guard.decide("call-b", "Leave that message for Gary.", enabled=True)
    assert cross_call.text == "What would you like me to tell Gary?"
    assert guard.snapshot("call-b")["has_message"] is False

    now[0] = 280.01
    stale = guard.decide("call-a", "Leave that message for Gary.", enabled=True)
    assert stale.text == "What would you like me to tell Gary?"
    assert guard.snapshot("call-a")["has_message"] is False

    guard.decide("call-cleanup", "Call me tomorrow.", enabled=True)
    guard.cleanup("call-cleanup")
    cleaned = guard.decide(
        "call-cleanup", "Leave that message for Gary.", enabled=True,
    )
    assert cleaned.text == "What would you like me to tell Gary?"


def test_referenced_message_correction_and_cancel_preserve_existing_guards():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-edit", "The blue notebook is on the desk.", enabled=True)
    guard.decide("call-edit", "Leave that message for Gary.", enabled=True)

    corrected = guard.decide(
        "call-edit", "No, change it to the green notebook is on the desk.", enabled=True,
    )
    assert corrected.text == "I have: “the green notebook is on the desk.” Is that right?"
    with __import__("pytest").raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-edit", {})
    cancelled = guard.decide("call-edit", "Never mind.", enabled=True)
    assert cancelled.text == "Of course."
    assert guard.snapshot("call-edit") is None


def test_disabled_guard_keeps_ext7_ordinary_and_forgets_prior_candidate():
    guard = PipelineMessageDepositGuard()
    assert guard.decide(
        "ext7-call", "The blue notebook is on the desk.", enabled=False,
    ).kind == "pass"
    assert guard.decide(
        "ext7-call", "Leave that message for Gary.", enabled=False,
    ).kind == "pass"
    assert guard.snapshot("ext7-call") is None

    enabled_later = guard.decide(
        "ext7-call", "Leave that message for Gary.", enabled=True,
    )
    assert enabled_later.text == "What would you like me to tell Gary?"


def test_referenced_request_after_acknowledgement_requires_fresh_content():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-ack", "Leave a message for Gary.", enabled=True)
    guard.decide("call-ack", "The sky is blue.", enabled=True)
    guard.decide("call-ack", "Yes.", enabled=True)
    guard.consume_confirmed_tool_parameters("call-ack", {})
    guard.note_tool_result("call-ack", success=True)

    ask = guard.decide(
        "call-ack", "Leave that message for Priya.", enabled=True,
    )
    assert ask.text == "What would you like me to tell Priya?"
    assert guard.snapshot("call-ack") == {
        "phase": "awaiting_message", "has_target": True, "has_message": False,
    }


def test_referenced_request_while_awaiting_message_changes_only_recipient():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-wait-message", "Leave a message for Gary.", enabled=True)

    ask = guard.decide(
        "call-wait-message", "Leave that message for Priya.", enabled=True,
    )
    assert ask.text == "What would you like me to tell Priya?"
    assert guard.snapshot("call-wait-message") == {
        "phase": "awaiting_message", "has_target": True, "has_message": False,
    }
    readback = guard.decide(
        "call-wait-message", "The meeting starts at four.", enabled=True,
    )
    assert readback.text == "I have: “The meeting starts at four.” Is that right?"


def test_referenced_request_while_awaiting_target_sets_target_but_not_payload():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-wait-target", "Leave a message.", enabled=True)

    ask = guard.decide(
        "call-wait-target", "Leave that message for Priya.", enabled=True,
    )
    assert ask.text == "What would you like me to tell Priya?"
    assert guard.snapshot("call-wait-target") == {
        "phase": "awaiting_message", "has_target": True, "has_message": False,
    }


def test_referenced_request_while_awaiting_confirmation_is_not_consent():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-wait-confirm", "Leave a message for Gary.", enabled=True)
    guard.decide("call-wait-confirm", "The sky is blue.", enabled=True)

    ask = guard.decide(
        "call-wait-confirm", "Leave that message for Priya.",
        enabled=True,
        caller_controls=True,
    )
    assert ask.text == "What would you like me to tell Priya?"
    assert guard.snapshot("call-wait-confirm") == {
        "phase": "awaiting_message", "has_target": True, "has_message": False,
    }
    with __import__("pytest").raises(ValueError, match="not_confirmed"):
        guard.consume_confirmed_tool_parameters("call-wait-confirm", {})


def test_referenced_request_does_not_change_executing_or_reconciling_attempt():
    guard = PipelineMessageDepositGuard()
    guard.decide("call-uncertain", "Leave a message for Gary.", enabled=True)
    guard.decide("call-uncertain", "The sky is blue.", enabled=True)
    guard.decide("call-uncertain", "Yes.", enabled=True)
    guard.consume_confirmed_tool_parameters("call-uncertain", {})

    assert guard.decide(
        "call-uncertain", "Leave that message for Priya.", enabled=True,
    ).kind == "pass"
    assert guard.snapshot("call-uncertain")["phase"] == "executing"
    guard.note_tool_result(
        "call-uncertain", success=False, native_outcome="unknown", dispatch_generation=1,
    )
    assert guard.snapshot("call-uncertain")["phase"] == "reconciling"
    assert guard.decide(
        "call-uncertain", "Leave that message for Priya.", enabled=True,
    ).kind == "pass"
    assert guard.snapshot("call-uncertain")["phase"] == "reconciling"
