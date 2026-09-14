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

from dataclasses import dataclass, field
import json
import re
import time
from typing import Callable, Dict, Iterable, Literal, Mapping, Optional


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
_REFERENCED_MESSAGE_REQUEST_RE = re.compile(
    r"(?:^|\b)(?:i(?:'d| would) like to\s+|i want to\s+|can i\s+|could i\s+)?"
    r"(?:leave|give|take)\s+(?:that|the\s+(?:last|previous))\s+message\s+"
    r"(?:for|to)\s+(?P<target>.+)$",
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
_MESSAGE_META_REQUESTS = {
    "change the message", "change message", "can i change the message",
    "can we change the message", "i want to change the message",
    "try again", "can i try again", "can we try again", "lets try again",
    "start again", "can we start again", "start over", "can we start over",
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


def _referenced_message_request_target(value: str) -> Optional[str]:
    text = _collapse_text(value)
    match = _REFERENCED_MESSAGE_REQUEST_RE.search(text)
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

@dataclass(frozen=True, repr=False)
class DepositReconciliation:
    """Private guard attempt, not an HTTP envelope or model-visible argument."""

    call_id: str
    generation: int
    target: str
    message: str
    envelope_json: str


@dataclass(frozen=True, repr=False)
class _PriorUtterance:
    text: str
    observed_at: float


@dataclass
class _DepositState:
    phase: Literal[
        "awaiting_target",
        "awaiting_message",
        "awaiting_confirmation",
        "depositing",
        "executing",
        "reconciling",
        "acknowledged",
    ]
    target: str
    message: str = ""
    confirmed_until: float = 0.0
    acknowledged_until: float = 0.0
    dispatch_attempted: bool = False
    dispatch_generation: int = 0
    dispatched_envelope: Optional[str] = field(default=None, repr=False)
    rearm_required: bool = False
    previous_dispatch: Optional[DepositReconciliation] = field(default=None, repr=False)


class PipelineMessageDepositGuard:
    """Per-call deterministic message-taking state with no durable payload copy."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        confirmed_execution_window_sec: float = 5.0,
        residual_affirmation_window_sec: float = 1.25,
        prior_utterance_window_sec: float = 180.0,
    ) -> None:
        if confirmed_execution_window_sec <= 0:
            raise ValueError("confirmed_execution_window_sec must be positive")
        if residual_affirmation_window_sec < 0:
            raise ValueError("residual_affirmation_window_sec must be non-negative")
        self._states: Dict[str, _DepositState] = {}
        if prior_utterance_window_sec <= 0:
            raise ValueError("prior_utterance_window_sec must be positive")
        self._prior_utterances: Dict[str, _PriorUtterance] = {}
        self._dispatch_generation = 0
        self._clock = clock
        self._prior_utterance_window_sec = prior_utterance_window_sec
        # The production tool-selection turn has historically completed in about
        # three seconds. Five seconds allows two seconds for bounded event-loop
        # and local HTTP-dispatch scheduling without leaving a reusable consent
        # token alive across a later caller turn.
        self._confirmed_execution_window_sec = confirmed_execution_window_sec
        self._residual_affirmation_window_sec = residual_affirmation_window_sec

    def _remember_prior_utterance(self, call_id: str, text: str) -> None:
        """Keep one bounded same-call candidate for an explicit next-turn reference."""
        key = _intent_key(text)
        if (
            not text
            or len(text) > _MAX_MESSAGE_CHARS
            or key in (_PRE_MESSAGE_ACKNOWLEDGEMENT | _AFFIRMATIVE | _NEGATIVE | _CANCEL)
            or key in _MESSAGE_META_REQUESTS
            or _message_request_target(text) is not None
            or _referenced_message_request_target(text) is not None
            or _is_message_request_without_target(text)
        ):
            self._prior_utterances.pop(call_id, None)
            return
        self._prior_utterances[call_id] = _PriorUtterance(text, self._clock())

    def _take_prior_utterance(self, call_id: str) -> Optional[str]:
        """Consume one fresh same-call candidate; never reuse it across requests."""
        prior = self._prior_utterances.pop(call_id, None)
        if prior is None:
            return None
        age = self._clock() - prior.observed_at
        if age < 0 or age > self._prior_utterance_window_sec:
            return None
        return prior.text

    def decide(
        self,
        call_id: str,
        transcript: str,
        *,
        enabled: bool,
        default_target: Optional[str] = None,
        caller_controls: bool = False,
        caller_end_markers: Iterable[str] = (),
    ) -> DepositDecision:
        """Classify one final caller transcript before it reaches the LLM."""
        if not enabled:
            state = self._states.get(call_id)
            self._prior_utterances.pop(call_id, None)
            if not state or state.phase not in {"executing", "reconciling"}:
                self._states.pop(call_id, None)
            return DepositDecision()

        text = _collapse_text(transcript)
        if not text:
            return DepositDecision(kind="suppress")
        key = _intent_key(text)
        state = self._states.get(call_id)

        if state and state.phase in {"executing", "reconciling"}:
            # Conversation release is not mutation cancellation. Retain the
            # private attempt until terminal native proof or call cleanup.
            if state.phase == "executing" and key in _AFFIRMATIVE:
                return DepositDecision(kind="suppress")
            return DepositDecision()

        if caller_controls and state:
            # Match a whole command against existing hangup policy, not words
            # embedded in the caller's literal message. Courtesy is not exit.
            end_keys = {_intent_key(marker) for marker in caller_end_markers}
            if key in end_keys and key not in (
                _PRE_MESSAGE_ACKNOWLEDGEMENT | _AFFIRMATIVE | _NEGATIVE
            ):
                self._states.pop(call_id, None)
                return DepositDecision()
            if key in _CANCEL and state.phase in {"depositing", "executing"}:
                self._states.pop(call_id, None)
                # Dispatched work cannot be pronounced undone by this guard.
                return DepositDecision() if state.dispatch_attempted else DepositDecision(
                    kind="speak", text="Of course."
                )
            if key in _MESSAGE_META_REQUESTS and state.phase != "acknowledged":
                if state.dispatch_attempted:
                    # An edit/retry request is not permission to replay a
                    # possibly dispatched mutation. Native agent reconciles.
                    self._states.pop(call_id, None)
                    return DepositDecision()
                state.message = ""
                state.confirmed_until = 0.0
                state.phase = "awaiting_message" if state.target else "awaiting_target"
                return DepositDecision(
                    kind="speak",
                    text=("All right. What would you like me to say instead?"
                          if state.target else
                          "Who would you like me to leave the message for?"),
                )

        referenced_target = _referenced_message_request_target(text)
        if state is None and referenced_target:
            prior_message = self._take_prior_utterance(call_id)
            if prior_message is None:
                self._states[call_id] = _DepositState(
                    phase="awaiting_message",
                    target=referenced_target,
                )
                return DepositDecision(
                    kind="speak",
                    text=f"What would you like me to tell {referenced_target}?",
                )
            self._states[call_id] = _DepositState(
                phase="awaiting_confirmation",
                target=referenced_target,
                message=prior_message,
            )
            return DepositDecision(kind="speak", text=_quoted_readback(prior_message))

        configured_target = _collapse_text(default_target or "")
        if len(configured_target) > _MAX_TARGET_CHARS:
            configured_target = ""

        if state and state.phase == "acknowledged":
            if referenced_target:
                self._states[call_id] = _DepositState(
                    phase="awaiting_message",
                    target=referenced_target,
                )
                self._prior_utterances.pop(call_id, None)
                return DepositDecision(
                    kind="speak",
                    text=f"What would you like me to tell {referenced_target}?",
                )
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
            self._remember_prior_utterance(call_id, text)
            return DepositDecision()

        if state is None:
            target = _message_request_target(text)
            if not target and not _is_message_request_without_target(text):
                self._remember_prior_utterance(call_id, text)
                return DepositDecision()
            self._prior_utterances.pop(call_id, None)
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
            if referenced_target:
                state.target = referenced_target
                state.message = ""
                state.phase = "awaiting_message"
                return DepositDecision(
                    kind="speak", text=f"What would you like me to tell {referenced_target}?"
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
            if referenced_target:
                state.target = referenced_target
                state.message = ""
                return DepositDecision(
                    kind="speak",
                    text=f"What would you like me to tell {referenced_target}?",
                )
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
                state.confirmed_until = 0.0
                return DepositDecision(kind="speak", text=_quoted_readback(correction))
            if referenced_target:
                state.target = referenced_target
                state.message = ""
                state.confirmed_until = 0.0
                state.phase = "awaiting_message"
                return DepositDecision(
                    kind="speak", text=f"What would you like me to tell {referenced_target}?"
                )
            if caller_controls:
                # An unrelated caller turn is conversation, not another demand
                # for confirmation. Discard the draft so a later Yes cannot
                # authorize the former payload.
                self._states.pop(call_id, None)
                self._remember_prior_utterance(call_id, text)
                return DepositDecision()
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
            if caller_controls:
                self._states.pop(call_id, None)
                self._remember_prior_utterance(call_id, text)
                return DepositDecision()
            # A new substantive caller turn revokes the unspent confirmation.
            # The utterance still reaches the ordinary dialog model, but a tool
            # call from that turn cannot consume the former authorization.
            state.phase = "awaiting_confirmation"
            state.confirmed_until = 0.0
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
        state.dispatch_attempted = True
        previous_generation = state.dispatch_generation
        self._dispatch_generation += 1
        state.dispatch_generation = self._dispatch_generation
        if state.rearm_required and state.dispatched_envelope is not None:
            original = json.loads(state.dispatched_envelope)
            state.previous_dispatch = DepositReconciliation(
                call_id, previous_generation,
                original["target"], original["message"], state.dispatched_envelope,
            )
        else:
            state.previous_dispatch = None
        state.dispatched_envelope = None
        state.confirmed_until = 0.0
        return {"target": state.target, "message": state.message}

    def note_tool_result(
        self, call_id: str, *, success: bool, native_outcome: Optional[str] = None,
        dispatch_generation: Optional[int] = None,
        native_rearm_required: bool = False,
    ) -> None:
        """Advance only the matching confirmed deposit after its vetted result."""
        state = self._states.get(call_id)
        if not state:
            return
        if state.dispatched_envelope is not None and (
            native_outcome is None or dispatch_generation is None
        ):
            # A boolean/unbound callback cannot prove the captured native
            # mutation terminal. Legacy adapters have no captured envelope.
            return
        if dispatch_generation is not None and state.dispatch_generation != dispatch_generation:
            return
        if state.phase != "executing":
            # Preserve the accepted post-speech residual-affirmation window.
            if not (state.phase == "acknowledged" and native_outcome == "verified"):
                return
        if state.previous_dispatch is not None:
            # Failure/cancellation before the fresh absence gate cannot forget
            # the original native operation in favour of this unspent draft.
            self.note_rearm_result(
                state.previous_dispatch, generation=state.dispatch_generation,
                native_outcome="unknown",
            )
            return
        if native_outcome is not None:
            if native_outcome == "not_deposited":
                # A remote terminal failure still needs the native operation's
                # exact absence grant before reusing its existing request ID.
                state.phase = "reconciling" if native_rearm_required else "awaiting_confirmation"
                state.dispatch_attempted = native_rearm_required
                state.rearm_required = native_rearm_required
                state.confirmed_until = 0.0
                state.acknowledged_until = 0.0
                return
            if native_outcome != "verified":
                state.phase = "reconciling"
                state.confirmed_until = 0.0
                state.acknowledged_until = 0.0
                return
            success = True
        if success:
            state.phase = "acknowledged"
            state.acknowledged_until = (
                self._clock() + self._residual_affirmation_window_sec
            )
        else:
            state.phase = "awaiting_confirmation"
            state.acknowledged_until = 0.0

    def execution_generation(self, call_id: str) -> Optional[int]:
        """Return the private generation of the one consumed tool invocation."""
        state = self._states.get(call_id)
        return state.dispatch_generation if state and state.phase == "executing" else None

    def rearm_ticket(self, call_id: str, *, generation: int) -> Optional[DepositReconciliation]:
        """Bind the previous native envelope to this freshly consumed Yes."""
        state = self._states.get(call_id)
        return state.previous_dispatch if (
            state and state.phase == "executing" and state.dispatch_generation == generation
        ) else None

    def note_rearm_result(
        self, ticket: DepositReconciliation, *, generation: int, native_outcome: str,
    ) -> bool:
        """Apply one exact pre-mutation observation, never dispatch a mutation."""
        state = self._states.get(ticket.call_id)
        if (not state or state.phase != "executing" or state.dispatch_generation != generation
            or state.previous_dispatch != ticket):
            return False
        state.previous_dispatch = None
        if native_outcome == "not_deposited":
            state.rearm_required = False
            return True
        if native_outcome == "verified" and state.dispatched_envelope == ticket.envelope_json:
            state.phase = "acknowledged"
            state.rearm_required = False
            state.acknowledged_until = self._clock() + self._residual_affirmation_window_sec
            return True
        state.phase = "reconciling"
        state.target, state.message = ticket.target, ticket.message
        state.dispatched_envelope = ticket.envelope_json
        state.rearm_required = True
        state.confirmed_until = state.acknowledged_until = 0.0
        return False

    def capture_dispatch_envelope(
        self, call_id: str, envelope: Mapping[str, object], *, generation: int,
    ) -> None:
        """Capture the closed server-bound request before HTTP dispatch, no auth."""
        state = self._states.get(call_id)
        if not state or state.phase != "executing" or state.dispatch_generation != generation:
            raise ValueError("message_deposit_stale_dispatch")
        fields = {
            "call_id", "request_id", "target", "message", "confirmed",
            "caller_name", "callback_number", "urgency",
        }
        if not isinstance(envelope, Mapping) or set(envelope) - fields or (
            envelope.get("call_id") != call_id or envelope.get("confirmed") is not True
            or envelope.get("target") != state.target or envelope.get("message") != state.message
            or not isinstance(envelope.get("request_id"), str) or not envelope["request_id"]
            or any(envelope.get(key) is not None and not isinstance(envelope[key], str)
                   for key in ("caller_name", "callback_number", "urgency"))
        ):
            raise ValueError("message_deposit_invalid_dispatch_envelope")
        encoded = json.dumps(dict(envelope), sort_keys=True, separators=(",", ":"))
        if state.dispatched_envelope is not None and state.dispatched_envelope != encoded:
            raise ValueError("message_deposit_dispatch_envelope_changed")
        state.dispatched_envelope = encoded

    def reconciliation_ticket(self, call_id: str) -> Optional[DepositReconciliation]:
        """Bind one future read-only result to the existing unresolved attempt."""
        state = self._states.get(call_id)
        if not state or state.phase != "reconciling" or state.dispatched_envelope is None:
            return None
        return DepositReconciliation(
            call_id, state.dispatch_generation, state.target, state.message,
            state.dispatched_envelope,
        )

    def note_reconciliation_result(
        self, ticket: DepositReconciliation, *, native_outcome: str,
    ) -> bool:
        """Apply native proof only; the managed read-only HTTP carrier owns IO."""
        state = self._states.get(ticket.call_id)
        if not state or state.phase != "reconciling" or (
            state.dispatch_generation != ticket.generation
            or state.target != ticket.target or state.message != ticket.message
            or state.dispatched_envelope != ticket.envelope_json
        ):
            return False
        if native_outcome == "verified":
            state.phase = "acknowledged"
            state.acknowledged_until = self._clock() + self._residual_affirmation_window_sec
        elif native_outcome == "not_deposited":
            # Absence is not caller consent. Corrections still get exact readback.
            state.phase = "awaiting_confirmation"
            state.dispatch_attempted = False
            state.rearm_required = True
        else:
            return False
        state.confirmed_until = 0.0
        return True

    def cleanup(self, call_id: str) -> None:
        """Discard all caller text when the call ends."""
        self._prior_utterances.pop(call_id, None)
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
