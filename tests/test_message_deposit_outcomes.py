# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava

"""Dummy HTTP/native outcomes; no production request, token, or caller data."""
import ast
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from src.core.pipeline_message_deposit import PipelineMessageDepositGuard
from src.tools.context import ToolExecutionContext
from src.tools.execution_history import normalize_tool_terminal_status
from src.tools.http.in_call_lookup import InCallHTTPConfig, InCallHTTPTool

ROOT = Path(__file__).parents[1]
CALL = "fixture-native-call-01"
SAVED = {
    "ok": True, "status": "saved", "artifact_verified": True, "replay": False,
    "native_id": "fixture-native-artifact",
    "spoken_response": "Thanks. I'll make sure they get it.",
}
ABSENT = {"ok": False, "status": "definitively_not_found", "artifact_verified": False, "replay": False}

def begin(guard, call=CALL):
    for text in ("Leave a message for Gary.", "The sky is blue.", "Yes."):
        guard.decide(call, text, enabled=True, caller_controls=True)
    params = guard.consume_confirmed_tool_parameters(
        call, {"target": "wrong", "message": "wrong", "call_id": "wrong"})
    context = ToolExecutionContext(
        call_id=call, caller_name="Fixture Caller", caller_number="fixture-callback",
        context_name="aimee_main", native_deposit_guard=guard,
        native_deposit_attempt=guard.execution_generation(call),
        native_deposit_required=True)
    return params, context

def config(**overrides):
    values = dict(
        name="pbx_message_deposit", url="https://vip.fixture.invalid/v1/message-deposit",
        method="POST", timeout_ms=180000, headers={"Authorization": "Bearer dummy-only"},
        direct_response_json_path="spoken_response",
        direct_failure_message="I am sorry, I lost that at my end.",
        body_template='{"call_id":"{call_id}","request_id":"{call_id}",'
                      '"target":"{target}","message":"{message}","confirmed":true,'
                      '"caller_name":"{caller_name}","callback_number":"{caller_number}",'
                      '"urgency":"normal"}')
    values.update(overrides)
    return InCallHTTPConfig(**values)

class Content:
    def __init__(self, data):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()
    async def iter_chunked(self, size):
        for start in range(0, len(self.data), size):
            await asyncio.sleep(0)
            yield self.data[start:start + size]

async def execute(tool, params, context, data, status=200, content=None):
    response = SimpleNamespace(status=status, content=content or Content(data), headers={})
    request_cm = AsyncMock()
    request_cm.__aenter__.return_value = response
    session = SimpleNamespace(request=MagicMock(return_value=request_cm))
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await tool.execute(params, context)
    called = session.request.call_args
    return result, called.kwargs if called else None

async def execute_many(tool, params, context, responses):
    managers = []
    for data, status, content in responses:
        manager = AsyncMock()
        manager.__aenter__.return_value = SimpleNamespace(
            status=status, content=content or Content(data), headers={})
        managers.append(manager)
    session = SimpleNamespace(request=MagicMock(side_effect=managers))
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    with patch("aiohttp.ClientSession", return_value=session_cm):
        result = await tool.execute(params, context)
    return result, [call.kwargs for call in session.request.call_args_list]

def direct_method():
    tree = ast.parse((ROOT / "src/engine.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
    node = next(n for n in cls.body if getattr(n, "name", None) == "_maybe_speak_direct_pipeline_tool_result")
    namespace = {
        "Any": object, "Dict": dict, "List": list, "Optional": __import__("typing").Optional,
        "normalize_tool_terminal_status": normalize_tool_terminal_status,
        "_ts_msg": lambda role, text: {"role": role, "content": text},
        "_PipelinePlaybackInterrupted": type("Interrupted", (Exception,), {}),
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-engine-direct", "exec"), namespace)
    return namespace[node.name]

class TypedDepositTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_yes_refreshes_original_grant_and_never_mutates_on_unknown(self):
        for status, proof in ((202,{"ok":False,"status":"pending_or_ambiguous","artifact_verified":False,"replay":False}),
                              (409,{"ok":False,"status":"conflicting","artifact_verified":False,"replay":False,"error":"request_replay_conflict"}),
                              (200,{"ok":True,"status":"verified_saved","artifact_verified":True,"replay":True,"native_id":"fixture-native-artifact"})):
            with self.subTest(status=status):
                guard=PipelineMessageDepositGuard();params,ctx=begin(guard);tool=InCallHTTPTool(config())
                _first,original=await execute_many(tool,params,ctx,[( {"ok":False,"error":"tts_request_failed"},502,None),(ABSENT,404,None)])
                guard.decide(CALL,"Change it to the sky is green.",enabled=True,caller_controls=True)
                guard.decide(CALL,"Yes",enabled=True,caller_controls=True)
                corrected=guard.consume_confirmed_tool_parameters(CALL,{})
                ctx.native_deposit_attempt=guard.execution_generation(CALL)
                result,sent=await execute_many(tool,corrected,ctx,[(proof,status,None)])
                self.assertEqual(len(sent),1)
                self.assertTrue(sent[0]["url"].endswith("/reconcile"))
                self.assertEqual(sent[0]["json"],original[0]["json"])
                self.assertNotEqual(sent[0]["json"]["message"],corrected["message"])
                self.assertNotEqual(result["status"],"success")
                self.assertEqual(guard.reconciliation_ticket(CALL).envelope_json,json.dumps(original[0]["json"],sort_keys=True,separators=(",",":")))
                guard.decide(CALL,"Yes",enabled=True,caller_controls=True)
                with self.assertRaises(ValueError):guard.consume_confirmed_tool_parameters(CALL,{})

    async def test_cancellation_during_fresh_grant_preserves_original_attempt(self):
        guard=PipelineMessageDepositGuard();params,ctx=begin(guard);tool=InCallHTTPTool(config())
        _first,original=await execute_many(tool,params,ctx,[({"ok":False,"error":"tts_request_failed"},502,None),(ABSENT,404,None)])
        guard.decide(CALL,"Change it to the sky is green.",enabled=True,caller_controls=True)
        guard.decide(CALL,"Yes",enabled=True,caller_controls=True)
        corrected=guard.consume_confirmed_tool_parameters(CALL,{})
        ctx.native_deposit_attempt=guard.execution_generation(CALL)
        entered=asyncio.Event();release=asyncio.Event()
        class Blocked(Content):
            async def iter_chunked(self,size):
                entered.set();await release.wait()
                async for chunk in super().iter_chunked(size):yield chunk
        task=asyncio.create_task(execute_many(tool,corrected,ctx,[(ABSENT,404,Blocked(ABSENT))]))
        await asyncio.wait_for(entered.wait(),1)
        self.assertEqual(guard.decide(CALL,"Did you get that?",enabled=True,caller_controls=True).kind,"pass")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        ticket=guard.reconciliation_ticket(CALL)
        self.assertEqual(json.loads(ticket.envelope_json),original[0]["json"])
        guard.decide(CALL,"Yes",enabled=True,caller_controls=True)
        with self.assertRaises(ValueError):guard.consume_confirmed_tool_parameters(CALL,{})
        guard.cleanup(CALL);self.assertIsNone(guard.snapshot(CALL))

    async def test_unchanged_retry_requires_new_yes_and_saved_replay_preserves_identity(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        tool = InCallHTTPTool(config(return_raw_json=True))
        first, sent = await execute_many(tool, params, ctx, [
            ({"ok": False, "error": "tts_request_failed"}, 502, None),
            (ABSENT, 404, None)])
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]["json"], sent[1]["json"])
        self.assertEqual(first["_native_deposit"]["outcome"], "not_deposited")
        with self.assertRaises(ValueError):
            guard.consume_confirmed_tool_parameters(CALL, {})
        guard.decide(CALL, "Yes", enabled=True, caller_controls=True)
        retried = guard.consume_confirmed_tool_parameters(CALL, {})
        ctx.native_deposit_attempt = guard.execution_generation(CALL)
        saved, saved_requests = await execute_many(tool, retried, ctx, [(ABSENT, 404, None), (SAVED, 200, None)])
        self.assertEqual(len(saved_requests), 2)
        self.assertTrue(saved_requests[0]["url"].endswith("/reconcile"))
        self.assertEqual(sent[0]["json"], saved_requests[1]["json"])
        self.assertEqual(saved["status"], "success")
        with self.assertRaises(ValueError):
            guard.consume_confirmed_tool_parameters(CALL, {})
        # A new explicit dictation/confirmation may receive Rita's idempotent
        # saved replay. The artifact receipt, not HTTP 2xx alone, is authority.
        replay_params, replay_ctx = begin(guard)
        replay, replay_requests = await execute_many(
            tool, replay_params, replay_ctx, [(dict(SAVED, replay=True), 200, None)])
        self.assertEqual(len(replay_requests), 1)
        self.assertEqual(replay_requests[0]["json"], saved_requests[1]["json"])
        self.assertEqual(replay["data"]["native_id"], saved["data"]["native_id"])
        self.assertIs(replay["data"]["replay"], True)
        self.assertEqual(replay["status"], "success")

    async def test_boolean_or_unbound_callbacks_cannot_rearm_captured_native_attempt(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        body = {"call_id": CALL, "request_id": CALL, **params, "confirmed": True}
        guard.capture_dispatch_envelope(CALL, body, generation=ctx.native_deposit_attempt)
        state = guard._states[CALL]
        for result in (
            {"success": False}, {"success": True},
            {"success": False, "native_outcome": "not_deposited"},
            {"success": True, "native_outcome": "verified"},
        ):
            with self.subTest(result=result):
                guard.note_tool_result(CALL, **result)
                self.assertIs(guard._states[CALL], state)
                self.assertEqual(guard.snapshot(CALL)["phase"], "executing")
                guard.decide(CALL, "Yes", enabled=True, caller_controls=True)
                with self.assertRaises(ValueError):
                    guard.consume_confirmed_tool_parameters(CALL, {})
        self.assertEqual(guard.decide(CALL, "Leave a message for Gary", enabled=False).kind, "pass")
        self.assertIs(guard._states[CALL], state)
        guard.note_tool_result(CALL, success=False, native_outcome="pending",
                               dispatch_generation=ctx.native_deposit_attempt)
        ticket = guard.reconciliation_ticket(CALL)
        guard.decide(CALL, "Yes", enabled=False)
        self.assertEqual(guard.reconciliation_ticket(CALL), ticket)
        guard.cleanup(CALL)
        self.assertIsNone(guard.snapshot(CALL))

    async def test_exact_original_wire_and_safe_reconciliation_classes(self):
        for status, proof, expected in (
            (200, {"ok": True, "status": "verified_saved", "artifact_verified": True,
                   "replay": True, "native_id": "fixture-native-artifact"}, "acknowledged"),
            (404, {"ok": False, "status": "definitively_not_found",
                   "artifact_verified": False, "replay": False}, "awaiting_confirmation"),
            (409, {"ok": False, "status": "conflicting", "artifact_verified": False,
                   "replay": False, "error": "call_uuid_payload_conflict"}, "reconciling"),
            (202, {"ok": False, "status": "pending_or_ambiguous", "artifact_verified": False,
                   "replay": False, "error": "helper_timeout"}, "reconciling"),
        ):
            with self.subTest(status=status):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                url = "https://vip.fixture.invalid/aimee-router/v1/message-deposit"
                result, requests = await execute_many(InCallHTTPTool(config(url=url)), params, ctx, [
                    ({"ok": False, "status": "pending", "error": "helper_timeout"}, 202, None),
                    (dict(proof, token="never-forward", call_id="never-forward"), status, None),
                ])
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[0]["url"], url)
                self.assertEqual(requests[1]["url"], url + "/reconcile")
                self.assertEqual(requests[0]["json"], requests[1]["json"])
                self.assertEqual(requests[0]["headers"], requests[1]["headers"])
                self.assertIs(requests[1]["allow_redirects"], False)
                self.assertLessEqual(requests[1]["timeout"].total, requests[0]["timeout"].total)
                self.assertEqual(guard.snapshot(CALL)["phase"], expected)
                self.assertNotIn("never-forward", json.dumps(result))
                self.assertNotIn(CALL, json.dumps(result))
                if status == 200:
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(result["_native_deposit_attempt"]["http_status"], 202)
                else:
                    self.assertNotEqual(result["status"], "success")
                    with self.assertRaises(ValueError):
                        guard.consume_confirmed_tool_parameters(CALL, {})
                if status == 404:
                    self.assertEqual(result["_native_deposit"]["error"], "deposit_not_found")
                    self.assertEqual(result["_native_deposit_attempt"]["http_status"], 202)

    async def test_later_selected_tool_reads_original_not_new_model_or_caller_fields(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        tool = InCallHTTPTool(config())
        await execute_many(tool, params, ctx, [
            ({"ok": False, "status": "pending", "error": "helper_timeout"}, 202, None),
            ({"ok": False, "status": "pending_or_ambiguous", "artifact_verified": False,
              "replay": False, "error": "helper_timeout"}, 202, None),
        ])
        ticket = guard.reconciliation_ticket(CALL)
        ctx.native_deposit_reconcile = ticket
        ctx.caller_name, ctx.caller_number = "new caller value", "new callback value"
        result, requests = await execute_many(
            tool, {"target": "wrong", "message": "wrong", "call_id": "wrong"}, ctx, [
                ({"ok": True, "status": "verified_saved", "artifact_verified": True,
                  "replay": True, "native_id": "fixture-native-artifact"}, 200, None),
            ])
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0]["url"].endswith("/v1/message-deposit/reconcile"))
        self.assertEqual(requests[0]["json"], json.loads(ticket.envelope_json))
        self.assertEqual(guard.snapshot(CALL)["phase"], "acknowledged")
        self.assertEqual(result["status"], "success")

    async def test_late_reconciliation_after_cleanup_and_new_attempt_cannot_publish(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        entered, release = asyncio.Event(), asyncio.Event()

        class BlockedProof(Content):
            async def iter_chunked(self, size):
                entered.set()
                await release.wait()
                async for chunk in super().iter_chunked(size):
                    yield chunk

        proof = {"ok": True, "status": "verified_saved", "artifact_verified": True,
                 "replay": True, "native_id": "fixture-native-artifact"}
        task = asyncio.create_task(execute_many(InCallHTTPTool(config()), params, ctx, [
            ({"ok": False, "status": "pending", "error": "helper_timeout"}, 202, None),
            (proof, 200, BlockedProof(proof)),
        ]))
        await asyncio.wait_for(entered.wait(), 1)
        old_ticket = guard.reconciliation_ticket(CALL)
        guard.cleanup(CALL)
        _, newer_ctx = begin(guard)
        release.set()
        result, requests = await task
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(ctx.native_deposit_attempt, newer_ctx.native_deposit_attempt)
        self.assertEqual(guard.execution_generation(CALL), newer_ctx.native_deposit_attempt)
        self.assertIsNone(guard.reconciliation_ticket(CALL))
        self.assertNotEqual(result["status"], "success")
        self.assertNotEqual(result.get("_direct_response_text"), SAVED["spoken_response"])
        self.assertFalse(guard.note_reconciliation_result(old_ticket, native_outcome="verified"))

    async def test_executing_conversation_retains_exact_attempt_until_native_proof(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        body = {"call_id": CALL, "request_id": CALL, **params, "confirmed": True}
        guard.capture_dispatch_envelope(CALL, body, generation=ctx.native_deposit_attempt)
        state = guard._states[CALL]
        for text in ("Change the message.", "can we try again?", "Did you get that?",
                     "Hang up.", "Cancel", "Leave a message for someone else.",
                     "The sky is green.", "Yes."):
            with self.subTest(text=text):
                decision = guard.decide(
                    CALL, text, enabled=True, caller_controls=True,
                    caller_end_markers=["hang up"])
                self.assertIn(decision.kind, ("pass", "suppress"))
                self.assertIs(guard._states[CALL], state)
                self.assertEqual(state.dispatch_generation, ctx.native_deposit_attempt)
                self.assertEqual(json.loads(state.dispatched_envelope), body)
                with self.assertRaises(ValueError):
                    guard.consume_confirmed_tool_parameters(CALL, {})
        guard.note_tool_result(CALL, success=False, native_outcome="pending",
                               dispatch_generation=ctx.native_deposit_attempt)
        ticket = guard.reconciliation_ticket(CALL)
        self.assertEqual(json.loads(ticket.envelope_json), body)
        self.assertTrue(guard.note_reconciliation_result(ticket, native_outcome="verified"))
        self.assertEqual(guard.snapshot(CALL)["phase"], "acknowledged")
        guard.cleanup(CALL)
        self.assertIsNone(guard.snapshot(CALL))

    async def test_each_proven_tts_failure_allows_fresh_correction_confirmation(self):
        for code in ("tts_client_failed", "tts_request_failed", "tts_response_invalid", "tts_pcm_invalid"):
            with self.subTest(code=code):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                first, original = await execute_many(InCallHTTPTool(config()), params, ctx, [
                    ({"ok": False, "error": code}, 502, None), (ABSENT, 404, None)])
                self.assertEqual(first["_native_deposit_attempt"], {
                    "outcome": "not_deposited", "error": code, "http_status": 502, "rearm_required": True})
                self.assertEqual(original[0]["json"], original[1]["json"])
                decision = guard.decide(CALL, "Change it to the sky is green.", enabled=True, caller_controls=True)
                self.assertIn("the sky is green", decision.text)
                guard.decide(CALL, "Yes", enabled=True, caller_controls=True)
                corrected = guard.consume_confirmed_tool_parameters(CALL, {})
                ctx.native_deposit_attempt = guard.execution_generation(CALL)
                second, retried = await execute_many(InCallHTTPTool(config()), corrected, ctx, [(ABSENT, 404, None), (SAVED, 200, None)])
                self.assertEqual(second["status"], "success")
                self.assertEqual(retried[0]["json"], original[0]["json"])
                self.assertEqual(retried[1]["json"]["message"], "the sky is green.")
                self.assertEqual(retried[1]["json"]["request_id"], original[0]["json"]["request_id"])

    async def test_pending_202_never_success_or_old_payload_reauthorization(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        result, sent = await execute(
            InCallHTTPTool(config()), params, ctx,
            {"ok": False, "status": "pending", "error": "native_outcome_ambiguous",
             "spoken_response": "Untrusted success"}, 202)
        self.assertEqual(normalize_tool_terminal_status(result), "failure")
        self.assertEqual(result["status"], "pending")
        self.assertNotIn("Untrusted success", json.dumps(result))
        ticket = guard.reconciliation_ticket(CALL)
        self.assertEqual(json.loads(ticket.envelope_json), sent["json"])
        for text in ("Change it to green", "Change the message", "Did you get that?", "Hang up", "Yes"):
            self.assertEqual(guard.decide(CALL, text, enabled=True, caller_controls=True).kind, "pass")
            with self.assertRaises(ValueError):
                guard.consume_confirmed_tool_parameters(CALL, {})
            self.assertEqual(guard.reconciliation_ticket(CALL), ticket)

    async def test_helper_errors_not_assumed_preactuation(self):
        for code in ("helper_spawn_failed", "helper_timeout", "helper_stdin_failed", "helper_response_invalid"):
            with self.subTest(code=code):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                result, _ = await execute(InCallHTTPTool(config()), params, ctx, {"ok": False, "error": code}, 502)
                self.assertEqual(result["_native_deposit"]["outcome"], "unknown")
                self.assertEqual(guard.snapshot(CALL)["phase"], "reconciling")

    async def test_tts_code_wrong_http_or_rejected_shape_is_not_absence(self):
        for status, data in (
            (503, {"ok": False, "error": "tts_request_failed"}),
            (502, {"ok": False, "status": "rejected", "error": "tts_request_failed"}),
            (502, {"ok": 0, "error": "tts_request_failed"})):
            with self.subTest(status=status, data=data):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                result, _ = await execute(InCallHTTPTool(config()), params, ctx, data, status)
                self.assertEqual(result["_native_deposit"]["outcome"], "unknown")

    async def test_verified_saved_is_same_main_and_mini_phrase(self):
        for context_name in ("aimee_main", "aimee"):
            with self.subTest(context=context_name):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                ctx.context_name = context_name
                ctx.native_deposit_required = context_name == "aimee_main"
                result, _ = await execute(InCallHTTPTool(config()), params, ctx, SAVED)
                self.assertEqual(result["_direct_response_text"], SAVED["spoken_response"])
                self.assertEqual(result["status"], "success")
                self.assertEqual(guard.snapshot(CALL)["phase"], "acknowledged")

    async def test_saved_requires_literal_flags_and_native_artifact(self):
        for key, value in (("ok", 1), ("artifact_verified", 1), ("native_id", ""), ("status", "pending")):
            with self.subTest(key=key):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                result, _ = await execute(InCallHTTPTool(config()), params, ctx, dict(SAVED, **{key: value}))
                self.assertNotEqual(result["status"], "success")

    async def test_no_spoken_field_still_records_verified_native_outcome(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        data = {k: v for k, v in SAVED.items() if k != "spoken_response"}
        result, _ = await execute(InCallHTTPTool(config()), params, ctx, data)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["_native_deposit"]["outcome"], "verified")
        self.assertEqual(guard.snapshot(CALL)["phase"], "acknowledged")
        with self.assertRaises(ValueError):
            guard.consume_confirmed_tool_parameters(CALL, {})

    async def test_error_body_and_exception_secret_never_returned_or_logged(self):
        for data in (
            {"ok": False, "error": "secret_dummy_value", "token": "body-secret",
             "message": "private caller message", "call_id": "private-raw-id"},
            b'{"error":"body-secret","message":"private caller message"'):
            with self.subTest(data_type=type(data).__name__):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                with self.assertLogs("src.tools.http.in_call_lookup", level="DEBUG") as logs:
                    with patch("src.tools.http.in_call_lookup.debug_enabled", return_value=True):
                        result, _ = await execute(InCallHTTPTool(config()), params, ctx, data, 502)
                rendered = json.dumps(result) + "\n".join(logs.output)
                for value in ("body-secret", "secret_dummy_value", "private caller message", "private-raw-id"):
                    self.assertNotIn(value, rendered)
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        tool = InCallHTTPTool(config())
        tool._build_substitution_context = AsyncMock(side_effect=RuntimeError("exception-secret"))
        with self.assertLogs("src.tools.http.in_call_lookup", level="DEBUG") as logs:
            result = await tool.execute(params, ctx)
        self.assertNotIn("exception-secret", json.dumps(result) + "\n".join(logs.output))
        self.assertEqual(result["_native_deposit"]["outcome"], "not_deposited")

    async def test_body_cap_is_enforced_even_on_error(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        result, _ = await execute(InCallHTTPTool(config()), params, ctx, b"x" * 65537, 502)
        self.assertEqual(result["error"], "deposit_response_too_large")
        self.assertEqual(guard.snapshot(CALL)["phase"], "reconciling")

    async def test_exact_private_context_not_model_identity_is_captured(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        result, sent = await execute(InCallHTTPTool(config()), params, ctx, {"ok": False, "error": "helper_timeout"}, 502)
        envelope = json.loads(guard.reconciliation_ticket(CALL).envelope_json)
        self.assertEqual(envelope, sent["json"])
        for key, value in (("call_id", ctx.call_id), ("request_id", ctx.call_id),
                           ("caller_name", ctx.caller_name), ("callback_number", ctx.caller_number)):
            self.assertEqual(envelope[key], value)
        self.assertNotIn("dummy-only", json.dumps(envelope))
        self.assertNotIn(CALL, json.dumps(result))

    async def test_capture_omitted_optional_extra_required_drift(self):
        for omitted in ("caller_name", "callback_number", "urgency"):
            guard = PipelineMessageDepositGuard()
            params, ctx = begin(guard)
            body = {"call_id": CALL, "request_id": CALL, **params, "confirmed": True,
                    "caller_name": "Fixture", "callback_number": "fixture", "urgency": "normal"}
            del body[omitted]
            guard.capture_dispatch_envelope(CALL, body, generation=ctx.native_deposit_attempt)
            guard.note_tool_result(CALL, success=False, native_outcome="unknown",
                                   dispatch_generation=ctx.native_deposit_attempt)
            self.assertNotIn(omitted, json.loads(guard.reconciliation_ticket(CALL).envelope_json))
        for changes in ({"token": "never-capture"}, {"call_id": "foreign"}, {"target": "foreign"},
                        {"message": "changed"}, {"confirmed": 1}, {"request_id": ""},
                        {"caller_name": 3}, {"callback_number": {}}, {"urgency": False}):
            with self.subTest(changes=changes):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                body = {"call_id": CALL, "request_id": CALL, **params, "confirmed": True, **changes}
                with self.assertRaises(ValueError):
                    guard.capture_dispatch_envelope(CALL, body, generation=ctx.native_deposit_attempt)
        for omitted in ("call_id", "request_id", "target", "message", "confirmed"):
            guard = PipelineMessageDepositGuard()
            params, ctx = begin(guard)
            body = {"call_id": CALL, "request_id": CALL, **params, "confirmed": True}
            del body[omitted]
            with self.assertRaises(ValueError):
                guard.capture_dispatch_envelope(CALL, body, generation=ctx.native_deposit_attempt)

    async def test_cleanup_during_preparation_prevents_io_and_revival(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        entered, release = asyncio.Event(), asyncio.Event()
        tool = InCallHTTPTool(config())
        original = tool._build_substitution_context
        async def prepare(*args):
            entered.set()
            await release.wait()
            return await original(*args)
        tool._build_substitution_context = prepare
        task = asyncio.create_task(execute(tool, params, ctx, SAVED))
        await entered.wait()
        guard.cleanup(CALL)
        newer, newer_ctx = begin(guard)
        release.set()
        result, sent = await task
        self.assertIsNone(sent)
        self.assertEqual(guard.snapshot(CALL)["phase"], "executing")
        self.assertEqual(guard.execution_generation(CALL), newer_ctx.native_deposit_attempt)
        self.assertEqual(result["error"], "deposit_request_failed")

    async def test_cancelled_response_marks_uncertain_not_fresh_consent(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        entered = asyncio.Event()
        class Blocked:
            async def iter_chunked(self, size):
                entered.set()
                await asyncio.Event().wait()
                yield b""
        task = asyncio.create_task(execute(InCallHTTPTool(config()), params, ctx, {}, content=Blocked()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(guard.snapshot(CALL)["phase"], "reconciling")
        self.assertIsNotNone(guard.reconciliation_ticket(CALL))
        guard.decide(CALL, "Yes", enabled=True, caller_controls=True)
        with self.assertRaises(ValueError):
            guard.consume_confirmed_tool_parameters(CALL, {})

    async def test_absence_proof_allows_exact_correction_but_not_unconfirmed_mutation(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        await execute(InCallHTTPTool(config()), params, ctx, {"ok": False, "error": "helper_timeout"}, 502)
        ticket = guard.reconciliation_ticket(CALL)
        self.assertFalse(guard.note_reconciliation_result(ticket, native_outcome="pending"))
        self.assertTrue(guard.note_reconciliation_result(ticket, native_outcome="not_deposited"))
        with self.assertRaises(ValueError):
            guard.consume_confirmed_tool_parameters(CALL, {})
        decision = guard.decide(CALL, "Change it to the sky is green.", enabled=True, caller_controls=True)
        self.assertIn("the sky is green", decision.text)
        guard.decide(CALL, "Yes", enabled=True, caller_controls=True)
        self.assertEqual(guard.consume_confirmed_tool_parameters(CALL, {})["message"], "the sky is green.")
        self.assertFalse(guard.note_reconciliation_result(ticket, native_outcome="not_deposited"))

    async def test_conflict_pending_unknown_proof_never_allows_new_payload(self):
        for outcome in ("pending", "unknown", "conflicting"):
            guard = PipelineMessageDepositGuard()
            params, ctx = begin(guard)
            await execute(InCallHTTPTool(config()), params, ctx, {"ok": False, "error": "helper_timeout"}, 502)
            self.assertFalse(guard.note_reconciliation_result(guard.reconciliation_ticket(CALL), native_outcome=outcome))
            for text in ("Change it to sky green", "Yes"):
                guard.decide(CALL, text, enabled=True, caller_controls=True)
            with self.assertRaises(ValueError):
                guard.consume_confirmed_tool_parameters(CALL, {})

    async def test_saved_proof_blocks_replay(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        await execute(InCallHTTPTool(config()), params, ctx, {"ok": False, "error": "helper_timeout"}, 502)
        self.assertTrue(guard.note_reconciliation_result(guard.reconciliation_ticket(CALL), native_outcome="verified"))
        self.assertEqual(guard.snapshot(CALL)["phase"], "acknowledged")
        with self.assertRaises(ValueError):
            guard.consume_confirmed_tool_parameters(CALL, {})

    async def test_tampered_cross_call_and_stale_ticket_after_cleanup_rejected(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        await execute(InCallHTTPTool(config()), params, ctx, {"ok": False, "error": "helper_timeout"}, 502)
        ticket = guard.reconciliation_ticket(CALL)
        self.assertFalse(guard.note_reconciliation_result(replace(ticket, envelope_json="{}"), native_outcome="not_deposited"))
        other, other_ctx = begin(guard, "fixture-native-call-02")
        self.assertFalse(guard.note_reconciliation_result(replace(ticket, call_id=other_ctx.call_id), native_outcome="not_deposited"))
        guard.cleanup(CALL)
        self.assertFalse(guard.note_reconciliation_result(ticket, native_outcome="not_deposited"))
        newer, newer_ctx = begin(guard)
        guard.note_tool_result(CALL, success=True, native_outcome="verified", dispatch_generation=ticket.generation)
        self.assertEqual(guard.snapshot(CALL)["phase"], "executing")
        self.assertEqual(guard.execution_generation(CALL), newer_ctx.native_deposit_attempt)

    async def test_generic_http_failure_contract_unchanged(self):
        tool = InCallHTTPTool(config(name="deposit", return_raw_json=True))
        result, _ = await execute(tool, {}, ToolExecutionContext(call_id=CALL), {"value": 7}, 400)
        self.assertEqual(result, {
            "status": "failed", "message": tool.config.error_message,
            "_direct_response_text": tool.config.direct_failure_message})

    async def test_legacy_pbx_named_partial_http_body_keeps_original_success(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        ctx.native_deposit_required = False
        tool = InCallHTTPTool(config(body_template='{"target":"{target}","message":"{message}"}'))
        result, sent = await execute(tool, params, ctx, {"spoken_response": "Legacy saved."})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["_direct_response_text"], "Legacy saved.")
        self.assertNotIn("_native_deposit", result)
        self.assertEqual(sent["json"], params)

    async def test_main_partial_body_cannot_bypass_required_private_capture(self):
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        tool = InCallHTTPTool(config(body_template='{"target":"{target}","message":"{message}"}'))
        result, sent = await execute(tool, params, ctx, SAVED)
        self.assertIsNone(sent)
        self.assertEqual(result["_native_deposit"]["outcome"], "not_deposited")

    async def test_actual_direct_method_no_text_tts_error_cancel_late_generation(self):
        method = direct_method()
        for outcome, direct in (("not_deposited", None), ("unknown", "I lost that at my end."),
                                ("verified", SAVED["spoken_response"])):
            with self.subTest(outcome=outcome):
                guard = PipelineMessageDepositGuard()
                params, ctx = begin(guard)
                engine = SimpleNamespace(
                    _pipeline_message_deposit_guard=lambda: guard,
                    session_store=SimpleNamespace(upsert_call=AsyncMock()),
                    _pipeline_output_allowed=lambda *a, **k: True,
                    _pipeline_tts_uses_streaming=lambda pipeline: True,
                    _stream_pipeline_tts_text=AsyncMock(side_effect=RuntimeError("dummy-tts-failure")))
                result = {"status": "failed", "_native_deposit": {"outcome": outcome}}
                if direct:
                    result["_direct_response_text"] = direct
                    with self.assertRaises(RuntimeError):
                        await method(engine, CALL, SimpleNamespace(), SimpleNamespace(), [], result,
                                     tool_name="pbx_message_deposit", execution_context=ctx)
                else:
                    self.assertFalse(await method(
                        engine, CALL, SimpleNamespace(), SimpleNamespace(), [], result,
                        tool_name="pbx_message_deposit", execution_context=ctx))
                self.assertEqual(guard.snapshot(CALL)["phase"], {
                    "not_deposited": "awaiting_confirmation", "unknown": "reconciling",
                    "verified": "acknowledged"}[outcome])
                guard.cleanup(CALL)
                newer, newer_ctx = begin(guard)
                await method(engine, CALL, SimpleNamespace(), SimpleNamespace(), [],
                             {"status": "failed", "_native_deposit": {"outcome": "unknown"}},
                             tool_name="pbx_message_deposit", execution_context=ctx)
                self.assertEqual(guard.execution_generation(CALL), newer_ctx.native_deposit_attempt)
        guard = PipelineMessageDepositGuard()
        params, ctx = begin(guard)
        engine.session_store.upsert_call = AsyncMock(side_effect=asyncio.CancelledError())
        engine._pipeline_message_deposit_guard = lambda: guard
        with self.assertRaises(asyncio.CancelledError):
            await method(engine, CALL, SimpleNamespace(), SimpleNamespace(), [],
                         {"status": "pending", "_native_deposit": {"outcome": "pending"},
                          "_direct_response_text": "I lost that at my end."},
                         tool_name="pbx_message_deposit", execution_context=ctx)
        self.assertEqual(guard.snapshot(CALL)["phase"], "reconciling")

    def test_actual_primary_and_followup_invocations_pass_execution_context(self):
        tree = ast.parse((ROOT / "src/engine.py").read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_maybe_speak_direct_pipeline_tool_result"
                 and any(kw.arg == "tool_name" for kw in n.keywords)]
        self.assertEqual(len(calls), 2)
        for call in calls:
            keywords = {kw.arg: kw.value for kw in call.keywords}
            self.assertIsInstance(keywords["execution_context"], ast.Name)
            self.assertEqual(keywords["execution_context"].id, "tool_ctx")

    async def test_real_aiohttp_non2xx_and_total_read_deadline(self):
        requests = []
        release = asyncio.Event()
        async def handler(request):
            requests.append(await request.json())
            if len(requests) == 1:
                return web.json_response({"ok": False, "error": "tts_request_failed"}, status=502)
            response = web.StreamResponse(status=202, headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await release.wait()
            return response
        app = web.Application()
        app.router.add_post("/v1/message-deposit", handler)
        async def reconcile(request):
            requests.append(await request.json())
            return web.json_response(ABSENT, status=404)
        app.router.add_post("/v1/message-deposit/reconcile", reconcile)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            guard = PipelineMessageDepositGuard()
            params, ctx = begin(guard)
            tool = InCallHTTPTool(config(url=f"http://localhost:{port}/v1/message-deposit", timeout_ms=80))
            result = await tool.execute(params, ctx)
            self.assertEqual(result["_native_deposit"]["outcome"], "not_deposited")
            guard.decide(CALL, "Yes", enabled=True, caller_controls=True)
            params = guard.consume_confirmed_tool_parameters(CALL, {})
            ctx.native_deposit_attempt = guard.execution_generation(CALL)
            result = await asyncio.wait_for(tool.execute(params, ctx), timeout=1)
            self.assertEqual(result["_native_deposit"]["http_status"], 202)
            self.assertEqual(result["_native_deposit"]["outcome"], "unknown")
            self.assertEqual(guard.snapshot(CALL)["phase"], "reconciling")
            self.assertEqual(len(requests), 4)
        finally:
            release.set()
            await runner.cleanup()

    async def test_real_reconcile_trickle_and_blocked_reads_share_original_budget(self):
        for trickle in (False, True):
            with self.subTest(trickle=trickle):
                requests = []
                release = asyncio.Event()

                async def handler(request):
                    requests.append((request.path, await request.json()))
                    if request.path == "/v1/message-deposit":
                        await asyncio.sleep(0.03)
                        return web.json_response(
                            {"ok": False, "status": "pending", "error": "helper_timeout"},
                            status=202)
                    response = web.StreamResponse(status=202)
                    await response.prepare(request)
                    try:
                        if trickle:
                            while not release.is_set():
                                await response.write(b" ")
                                await asyncio.sleep(0.01)
                        else:
                            await release.wait()
                    except ConnectionError:
                        pass
                    return response

                app = web.Application()
                app.router.add_post("/v1/message-deposit", handler)
                app.router.add_post("/v1/message-deposit/reconcile", handler)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                port = site._server.sockets[0].getsockname()[1]
                try:
                    guard = PipelineMessageDepositGuard()
                    params, ctx = begin(guard)
                    tool = InCallHTTPTool(config(
                        url=f"http://localhost:{port}/v1/message-deposit", timeout_ms=120))
                    started = asyncio.get_running_loop().time()
                    result = await asyncio.wait_for(tool.execute(params, ctx), 1)
                    elapsed = asyncio.get_running_loop().time() - started
                    self.assertLess(elapsed, 0.5)
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(requests[0][1], requests[1][1])
                    self.assertEqual(requests[1][0], "/v1/message-deposit/reconcile")
                    self.assertNotEqual(result["status"], "success")
                    self.assertEqual(guard.snapshot(CALL)["phase"], "reconciling")
                    guard.decide(CALL, "Change it to green", enabled=True, caller_controls=True)
                    with self.assertRaises(ValueError):
                        guard.consume_confirmed_tool_parameters(CALL, {})
                finally:
                    release.set()
                    await runner.cleanup()

if __name__ == "__main__":
    unittest.main()
