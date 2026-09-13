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
