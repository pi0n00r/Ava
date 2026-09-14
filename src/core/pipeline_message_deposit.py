# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava

"""Deterministic caller-facing state for the managed message-deposit tool.

The LLM remains responsible for ordinary conversation, but it must not be the
authority for the two safety-sensitive message-taking transitions: an exact
readback before confirmation and suppression of a residual affirmation after a
verified deposit.  This module is deliberately independent of a recipient
directory; it preserves the caller's target and message without hard-coded
people or extensions.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable, Dict, Literal, Mapping, Optional


_MAX_TARGET_CHARS = 120
_MAX_MESSAGE_CHARS = 300

_MESSAGE_REQUEST_RE = re.compile(
    r"(?:^|\b)(?:i(?:'d| would) like to\s+|i want to\s+|can i\s+|could i\s+)?"
    r"(?:leave|give|take)\s+(?:a\s+)?message\s+(?:for|to)\s+(?P<target>.+)$",
    re.IGNORECASE,
)
_MESSAGE_REQUEST_WITHOUT_TARGET_RE = re.compile(
    r"^(?:i(?:'d| would)?\s+like\s+to\s+|i\s+want\s+to\s+|"
    r"can\s+i\s+|could\s+i\s+|may\s+i\s+|please\s+)?"
    r"(?:leave|give|take)\s+(?:a\s+)?message"
    r"(?:\s+please)?[.!?]*$",
    re.IGNORECASE,
)
_MESSAGE_CORRECTION_RE = re.compile(
    r"^(?:(?:no[,;:]?\s*)?(?:change|make)\s+(?:it|that)\s+(?:to|say)\s+|"
    r"(?:no[,;:]?\s*)?(?:instead[,;:]?\s*)?(?:say|tell\s+(?:him|her|them))\s+)"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)
_EXPLICIT_MESSAGE_CONTENT_RE = re.compile(
    r"^(?:the\s+message\s+is|my\s+message\s+is|message)\s*[:,-]?\s*(?P<message>.+)$",
    re.IGNORECASE,
)
_TRAILING_POLITENESS_RE = re.compile(
    r"(?:[,;:]?\s*(?:please|thanks|thank you))?[.!?]*$",
    re.IGNORECASE,
)
_AFFIRMATIVE = {
    "affirmative",
    "correct",
    "do it",
    "go ahead",
    "mm hmm",
    "please do",
    "right",
    "sure",
    "that is correct",
    "that is right",
    "thats correct",
    "thats right",
    "uh huh",
    "yeah",
    "yeah i do",
    "yeah i would",
    "yeah please",
    "yep",
    "yes",
    "yes i do",
    "yes i would",
    "yes please",
    "yup",
}
_NEGATIVE = {
    "nah",
    "no",
    "no thanks",
    "no thank you",
    "nope",
    "not correct",
    "not right",
    "that is not correct",
    "that is not right",
    "thats not correct",
    "thats not right",
}
_CANCEL = {
    "cancel",
    "cancel it",
    "cancel that",
    "do not send it",
    "dont send it",
    "forget it",
    "forget that",
    "never mind",
    "nevermind",
}
_PRE_MESSAGE_ACKNOWLEDGEMENT = {
    "all right",
    "alright",
    "okay",
    "okay please",
    "okay thanks",
    "okay thank you",
    "please",
    "sure",
    "thanks",
    "thank you",
}


def _collapse_text(value: str) -> str:
    """Remove control characters and collapse whitespace without paraphrasing."""
    printable = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char for char in str(value or "")
    )
    return " ".join(printable.split()).strip()


def _intent_key(value: str) -> str:
    normalized = _collapse_text(value).lower().replace("’", "'")
    normalized = re.sub(r"[^\w']+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split()).strip().replace("'", "")


def _message_request_target(value: str) -> Optional[str]:
    text = _collapse_text(value)
    match = _MESSAGE_REQUEST_RE.search(text)
    if not match:
        return None
    target = _TRAILING_POLITENESS_RE.sub("", match.group("target")).strip(" \t,;:!?.")
    target = _collapse_text(target)
    if not target or len(target) > _MAX_TARGET_CHARS:
        return None
    return target


def _is_message_request_without_target(value: str) -> bool:
    return bool(_MESSAGE_REQUEST_WITHOUT_TARGET_RE.fullmatch(_collapse_text(value)))


def _message_correction(value: str) -> Optional[str]:
    match = _MESSAGE_CORRECTION_RE.fullmatch(_collapse_text(value))
    if not match:
        return None
    message = _collapse_text(match.group("message"))
    if not message or len(message) > _MAX_MESSAGE_CHARS:
        return None
    return message


def _explicit_message_content(value: str, target: str) -> Optional[str]:
    text = _collapse_text(value)
    match = _EXPLICIT_MESSAGE_CONTENT_RE.fullmatch(text)
    if not match and target:
        match = re.fullmatch(
            rf"tell\s+{re.escape(target)}\s*[:,-]?\s*(?P<message>.+)",
            text,
            flags=re.IGNORECASE,
        )
    if not match:
        return None
    message = _collapse_text(match.group("message"))
    if not message or len(message) > _MAX_MESSAGE_CHARS:
        return None
    return message


def _quoted_readback(value: str) -> str:
    # Keep the caller's normalized words verbatim.  Use ASCII quotes internally
    # so caller-supplied curly quotes cannot terminate the spoken outer quote.
    safe = value.replace("“", '"').replace("”", '"')
    return f"I have: “{safe}” Is that right?"


DecisionKind = Literal["pass", "speak", "suppress"]


@dataclass(frozen=True)
class DepositDecision:
    """One deterministic action for the pipeline dialog worker."""

    kind: DecisionKind = "pass"
    text: str = ""


@dataclass
class _DepositState:
    phase: Literal[
        "awaiting_target",
        "awaiting_message",
        "awaiting_confirmation",
        "depositing",
        "executing",
        "acknowledged",
    ]
    target: str
    message: str = ""
    confirmed_until: float = 0.0
    acknowledged_until: float = 0.0


class PipelineMessageDepositGuard:
    """Per-call deterministic message-taking state with no durable payload copy."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        confirmed_execution_window_sec: float = 5.0,
        residual_affirmation_window_sec: float = 1.25,
    ) -> None:
        if confirmed_execution_window_sec <= 0:
            raise ValueError("confirmed_execution_window_sec must be positive")
        if residual_affirmation_window_sec < 0:
            raise ValueError("residual_affirmation_window_sec must be non-negative")
        self._states: Dict[str, _DepositState] = {}
        self._clock = clock
        # The production tool-selection turn has historically completed in about
        # three seconds. Five seconds allows two seconds for bounded event-loop
        # and local HTTP-dispatch scheduling without leaving a reusable consent
        # token alive across a later caller turn.
        self._confirmed_execution_window_sec = confirmed_execution_window_sec
        self._residual_affirmation_window_sec = residual_affirmation_window_sec

    def decide(
        self,
        call_id: str,
        transcript: str,
        *,
        enabled: bool,
        default_target: Optional[str] = None,
    ) -> DepositDecision:
        """Classify one final caller transcript before it reaches the LLM."""
        if not enabled:
            self._states.pop(call_id, None)
            return DepositDecision()

        text = _collapse_text(transcript)
        if not text:
            return DepositDecision(kind="suppress")
        key = _intent_key(text)
        state = self._states.get(call_id)

        configured_target = _collapse_text(default_target or "")
        if len(configured_target) > _MAX_TARGET_CHARS:
            configured_target = ""

        if state and state.phase == "acknowledged":
            target = _message_request_target(text)
            if target:
                self._states[call_id] = _DepositState(
                    phase="awaiting_message",
                    target=target,
                )
                return DepositDecision(
                    kind="speak",
                    text=f"Of course. What would you like me to tell {target}?",
                )
            if _is_message_request_without_target(text):
                if configured_target:
                    self._states[call_id] = _DepositState(
                        phase="awaiting_message",
                        target=configured_target,
                    )
                    return DepositDecision(
                        kind="speak",
                        text=f"Of course. What would you like me to tell {configured_target}?",
                    )
                self._states[call_id] = _DepositState(
                    phase="awaiting_target",
                    target="",
                )
                return DepositDecision(
                    kind="speak",
                    text="Of course. Who would you like me to leave the message for?",
                )
            if key in _AFFIRMATIVE:
                # A short affirmation may have been spoken over the verified
                # success phrase and arrive as the next STT final.  It is not a
                # new turn and must not create a second acknowledgement.
                if self._clock() <= state.acknowledged_until:
                    return DepositDecision(kind="suppress")
                self._states.pop(call_id, None)
                return DepositDecision()
            # Any substantive utterance is a new request.  Release the guard and
            # let the ordinary agent handle it.
            self._states.pop(call_id, None)
            return DepositDecision()

        if state is None:
            target = _message_request_target(text)
            if not target and not _is_message_request_without_target(text):
                return DepositDecision()
            if not target:
                if configured_target:
                    target = configured_target
                else:
                    self._states[call_id] = _DepositState(
                        phase="awaiting_target",
                        target="",
                    )
                    return DepositDecision(
                        kind="speak",
                        text="Of course. Who would you like me to leave the message for?",
                    )
            self._states[call_id] = _DepositState(
                phase="awaiting_message",
                target=target,
            )
            return DepositDecision(
                kind="speak",
                text=f"Of course. What would you like me to tell {target}?",
            )

        if state.phase == "awaiting_target":
            if key in _CANCEL:
                self._states.pop(call_id, None)
                return DepositDecision(kind="speak", text="Of course.")
            if _is_message_request_without_target(text) or key in _PRE_MESSAGE_ACKNOWLEDGEMENT:
                return DepositDecision(
                    kind="speak",
                    text="Who would you like me to leave the message for?",
                )
            target = _message_request_target(text)
            if not target:
                target = re.sub(r"^(?:for|to)\s+", "", text, flags=re.IGNORECASE)
                target = _TRAILING_POLITENESS_RE.sub("", target).strip(" \t,;:!?.")
                target = _collapse_text(target)
            if not target or len(target) > _MAX_TARGET_CHARS:
                return DepositDecision(
                    kind="speak",
                    text="Who would you like me to leave the message for?",
                )
            state.target = target
            state.phase = "awaiting_message"
            return DepositDecision(
                kind="speak",
                text=f"What would you like me to tell {target}?",
            )

        if state.phase == "awaiting_message":
            if key in _CANCEL:
                self._states.pop(call_id, None)
                return DepositDecision(kind="speak", text="Of course.")
            replacement_target = _message_request_target(text)
            if replacement_target:
                state.target = replacement_target
                state.message = ""
                return DepositDecision(
                    kind="speak",
                    text=f"What would you like me to tell {replacement_target}?",
                )
            if _is_message_request_without_target(text):
                return DepositDecision(
                    kind="speak",
                    text=f"What would you like me to tell {state.target}?",
                )
            explicit_message = _explicit_message_content(text, state.target)
            if key in _PRE_MESSAGE_ACKNOWLEDGEMENT and not explicit_message:
                return DepositDecision(
                    kind="speak",
                    text=f"What would you like me to tell {state.target}?",
                )
            message = explicit_message or text
            if len(message) > _MAX_MESSAGE_CHARS:
                return DepositDecision(
                    kind="speak",
                    text="That is too long for me to read back safely. What shorter message would you like me to take?",
                )
            state.message = message
            state.phase = "awaiting_confirmation"
            return DepositDecision(kind="speak", text=_quoted_readback(message))

        if state.phase == "awaiting_confirmation":
            if key in _CANCEL:
                self._states.pop(call_id, None)
                return DepositDecision(kind="speak", text="Of course.")
            if key in _NEGATIVE:
                state.message = ""
                state.phase = "awaiting_message"
                return DepositDecision(
                    kind="speak",
                    text="All right. What would you like me to say instead?",
                )
            if key in _AFFIRMATIVE:
                state.phase = "depositing"
                state.confirmed_until = (
                    self._clock() + self._confirmed_execution_window_sec
                )
                return DepositDecision()
            correction = _message_correction(text)
            if correction:
                state.message = correction
                return DepositDecision(kind="speak", text=_quoted_readback(correction))
            return DepositDecision(
                kind="speak",
                text="Was that right? You can say yes, or tell me what to change.",
            )

        if state.phase == "depositing":
            # The dialog worker is normally awaiting the tool.  If a residual
            # affirmation was already queued, hold it until the tool result marks
            # the state acknowledged or failed.
            if key in _AFFIRMATIVE:
                return DepositDecision(kind="suppress")
            # A new substantive caller turn revokes the unspent confirmation.
            # The utterance still reaches the ordinary dialog model, but a tool
            # call from that turn cannot consume the former authorization.
            state.phase = "awaiting_confirmation"
            state.confirmed_until = 0.0
            return DepositDecision()

        if state.phase == "executing":
            if key in _AFFIRMATIVE:
                return DepositDecision(kind="suppress")
            return DepositDecision()

        return DepositDecision()

    def consume_confirmed_tool_parameters(
        self,
        call_id: str,
        model_parameters: Mapping[str, object],
    ) -> Dict[str, str]:
        """Atomically consume the caller-confirmed payload for one tool call.

        The model is allowed to select the tool, not rewrite the caller's
        confirmed words.  Consequently the returned parameters always replace
        model-provided target/message values.  A missing confirmation or a
        second consume fails closed.
        """
        if not isinstance(model_parameters, Mapping):
            raise ValueError("message_deposit_invalid_parameters")
        state = self._states.get(call_id)
        if not state or state.phase != "depositing":
            raise ValueError("message_deposit_not_confirmed_or_already_consumed")
        if self._clock() > state.confirmed_until:
            state.phase = "awaiting_confirmation"
            state.confirmed_until = 0.0
            raise ValueError("message_deposit_confirmation_expired")
        state.phase = "executing"
        state.confirmed_until = 0.0
        return {"target": state.target, "message": state.message}

    def note_tool_result(self, call_id: str, *, success: bool) -> None:
        """Advance only the matching confirmed deposit after its vetted result."""
        state = self._states.get(call_id)
        if not state or state.phase != "executing":
            return
        if success:
            state.phase = "acknowledged"
            state.acknowledged_until = (
                self._clock() + self._residual_affirmation_window_sec
            )
        else:
            state.phase = "awaiting_confirmation"
            state.acknowledged_until = 0.0

    def cleanup(self, call_id: str) -> None:
        """Discard all caller text when the call ends."""
        self._states.pop(call_id, None)

    def snapshot(self, call_id: str) -> Optional[dict]:
        """Expose payload-free state for tests and bounded observability."""
        state = self._states.get(call_id)
        if not state:
            return None
        return {
            "phase": state.phase,
            "has_target": bool(state.target),
            "has_message": bool(state.message),
        }
