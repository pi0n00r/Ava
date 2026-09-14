# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
"""Caller control regressions; no network, audio, or native PBX calls."""

import ast
import asyncio
import copy
import hashlib
import importlib.util
import os
import pathlib
import subprocess
import sys
import types
import unittest
from unittest.mock import AsyncMock

from src.core.pipeline_message_deposit import PipelineMessageDepositGuard
from src.tools.telephony.hangup_policy import resolve_hangup_policy


CANDIDATE = pathlib.Path(__file__).resolve().parents[1]
BASE_COMMIT = "5a6cc7195ad022bef5a94f8d8665a463a5566b65"
BASELINE = os.environ.get("JD_AVA_BASELINE_SOURCE")
END_MARKERS = resolve_hangup_policy({})["markers"]["end_call"]


def baseline_bytes(relative):
    if BASELINE:
        return (pathlib.Path(BASELINE) / relative).read_bytes()
    result = subprocess.run(
        ["git", "-C", str(CANDIDATE), "show", BASE_COMMIT + ":" + relative],
        check=True, capture_output=True, timeout=10,
    )
    return result.stdout


def load_baseline():
    name = "jd_baseline_message_deposit"
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(baseline_bytes("src/core/pipeline_message_deposit.py"),
                 BASE_COMMIT + ":src/core/pipeline_message_deposit.py",
                 "exec"), module.__dict__)
    return module.PipelineMessageDepositGuard


BASELINE_GUARD = load_baseline()


def main_decide(guard, text, call="call-a", **kwargs):
    return guard.decide(
        call, text, enabled=True, caller_controls=True,
        caller_end_markers=END_MARKERS, **kwargs
    )


def pending(guard=None, call="call-a"):
    guard = guard or PipelineMessageDepositGuard(clock=lambda: 1.0)
    main_decide(guard, "leave a message for Recipient", call)
    main_decide(guard, "I will arrive at noon.", call)
    return guard


def engine_prelude():
    """Execute the exact guarded-decision block, not the whole dialog worker."""
    source = (CANDIDATE / "src/engine.py").read_text()
    tree = ast.parse(source)
    engine = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
    # The call is nested in the real async dialog worker.
    for node in ast.walk(engine):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for i, statement in enumerate(node.body):
            if (
                isinstance(statement, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "configured_tool_names"
                        for t in statement.targets)
            ):
                selected = []
                for item in node.body[i:]:
                    selected.append(copy.deepcopy(item))
                    if isinstance(item, ast.If) and isinstance(item.test, ast.Name) and item.test.id == "confirmed_deposit":
                        break
                if not selected or not isinstance(selected[-1], ast.If):
                    raise RuntimeError("guard_block_boundary_changed")
                # A reached ordinary path returns a marker, not model output.
                selected.append(ast.Return(value=ast.Constant(value="ordinary")))
                wrapper = ast.AsyncFunctionDef(
                    name="prelude",
                    args=ast.arguments(
                        posonlyargs=[], args=[ast.arg(arg=x) for x in (
                            "self", "call_id", "llm_options", "transcript_text",
                            "conversation_history", "session", "pipeline"
                        )], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                        defaults=[]
                    ), body=selected, decorator_list=[], returns=None, type_comment=None
                )
                bound_methods = [
                    copy.deepcopy(m) for m in engine.body
                    if isinstance(m, ast.FunctionDef) and m.name in {
                        "_confirmed_pipeline_message_deposit_call",
                        "_bind_pipeline_tool_parameters",
                    }
                ]
                if len(bound_methods) != 2:
                    raise RuntimeError("engine_method_boundary_changed")
                module = ast.fix_missing_locations(ast.Module(body=bound_methods + [wrapper], type_ignores=[]))
                namespace = {
                    "resolve_hangup_policy": resolve_hangup_policy,
                    "Dict": dict, "Any": object, "Optional": __import__("typing").Optional,
                    "_ts_msg": lambda role, text: {"role": role, "content": text},
                    "logger": types.SimpleNamespace(info=lambda *a, **k: None),
                }
                exec(compile(module, str(CANDIDATE / "src/engine.py"), "exec"), namespace)
                return namespace
    raise RuntimeError("guard_block_not_found")


ENGINE = engine_prelude()


class MessageControlTests(unittest.TestCase):
    def assert_unconsumable(self, guard, call="call-a"):
        with self.assertRaisesRegex(ValueError, "not_confirmed_or_already_consumed"):
            guard.consume_confirmed_tool_parameters(call, {})

    def test_change_message_resets_draft_without_dictating_command(self):
        guard = pending()
        result = main_decide(guard, "Change the message.")
        self.assertEqual(result.kind, "speak")
        self.assertIn("say instead", result.text)
        self.assertEqual(guard.snapshot("call-a"), {
            "phase": "awaiting_message", "has_target": True, "has_message": False
        })
        self.assert_unconsumable(guard)
        readback = main_decide(guard, "I will arrive at one.")
        self.assertIn("I will arrive at one.", readback.text)
        self.assert_unconsumable(guard)
        main_decide(guard, "Yes")
        self.assertEqual(guard.consume_confirmed_tool_parameters("call-a", {
            "target": "Wrong", "message": "Wrong"
        }), {"target": "Recipient", "message": "I will arrive at one."})

    def test_human_edit_commands_cannot_reauthorize_old_payload_by_yes(self):
        for words in ("Change the message.", "can we try again?"):
            with self.subTest(words=words):
                guard = pending()
                main_decide(guard, words)
                self.assertFalse(guard.snapshot("call-a")["has_message"])
                main_decide(guard, "Yes")
                self.assert_unconsumable(guard)

    def test_try_again_resets_unsent_draft(self):
        guard = pending()
        result = main_decide(guard, "can we try again?")
        self.assertEqual(result.kind, "speak")
        self.assertEqual(guard.snapshot("call-a")["phase"], "awaiting_message")
        self.assertFalse(guard.snapshot("call-a")["has_message"])
        self.assert_unconsumable(guard)

    def test_meta_request_without_target_does_not_become_recipient(self):
        guard = PipelineMessageDepositGuard()
        main_decide(guard, "leave a message")
        result = main_decide(guard, "Change the message.")
        self.assertIn("Who", result.text)
        self.assertEqual(guard.snapshot("call-a")["phase"], "awaiting_target")
        self.assertFalse(guard.snapshot("call-a")["has_target"])

    def test_meta_before_dictation_is_not_payload(self):
        guard = PipelineMessageDepositGuard()
        main_decide(guard, "leave a message for Recipient")
        main_decide(guard, "can we try again?")
        self.assertFalse(guard.snapshot("call-a")["has_message"])
        self.assertEqual(guard.snapshot("call-a")["phase"], "awaiting_message")

    def test_meta_revokes_unconsumed_confirmation(self):
        guard = pending()
        main_decide(guard, "Yes")
        self.assertEqual(guard.snapshot("call-a")["phase"], "depositing")
        main_decide(guard, "Change the message.")
        self.assert_unconsumable(guard)
        self.assertFalse(guard.snapshot("call-a")["has_message"])

    def test_hangup_is_released_in_all_pending_phases(self):
        for phase in ("awaiting_target", "awaiting_message", "awaiting_confirmation", "depositing", "executing"):
            with self.subTest(phase=phase):
                guard = PipelineMessageDepositGuard()
                if phase == "awaiting_target":
                    main_decide(guard, "leave a message")
                else:
                    main_decide(guard, "leave a message for Recipient")
                if phase in {"awaiting_confirmation", "depositing", "executing"}:
                    main_decide(guard, "I will arrive at noon.")
                if phase in {"depositing", "executing"}:
                    main_decide(guard, "Yes")
                if phase == "executing":
                    guard.consume_confirmed_tool_parameters("call-a", {})
                self.assertEqual(guard.snapshot("call-a")["phase"], phase)
                self.assertEqual(main_decide(guard, "Hang up.").kind, "pass")
                if phase == "executing":
                    self.assertEqual(guard.snapshot("call-a")["phase"], "executing")
                    self.assert_unconsumable(guard)
                    guard.cleanup("call-a")
                self.assertIsNone(guard.snapshot("call-a"))
                self.assert_unconsumable(guard)
                guard.note_tool_result("call-a", success=True)
                self.assertIsNone(guard.snapshot("call-a"))

    def test_exact_configured_end_marker_is_used(self):
        guard = pending()
        result = guard.decide(
            "call-a", "You may hang up.", enabled=True, caller_controls=True,
            caller_end_markers=["you may hang up"]
        )
        self.assertEqual(result.kind, "pass")
        self.assertIsNone(guard.snapshot("call-a"))

    def test_exit_without_message_state_is_ordinary_conversation(self):
        guard = PipelineMessageDepositGuard()
        self.assertEqual(main_decide(guard, "Hang up.").kind, "pass")
        self.assertIsNone(guard.snapshot("call-a"))

    def test_thanks_is_not_an_exit_or_message(self):
        for words in ("Okay thanks", "Thank you", "Sure"):
            with self.subTest(words=words):
                guard = PipelineMessageDepositGuard()
                main_decide(guard, "leave a message for Recipient")
                self.assertEqual(main_decide(guard, words).kind, "speak")
                self.assertFalse(guard.snapshot("call-a")["has_message"])

    def test_literal_dictation_of_commands_is_preserved(self):
        for payload in ("hang up", "Change the message.", "can we try again?"):
            with self.subTest(payload=payload):
                guard = PipelineMessageDepositGuard()
                main_decide(guard, "leave a message for Recipient")
                readback = main_decide(guard, "The message is " + payload)
                self.assertIn(payload, readback.text)
                main_decide(guard, "Yes")
                self.assertEqual(guard.consume_confirmed_tool_parameters("call-a", {})["message"], payload)

    def test_command_words_embedded_in_message_are_preserved(self):
        guard = PipelineMessageDepositGuard()
        main_decide(guard, "leave a message for Recipient")
        text = "Please hang up the laundry when it is dry."
        self.assertIn(text, main_decide(guard, text).text)

    def test_inline_correction_is_preserved_and_needs_fresh_yes(self):
        guard = pending()
        self.assertIn("I will arrive at one.", main_decide(
            guard, "No, change it to I will arrive at one."
        ).text)
        self.assert_unconsumable(guard)
        main_decide(guard, "Yes")
        self.assertEqual(guard.consume_confirmed_tool_parameters("call-a", {})["message"], "I will arrive at one.")

    def test_cancel_and_no_keep_existing_behavior(self):
        guard = pending()
        self.assertIn("say instead", main_decide(guard, "No").text)
        self.assertEqual(guard.snapshot("call-a")["phase"], "awaiting_message")
        self.assertEqual(main_decide(guard, "Cancel").text, "Of course.")
        self.assertIsNone(guard.snapshot("call-a"))

    def test_cancel_before_dispatch_revokes_consent(self):
        guard = pending()
        main_decide(guard, "Yes")
        self.assertEqual(main_decide(guard, "Do not send it").text, "Of course.")
        self.assertIsNone(guard.snapshot("call-a"))
        self.assert_unconsumable(guard)

    def test_cancel_after_dispatch_does_not_claim_undo(self):
        guard = pending()
        main_decide(guard, "Yes")
        guard.consume_confirmed_tool_parameters("call-a", {})
        result = main_decide(guard, "Cancel")
        self.assertEqual((result.kind, result.text), ("pass", ""))
        self.assertEqual(guard.snapshot("call-a")["phase"], "executing")
        self.assert_unconsumable(guard)
        guard.cleanup("call-a")
        guard.note_tool_result("call-a", success=True, native_outcome="verified")
        self.assertIsNone(guard.snapshot("call-a"))

    def test_edit_or_retry_after_failed_attempt_does_not_replay(self):
        for words in ("Change the message.", "can we try again?"):
            with self.subTest(words=words):
                guard = pending()
                main_decide(guard, "Yes")
                guard.consume_confirmed_tool_parameters("call-a", {})
                guard.note_tool_result("call-a", success=False)
                self.assertEqual(main_decide(guard, words).kind, "pass")
                self.assertIsNone(guard.snapshot("call-a"))
                self.assert_unconsumable(guard)
                self.assertEqual(main_decide(guard, "Yes").kind, "pass")
                self.assertIsNone(guard.snapshot("call-a"))
                guard.note_tool_result("call-a", success=True)
                self.assertIsNone(guard.snapshot("call-a"))

    def test_edit_after_success_is_ordinary_new_conversation(self):
        guard = pending()
        main_decide(guard, "Yes")
        guard.consume_confirmed_tool_parameters("call-a", {})
        guard.note_tool_result("call-a", success=True)
        self.assertEqual(main_decide(guard, "Change the message.").kind, "pass")
        self.assertIsNone(guard.snapshot("call-a"))

    def test_did_you_get_that_is_conversation_not_confirmation_loop(self):
        guard = pending()
        result = main_decide(guard, "Did you get that?")
        self.assertEqual((result.kind, result.text), ("pass", ""))
        self.assertIsNone(guard.snapshot("call-a"))
        self.assert_unconsumable(guard)
        self.assertEqual(main_decide(guard, "Yes").kind, "pass")
        self.assert_unconsumable(guard)

    def test_any_unknown_confirmation_turn_releases_and_cannot_reauthorize(self):
        for words in ("How is the weather?", "I need to go now.", "Okay thanks", "Can you hang up please?"):
            with self.subTest(words=words):
                guard = pending()
                self.assertEqual(main_decide(guard, words).kind, "pass")
                self.assertIsNone(guard.snapshot("call-a"))
                main_decide(guard, "Yes")
                self.assert_unconsumable(guard)

    def test_unknown_after_yes_revokes_unspent_authorization(self):
        guard = pending()
        main_decide(guard, "Yes")
        self.assertEqual(main_decide(guard, "Did you get that?").kind, "pass")
        self.assertIsNone(guard.snapshot("call-a"))
        main_decide(guard, "Yes")
        self.assert_unconsumable(guard)

    def test_conversation_during_execution_retains_attempt_until_terminal_proof(self):
        guard = pending()
        main_decide(guard, "Yes")
        guard.consume_confirmed_tool_parameters("call-a", {})
        generation = guard.execution_generation("call-a")
        self.assertEqual(main_decide(guard, "How is the weather?").kind, "pass")
        self.assertEqual(guard.snapshot("call-a")["phase"], "executing")
        main_decide(guard, "Yes")
        self.assert_unconsumable(guard)
        guard.note_tool_result("call-a", success=True, native_outcome="verified",
                               dispatch_generation=generation)
        self.assertEqual(guard.snapshot("call-a")["phase"], "acknowledged")
        guard.cleanup("call-a")
        guard.note_tool_result("call-a", success=True, native_outcome="verified",
                               dispatch_generation=generation)
        self.assertIsNone(guard.snapshot("call-a"))

    def test_fresh_explicit_message_after_conversation_needs_new_readback_yes(self):
        guard = pending()
        main_decide(guard, "Did you get that?")
        main_decide(guard, "leave a message for Recipient")
        result = main_decide(guard, "The new instruction.")
        self.assertIn("The new instruction.", result.text)
        self.assert_unconsumable(guard)
        main_decide(guard, "Yes")
        self.assertEqual(guard.consume_confirmed_tool_parameters("call-a", {})["message"], "The new instruction.")

    def test_call_scope_and_cleanup_isolation(self):
        guard = pending()
        pending(guard, "call-b")
        main_decide(guard, "Hang up.")
        self.assertIsNone(guard.snapshot("call-a"))
        self.assertEqual(guard.snapshot("call-b")["phase"], "awaiting_confirmation")
        main_decide(guard, "Yes", "call-b")
        self.assertEqual(guard.consume_confirmed_tool_parameters("call-b", {})["target"], "Recipient")
        guard.cleanup("call-b")
        guard.note_tool_result("call-b", success=True)
        self.assertIsNone(guard.snapshot("call-b"))

    def test_unconfirmed_legacy_model_attempt_is_still_rejected(self):
        guard = pending()
        self.assert_unconsumable(guard)
        main_decide(guard, "Yes")
        guard.consume_confirmed_tool_parameters("call-a", {})
        self.assert_unconsumable(guard)

    def test_default_guard_matches_baseline_across_dialog_sequences(self):
        dialogs = [
            ["leave a message for Recipient", "Okay thanks", "Message is Thank you", "Yes"],
            ["leave a message", "Recipient", "I will arrive at noon.", "No", "Change it to later", "Yes"],
            ["leave a message for Recipient", "I will arrive at noon.", "Change the message.", "can we try again?", "Hang up."],
            ["ordinary conversation", "Hang up."],
            ["leave a message for Recipient", "I will arrive at noon.", "Cancel"],
        ]
        for dialog in dialogs:
            with self.subTest(dialog=dialog):
                baseline = BASELINE_GUARD(clock=lambda: 1.0)
                candidate = PipelineMessageDepositGuard(clock=lambda: 1.0)
                for text in dialog:
                    before = baseline.decide("mini", text, enabled=True)
                    after = candidate.decide("mini", text, enabled=True)
                    self.assertEqual((after.kind, after.text), (before.kind, before.text))
                    self.assertEqual(candidate.snapshot("mini"), baseline.snapshot("mini"))
                if (candidate.snapshot("mini") or {}).get("phase") == "depositing":
                    self.assertEqual(candidate.consume_confirmed_tool_parameters("mini", {}), baseline.consume_confirmed_tool_parameters("mini", {}))
                    candidate.note_tool_result("mini", success=False)
                    baseline.note_tool_result("mini", success=False)
                    for text in ("Change the message.", "Yes"):
                        before = baseline.decide("mini", text, enabled=True)
                        after = candidate.decide("mini", text, enabled=True)
                        self.assertEqual((after.kind, after.text), (before.kind, before.text))
                        self.assertEqual(candidate.snapshot("mini"), baseline.snapshot("mini"))

    def test_disabled_tool_still_does_not_intercept(self):
        guard = pending()
        result = guard.decide("call-a", "Change the message.", enabled=False, caller_controls=True, caller_end_markers=END_MARKERS)
        self.assertEqual(result.kind, "pass")
        self.assertIsNone(guard.snapshot("call-a"))

    def test_untouched_shared_paths_are_byte_identical(self):
        for relative in (
            "src/tools/telephony/hangup_policy.py",
            "src/logging_config.py", "tests/test_pipeline_message_deposit.py"
        ):
            with self.subTest(path=relative):
                self.assertEqual((CANDIDATE / relative).read_bytes(), baseline_bytes(relative))

    def test_no_other_engine_method_changed(self):
        def methods(source):
            tree = ast.parse(source)
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
            return {n.name: ast.dump(n, include_attributes=False)
                    for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        before = methods(baseline_bytes("src/engine.py"))
        after = methods((CANDIDATE / "src/engine.py").read_bytes())
        changed = {n for n in set(before) | set(after) if before.get(n) != after.get(n)}
        self.assertEqual(changed, {
            "_pipeline_runner", "_maybe_speak_direct_pipeline_tool_result",
        })


class EngineDecisionBlockTests(unittest.IsolatedAsyncioTestCase):
    def fake_engine(self, guard):
        return types.SimpleNamespace(
            config=types.SimpleNamespace(tools={}),
            _pipeline_message_deposit_guard=lambda: guard,
            _no_input_note_processing=AsyncMock(),
            _maybe_speak_direct_pipeline_tool_result=AsyncMock(),
            session_store=types.SimpleNamespace(upsert_call=AsyncMock()),
            _confirmed_pipeline_message_deposit_call=lambda call, options: ENGINE["_confirmed_pipeline_message_deposit_call"](
                types.SimpleNamespace(_pipeline_message_deposit_guard=lambda: guard), call, options
            )
        )

    async def run_prelude(self, guard, options, text):
        self.engine = self.fake_engine(guard)
        session = types.SimpleNamespace(conversation_history=[])
        return await ENGINE["prelude"](
            self.engine, "call-a", options, text, [], session, object()
        )

    async def test_main_exact_exit_reaches_ordinary_path(self):
        guard = pending()
        options = {"tools": ["pbx_message_deposit"], "call_id_header_enabled": True, "session_user_from_call_id": True}
        self.assertEqual(await self.run_prelude(guard, options, "Hang up."), "ordinary")
        self.engine._maybe_speak_direct_pipeline_tool_result.assert_not_awaited()
        self.assertIsNone(guard.snapshot("call-a"))

    async def test_main_meta_request_speaks_and_does_not_dispatch(self):
        guard = pending()
        options = {"tools": ["pbx_message_deposit"], "call_id_header_enabled": True, "session_user_from_call_id": True}
        self.assertIsNone(await self.run_prelude(guard, options, "Change the message."))
        self.engine._maybe_speak_direct_pipeline_tool_result.assert_awaited_once()
        self.assertEqual(guard.snapshot("call-a")["phase"], "awaiting_message")

    async def test_main_did_you_get_that_reaches_native_conversation_without_old_tool(self):
        guard = pending()
        options = {"tools": ["pbx_message_deposit"], "call_id_header_enabled": True, "session_user_from_call_id": True}
        self.assertEqual(await self.run_prelude(guard, options, "Did you get that?"), "ordinary")
        self.engine._maybe_speak_direct_pipeline_tool_result.assert_not_awaited()
        self.assertIsNone(self.engine._confirmed_pipeline_message_deposit_call("call-a", options))
        with self.assertRaises(ValueError):
            ENGINE["_bind_pipeline_tool_parameters"](self.engine, "call-a", "pbx_message_deposit", {})

    async def test_mini_and_nonliteral_flags_keep_prior_loop_behavior(self):
        for options in (
            {}, {"call_id_header_enabled": True},
            {"session_user_from_call_id": True},
            {"call_id_header_enabled": "true", "session_user_from_call_id": True},
            {"call_id_header_enabled": 1, "session_user_from_call_id": True},
            {"call_id_header_enabled": True, "session_user_from_call_id": False},
        ):
            with self.subTest(options=options):
                guard = pending()
                self.assertIsNone(await self.run_prelude(guard, {"tools": ["pbx_message_deposit"], **options}, "Hang up."))
                self.engine._maybe_speak_direct_pipeline_tool_result.assert_awaited_once()
                self.assertEqual(guard.snapshot("call-a")["phase"], "awaiting_confirmation")

    async def test_no_deposit_tool_leaves_native_route_and_catalogue_unchanged(self):
        guard = pending()
        options = {"tools": ["ordinary_native_tool"], "call_id_header_enabled": True, "session_user_from_call_id": True}
        saved = copy.deepcopy(options)
        self.assertEqual(await self.run_prelude(guard, options, "Change the message."), "ordinary")
        self.assertIsNone(guard.snapshot("call-a"))
        self.assertEqual(options, saved)

    async def test_existing_confirmed_executor_selection_and_exact_bind(self):
        guard = pending()
        main_decide(guard, "Yes")
        options = {"tools": ["pbx_message_deposit"], "call_id_header_enabled": True, "session_user_from_call_id": True}
        engine = self.fake_engine(guard)
        self.assertEqual(ENGINE["_confirmed_pipeline_message_deposit_call"](engine, "call-a", options),
                         {"name": "pbx_message_deposit", "parameters": {}})
        self.assertEqual(ENGINE["_bind_pipeline_tool_parameters"](engine, "call-a", "pbx_message_deposit", {"target": "Wrong", "message": "Wrong"}),
                         {"target": "Recipient", "message": "I will arrive at noon."})
        with self.assertRaises(ValueError):
            ENGINE["_bind_pipeline_tool_parameters"](engine, "call-a", "pbx_message_deposit", {})
        self.assertEqual(ENGINE["_bind_pipeline_tool_parameters"](engine, "call-a", "ordinary_native_tool", {"value": 1}), {"value": 1})


if __name__ == "__main__":
    unittest.main()
