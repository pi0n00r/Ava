"""
In-Call HTTP Lookup Tool - AI-invoked HTTP requests during conversation.

Allows AI to make HTTP requests mid-call to fetch data (e.g., check availability,
lookup order status) and receive results to inform the conversation.
"""

import asyncio
import os
import re
import json
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from src.tools.http.path_utils import extract_path

import aiohttp

from src.tools.base import Tool, ToolDefinition, ToolCategory, ToolPhase, ToolParameter
from src.tools.context import ToolExecutionContext
from src.tools.http.debug_trace import (
    BODY_CAPABLE_HTTP_METHODS,
    build_var_snapshot,
    debug_enabled,
    extract_used_brace_vars,
    extract_used_env_vars,
    preview,
    redact_headers,
)

logger = logging.getLogger(__name__)

# Rita emits these only after native reconcile proved absence, before deposit.
_DEPOSIT_TTS_ERRORS = frozenset({
    "tts_client_failed", "tts_request_failed",
    "tts_response_invalid", "tts_pcm_invalid",
})
_DEPOSIT_ERROR_CODES = _DEPOSIT_TTS_ERRORS | frozenset({
    "authentication_required", "invalid_identity", "confirmation_required",
    "urgency_invalid", "message_invalid", "message_narration_too_large",
    "invalid_target", "unknown_target", "ambiguous_target",
    "freepbx_destination_missing", "freepbx_destination_ambiguous",
    "freepbx_client_failed", "freepbx_token_failed", "freepbx_token_rejected",
    "freepbx_token_invalid", "freepbx_graphql_failed", "freepbx_graphql_rejected",
    "freepbx_graphql_invalid", "freepbx_graphql_errors",
    "freepbx_graphql_missing_data", "freepbx_inventory_inconsistent",
    "freepbx_invalid_extension", "helper_header_failed", "helper_spawn_failed",
    "helper_stdin_failed", "helper_timeout", "helper_wait_failed",
    "helper_response_missing", "helper_response_invalid", "helper_task_failed",
    "call_channel_not_found", "call_channel_ambiguous",
    "call_pjsip_channel_not_found", "call_pjsip_channel_ambiguous",
    "ami_timeout", "ami_connect_failed", "ami_banner_timeout",
    "ami_banner_failed", "ami_banner_invalid", "ami_write_failed",
    "ami_login_rejected", "ami_missing_response",
    "request_replay_conflict", "call_uuid_payload_conflict",
    "original_request_unknown", "original_request_active",
    "original_request_outcome_uncertain", "original_request_terminal_nonabsence",
    "deposit_not_found", "native_outcome_ambiguous", "native_rejected",
    "imap_uid_delta_not_one",
})
_DEPOSIT_SAVED_RESPONSE = "Thanks. I'll make sure they get it."


@dataclass
class InCallHTTPConfig:
    """Configuration for an in-call HTTP lookup tool instance."""
    name: str
    description: str = ""
    enabled: bool = True
    is_global: bool = False
    timeout_ms: int = 5000
    
    # Hold audio (played if request exceeds threshold)
    hold_audio_file: Optional[str] = None
    hold_audio_threshold_ms: int = 500
    
    # HTTP request configuration
    url: str = ""
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None
    
    # AI-provided parameters (registered with provider for function calling)
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    
    # Response handling
    output_variables: Dict[str, str] = field(default_factory=dict)  # var_name -> JSON path
    return_raw_json: bool = False  # If True, return full JSON to AI
    # Opt-in only: select one operator-vetted caller-facing response value.
    direct_response_json_path: Optional[str] = None
    direct_failure_message: Optional[str] = None
    # Pipeline-only, caller-side non-speech wait layer. The built-in CC0
    # typing asset is fixed; configuration only opts this tool in.
    caller_wait_ambience: bool = False
    
    # Response limits
    max_response_size_bytes: int = 65536  # 64KB max
    
    # Error handling
    error_message: str = "I'm sorry, I couldn't retrieve that information right now."


class InCallHTTPTool(Tool):
    """
    In-call HTTP lookup tool for AI-invoked requests during conversation.
    
    Configured via YAML, allows AI to make HTTP requests mid-call and receive
    results. Supports both selected output variables and raw JSON responses.
    
    Example config:
    ```yaml
    in_call_tools:
      check_appointment:
        kind: in_call_http_lookup
        enabled: true
        description: "Check if an appointment slot is available"
        timeout_ms: 5000
        hold_audio_file: "custom/please-wait"
        hold_audio_threshold_ms: 500
        url: "https://api.example.com/appointments/check"
        method: POST
        headers:
          Authorization: "Bearer ${API_KEY}"
          Content-Type: "application/json"
        body_template: |
          {
            "caller_number": "{caller_number}",
            "date": "{date}",
            "time": "{time}"
          }
        parameters:
          - name: date
            type: string
            description: "Appointment date in YYYY-MM-DD format"
            required: true
          - name: time
            type: string
            description: "Appointment time in HH:MM format"
            required: true
        output_variables:
          available: "available"
          next_available_slot: "next_slot"
        return_raw_json: false
        error_message: "I couldn't check the appointment availability. Would you like me to try again?"
    ```
    """
    
    def __init__(self, config: InCallHTTPConfig):
        self.config = config
        
        # Convert config parameters to ToolParameter objects
        tool_params = []
        for p in config.parameters:
            tool_params.append(ToolParameter(
                name=p.get('name', ''),
                type=p.get('type', 'string'),
                description=p.get('description', ''),
                required=p.get('required', False),
                enum=p.get('enum'),
                default=p.get('default'),
            ))
        
        self._definition = ToolDefinition(
            name=config.name,
            description=config.description or f"HTTP lookup: {config.name}",
            category=ToolCategory.BUSINESS,
            phase=ToolPhase.IN_CALL,
            is_global=config.is_global,
            parameters=tool_params,
            timeout_ms=config.timeout_ms,
            hold_audio_file=config.hold_audio_file,
            hold_audio_threshold_ms=config.hold_audio_threshold_ms,
        )
        self.caller_wait_ambience = bool(config.caller_wait_ambience)
    
    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    
    @staticmethod
    def _normalize_direct_response(value: Any) -> Optional[str]:
        """Return one bounded spoken line, or fail closed."""
        if not isinstance(value, str):
            return None
        rendered = " ".join(value.split()).strip()
        if not rendered or len(rendered) > 500:
            return None
        return rendered

    def _failure_result(self, status: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": status,
            "message": self.config.error_message,
        }
        direct_failure = self._normalize_direct_response(
            self.config.direct_failure_message
        )
        if direct_failure is not None:
            result["_direct_response_text"] = direct_failure
        return result

    def _direct_response_text(self, data: Any) -> Optional[str]:
        """Extract only the explicitly configured caller-facing response field."""
        path = str(self.config.direct_response_json_path or "").strip()
        if not path:
            return None
        try:
            return self._normalize_direct_response(self._extract_path(data, path))
        except Exception:
            logger.warning(
                "Direct response path could not be resolved: %s path=%s",
                self.config.name,
                path,
            )
            return None
    
    def _deposit_failure(
        self, error: str, *, http_status: Optional[int] = None,
        outcome: str = "unknown",
    ) -> Dict[str, Any]:
        """Retain machine diagnostics, never the native response body."""
        result = self._failure_result("pending" if outcome == "pending" else "failed")
        result["error"] = error
        result["_native_deposit"] = {
            "outcome": outcome, "error": error, "http_status": http_status,
        }
        logger.warning(
            "Native message deposit outcome: outcome=%s error=%s http_status=%s",
            outcome, error, http_status,
        )
        return result

    async def _deposit_response(self, response: Any, *, readonly: bool = False) -> Dict[str, Any]:
        """Interpret only the existing managed PBX deposit response contract."""
        status = response.status if type(response.status) is int else None
        try:
            max_bytes = int(self.config.max_response_size_bytes)
            if max_bytes <= 0:
                return self._deposit_failure("deposit_response_limit_invalid", http_status=status)
            chunks = []
            total = 0
            async for chunk in response.content.iter_chunked(8192):
                total += len(chunk)
                if total > max_bytes:
                    return self._deposit_failure("deposit_response_too_large", http_status=status)
                chunks.append(chunk)
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except (ValueError, UnicodeError):
            return self._deposit_failure("deposit_response_invalid", http_status=status)
        except Exception:
            return self._deposit_failure("deposit_response_read_failed", http_status=status)
        if not isinstance(data, dict):
            return self._deposit_failure("deposit_response_invalid", http_status=status)

        raw_error = data.get("error")
        error = raw_error if isinstance(raw_error, str) and raw_error in _DEPOSIT_ERROR_CODES else (
            "deposit_error_unrecognized"
        )
        if readonly:
            return self._reconciliation_response(data, status, error)
        if (
            status == 502 and data.get("ok") is False
            and data.get("status") is None and error in _DEPOSIT_TTS_ERRORS
        ):
            result = self._deposit_failure(error, http_status=status, outcome="not_deposited")
            result["_native_deposit"]["rearm_required"] = True
            return result
        if status == 202 and data.get("ok") is False and data.get("status") == "pending":
            return self._deposit_failure(error, http_status=status, outcome="pending")
        if not (
            status == 200 and data.get("ok") is True
            and data.get("status") == "saved" and data.get("artifact_verified") is True
            and isinstance(data.get("native_id"), str) and data["native_id"].strip()
        ):
            return self._deposit_failure(error, http_status=status)

        # Success is native artifact verification, not HTTP 2xx or human receipt.
        native = {"outcome": "verified", "error": None, "http_status": status}
        data = {key: data[key] for key in (
            "ok", "status", "artifact_verified", "replay", "native_id", "spoken_response",
        ) if key in data}
        direct_response = self._direct_response_text(data)
        if self.config.direct_response_json_path and direct_response is None:
            result = self._deposit_failure("deposit_spoken_response_invalid", http_status=status)
            result["_native_deposit"] = native
            return result
        result = {"status": "success", "_native_deposit": native}
        if direct_response is not None:
            result["_direct_response_text"] = direct_response
        if self.config.return_raw_json:
            result["data"] = data
            result["message"] = "Retrieved data successfully."
        else:
            result["data"] = self._extract_output_variables(data)
            result["message"] = self._build_result_message(result["data"])
        return result

    def _reconciliation_response(
        self, data: Dict[str, Any], status: Optional[int], error: str,
    ) -> Dict[str, Any]:
        """Consume the Rita read-only status union without native PBX logic."""
        if (
            status == 200 and data.get("status") == "verified_saved"
            and data.get("ok") is True and data.get("artifact_verified") is True
            and type(data.get("replay")) is bool
            and isinstance(data.get("native_id"), str) and data["native_id"].strip()
        ):
            result = {
                "status": "success",
                "data": {key: data[key] for key in (
                    "ok", "status", "artifact_verified", "replay", "native_id",
                )},
                "message": "The existing native voicemail artifact is verified.",
                "_native_deposit": {"outcome": "verified", "error": None, "http_status": status},
            }
            if self.config.direct_response_json_path == "spoken_response":
                # Reuse Rita's existing saved-artifact phrase, not a response body.
                result["_direct_response_text"] = _DEPOSIT_SAVED_RESPONSE
            return result
        if data.get("ok") is False and data.get("artifact_verified") is False and data.get("replay") is False:
            if status == 404 and data.get("status") == "definitively_not_found":
                # 8162495e serializes the original operation and grants rearm
                # only after terminal pre-actuation absence of this exact body.
                return self._deposit_failure("deposit_not_found", http_status=status, outcome="not_deposited")
            if status == 409 and data.get("status") == "conflicting":
                return self._deposit_failure(error, http_status=status, outcome="conflicting")
            if status == 202 and data.get("status") == "pending_or_ambiguous":
                return self._deposit_failure(error, http_status=status, outcome="pending")
        return self._deposit_failure("deposit_reconcile_response_invalid", http_status=status)

    async def _reconcile_request(
        self, session: Any, request_kwargs: Dict[str, Any], context: Any,
        ticket: Any, deadline: float, *, rearm: bool = False,
    ):
        """One read-only request within the current HTTP operation's total budget."""
        guard = context.native_deposit_guard
        current_ticket = (guard.rearm_ticket(context.call_id, generation=context.native_deposit_attempt)
                          if rearm else guard.reconciliation_ticket(context.call_id))
        if current_ticket != ticket or ticket.call_id != context.call_id:
            return self._deposit_failure("deposit_reconcile_stale_request"), False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if rearm:
                guard.note_rearm_result(ticket, generation=context.native_deposit_attempt, native_outcome="unknown")
            return self._deposit_failure("deposit_reconcile_budget_expired"), False
        try:
            parsed = urlsplit(request_kwargs["url"])
            if not parsed.path.rstrip("/").endswith("/v1/message-deposit"):
                if rearm:
                    guard.note_rearm_result(ticket, generation=context.native_deposit_attempt, native_outcome="unknown")
                return self._deposit_failure("deposit_reconcile_route_invalid"), False
            readonly_kwargs = dict(request_kwargs)
            readonly_kwargs.pop("data", None)
            readonly_kwargs.update(
                url=urlunsplit(parsed._replace(path=parsed.path.rstrip("/") + "/reconcile")),
                json=json.loads(ticket.envelope_json),
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=remaining),
            )
            async with session.request(**readonly_kwargs) as response:
                result = await self._deposit_response(response, readonly=True)
        except asyncio.CancelledError:
            if rearm:
                guard.note_rearm_result(ticket, generation=context.native_deposit_attempt, native_outcome="unknown")
            raise
        except Exception:
            result = self._deposit_failure("deposit_reconcile_transport_failed")
        if rearm:
            if result["_native_deposit"]["outcome"] == "verified" and request_kwargs.get("json") != json.loads(ticket.envelope_json):
                result = self._deposit_failure("call_uuid_payload_conflict", http_status=result["_native_deposit"]["http_status"], outcome="conflicting")
            applied = guard.note_rearm_result(
                ticket, generation=context.native_deposit_attempt,
                native_outcome=result["_native_deposit"]["outcome"],
            )
        else:
            applied = guard.note_reconciliation_result(
                ticket, native_outcome=result["_native_deposit"]["outcome"],
            )
        if result["_native_deposit"]["outcome"] in {"verified", "not_deposited"} and not applied:
            result = self._deposit_failure("deposit_reconcile_stale_result")
            result.pop("_direct_response_text", None)
        return result, applied

    @staticmethod
    def _note_deposit_result(result: Dict[str, Any], context: Any) -> Dict[str, Any]:
        guard = getattr(context, "native_deposit_guard", None)
        if guard is not None:
            guard.note_tool_result(
                context.call_id, success=result.get("status") == "success",
                native_outcome=result["_native_deposit"]["outcome"],
                dispatch_generation=context.native_deposit_attempt,
                native_rearm_required=result["_native_deposit"].get("rearm_required") is True,
            )
        return result

    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, Any]:
        """
        Execute the HTTP lookup and return results to AI.
        
        Args:
            parameters: AI-provided parameters
            context: ToolExecutionContext with call info
        
        Returns:
            Dictionary with:
            - status: "success" | "failed" | "error"
            - message: Human-readable message for AI
            - data: Output variables or raw JSON (if return_raw_json=True)
        """
        deposit_name = self.config.name == "pbx_message_deposit"
        native_deposit = deposit_name and getattr(context, "native_deposit_required", False) is True
        request_dispatched = False
        native_guard = getattr(context, "native_deposit_guard", None) if deposit_name else None
        readonly_ticket = getattr(context, "native_deposit_reconcile", None) if deposit_name else None
        if not self.config.enabled:
            if native_deposit:
                return self._note_deposit_result(
                    self._deposit_failure("deposit_tool_disabled", outcome="not_deposited"), context,
                )
            logger.debug(f"In-call HTTP tool disabled: {self.config.name}")
            return self._failure_result("failed")
        
        if not self.config.url:
            if native_deposit:
                return self._note_deposit_result(
                    self._deposit_failure("deposit_url_missing", outcome="not_deposited"), context,
                )
            logger.warning(f"In-call HTTP tool has no URL configured: {self.config.name}")
            return self._failure_result("error")
        
        try:
            started = time.monotonic()
            # Build substitution context (context vars + pre-call results + AI params)
            if deposit_name:
                sub_context = await asyncio.wait_for(
                    self._build_substitution_context(parameters, context),
                    timeout=self.config.timeout_ms / 1000.0,
                )
            else:
                sub_context = await self._build_substitution_context(parameters, context)
            
            # Build request
            url = self._substitute_variables(self.config.url, sub_context)
            headers = {
                k: self._substitute_variables(v, sub_context)
                for k, v in self.config.headers.items()
            }
            query_params = {
                k: self._substitute_variables(v, sub_context)
                for k, v in self.config.query_params.items()
            }
            
            body = None
            json_body = None
            method = str(self.config.method or "GET").strip().upper()
            if method in BODY_CAPABLE_HTTP_METHODS and self.config.body_template:
                # Escape model- and caller-supplied values as JSON string
                # content. URL, header, and query substitutions remain literal.
                body_str = self._substitute_variables(
                    self.config.body_template,
                    sub_context,
                    json_escape=True,
                )
                # Try to parse as JSON for proper Content-Type handling
                try:
                    json_body = json.loads(body_str)
                except json.JSONDecodeError:
                    body = body_str

            # Retain legacy PBX-named generic HTTP adapters. Rita's closed
            # request envelope identifies its native result contract; the main
            # trusted relay additionally requires that envelope before any IO.
            if deposit_name and isinstance(json_body, dict) and {
                "call_id", "request_id", "target", "message", "confirmed",
            } <= json_body.keys():
                native_deposit = True

            if debug_enabled(logger) and not native_deposit:
                used_brace = extract_used_brace_vars(
                    self.config.url,
                    *(self.config.headers or {}).values(),
                    *(self.config.query_params or {}).values(),
                    self.config.body_template,
                )
                used_env = extract_used_env_vars(
                    self.config.url,
                    *(self.config.headers or {}).values(),
                    *(self.config.query_params or {}).values(),
                    self.config.body_template,
                )
                logger.debug(
                    "[HTTP_TOOL_TRACE] request_resolved in_call tool=%s method=%s url=%s headers=%s params=%s body=%s json_body=%s vars=%s call_id=%s",
                    self.config.name,
                    method,
                    url,
                    redact_headers(headers),
                    query_params,
                    preview(body),
                    preview(json.dumps(json_body)) if json_body is not None else "",
                    build_var_snapshot(
                        used_brace_vars=used_brace,
                        used_env_vars=used_env,
                        values=sub_context,
                        env=os.environ,
                    ),
                    context.call_id,
                )
            
            logger.info(
                f"Executing in-call HTTP tool: {self.config.name}",
                extra={
                    "method": method,
                    "url": self._redact_url(url),
                    "call_id": context.call_id,
                }
            )
            
            # Make request
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_ms / 1000.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                request_kwargs = {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "params": query_params if query_params else None,
                }
                
                if json_body is not None:
                    request_kwargs["json"] = json_body
                elif body is not None:
                    request_kwargs["data"] = body

                deadline = started + self.config.timeout_ms / 1000.0
                if readonly_ticket is not None:
                    proof, _applied = await self._reconcile_request(
                        session, request_kwargs, context, readonly_ticket, deadline,
                    )
                    return proof
                
                if native_deposit and native_guard is not None:
                    native_guard.capture_dispatch_envelope(
                        context.call_id, json_body,
                        generation=context.native_deposit_attempt,
                    )
                    rearm_ticket = getattr(context, "native_deposit_rearm", None) or native_guard.rearm_ticket(
                        context.call_id, generation=context.native_deposit_attempt,
                    )
                    if rearm_ticket is not None:
                        proof, applied = await self._reconcile_request(
                            session, request_kwargs, context, rearm_ticket, deadline, rearm=True,
                        )
                        if not (applied and proof["_native_deposit"]["outcome"] == "not_deposited"):
                            return proof
                if native_deposit:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return self._note_deposit_result(
                            self._deposit_failure("deposit_request_budget_expired", outcome="not_deposited"), context,
                        )
                    request_kwargs["timeout"] = aiohttp.ClientTimeout(total=remaining)
                request_dispatched = True
                async with session.request(**request_kwargs) as response:
                    if native_deposit:
                        result = self._note_deposit_result(await self._deposit_response(response), context)
                        ticket = native_guard.reconciliation_ticket(context.call_id) if native_guard is not None else None
                        if ticket is not None and ticket.generation == context.native_deposit_attempt:
                            proof, applied = await self._reconcile_request(
                                session, request_kwargs, context, ticket, deadline,
                            )
                            if applied:
                                proof["_native_deposit_attempt"] = result["_native_deposit"]
                                return proof
                            result["_native_reconciliation"] = proof["_native_deposit"]
                        return result
                    # Check response size
                    content_length = response.headers.get('Content-Length')
                    if content_length and int(content_length) > self.config.max_response_size_bytes:
                        logger.warning(
                            f"Response too large: {self.config.name}",
                            extra={"size": content_length, "max": self.config.max_response_size_bytes}
                        )
                        return self._failure_result("error")
                    
                    if not 200 <= response.status < 300:
                        logger.warning(
                            f"In-call HTTP tool returned non-2xx: {self.config.name}",
                            extra={"status": response.status, "call_id": context.call_id}
                        )
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            body_preview = ""
                            try:
                                body_preview = preview(await response.text())
                            except Exception as e:
                                body_preview = f"<failed to read body: {e}>"
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_non_2xx in_call tool=%s status=%s elapsed_ms=%s body_preview=%s call_id=%s",
                                self.config.name,
                                response.status,
                                elapsed_ms,
                                body_preview,
                                context.call_id,
                            )
                        return self._failure_result("failed")
                    
                    # Read body with enforced size limit (do not trust Content-Length header).
                    body_bytes = b""
                    try:
                        max_bytes = int(self.config.max_response_size_bytes or 0)
                        if max_bytes <= 0:
                            logger.warning(
                                "Invalid max_response_size_bytes for %s: %s",
                                self.config.name,
                                self.config.max_response_size_bytes,
                            )
                            return self._failure_result("error")

                        total = 0
                        chunks: list[bytes] = []
                        async for chunk in response.content.iter_chunked(8192):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                logger.warning(
                                    "Response too large: %s max=%s",
                                    self.config.name,
                                    max_bytes,
                                )
                                if debug_enabled(logger):
                                    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                                    logger.debug(
                                        "[HTTP_TOOL_TRACE] response_too_large in_call tool=%s status=%s elapsed_ms=%s body_len=%s max=%s call_id=%s",
                                        self.config.name,
                                        getattr(response, "status", None),
                                        elapsed_ms,
                                        total,
                                        max_bytes,
                                        context.call_id,
                                    )
                                return self._failure_result("error")
                            chunks.append(chunk)

                        body_bytes = b"".join(chunks)
                        charset = getattr(response, "charset", None) or "utf-8"
                        body_text = body_bytes.decode(charset, errors="replace")
                        data = json.loads(body_text) if body_text.strip() else {}
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON response: {self.config.name} error={e}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_invalid_json in_call tool=%s elapsed_ms=%s body_len=%s body_preview=%s call_id=%s error=%s",
                                self.config.name,
                                elapsed_ms,
                                len(body_bytes or b""),
                                preview(body_bytes),
                                context.call_id,
                                str(e),
                            )
                        return self._failure_result("error")
                    except Exception as e:
                        logger.warning(f"Failed to read response: {self.config.name} error={e}")
                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] response_read_failed in_call tool=%s status=%s elapsed_ms=%s error=%s body_len=%s body_preview=%s call_id=%s",
                                self.config.name,
                                getattr(response, "status", None),
                                elapsed_ms,
                                str(e),
                                len(body_bytes or b""),
                                preview(body_bytes),
                                context.call_id,
                            )
                        return self._failure_result("error")

                    if debug_enabled(logger):
                        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                        logger.debug(
                            "[HTTP_TOOL_TRACE] response_ok in_call tool=%s status=%s elapsed_ms=%s body_preview=%s call_id=%s",
                            self.config.name,
                            response.status,
                            elapsed_ms,
                            preview(body_text),
                            context.call_id,
                        )
                    
                    # Build result
                    result = {
                        "status": "success",
                    }

                    direct_response = self._direct_response_text(data)
                    if self.config.direct_response_json_path and direct_response is None:
                        logger.warning(
                            "Configured direct response is missing or invalid: %s path=%s",
                            self.config.name,
                            self.config.direct_response_json_path,
                        )
                        return self._failure_result("failed")
                    if direct_response is not None:
                        result["_direct_response_text"] = direct_response
                    
                    if self.config.return_raw_json:
                        # Return full JSON to AI
                        result["data"] = data
                        result["message"] = f"Retrieved data successfully."
                    else:
                        # Extract output variables
                        extracted = self._extract_output_variables(data)
                        result["data"] = extracted
                        # Build human-readable message
                        result["message"] = self._build_result_message(extracted)

                        if debug_enabled(logger):
                            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
                            logger.debug(
                                "[HTTP_TOOL_TRACE] outputs in_call tool=%s elapsed_ms=%s outputs=%s call_id=%s",
                                self.config.name,
                                elapsed_ms,
                                extracted,
                                context.call_id,
                            )
                    
                    logger.info(
                        f"In-call HTTP tool completed: {self.config.name}",
                        extra={
                            "status": response.status,
                            "call_id": context.call_id,
                            "output_keys": list(result.get("data", {}).keys()),
                        }
                    )
                    
                    return result
        
        except asyncio.CancelledError:
            if native_deposit and native_guard is not None:
                native_guard.note_tool_result(
                    context.call_id, success=False,
                    native_outcome="unknown" if request_dispatched else "not_deposited",
                    dispatch_generation=context.native_deposit_attempt,
                )
            raise
        except aiohttp.ClientError as e:
            if native_deposit:
                return self._note_deposit_result(self._deposit_failure("deposit_transport_failed"), context)
            logger.warning(f"In-call HTTP tool request failed: {self.config.name} error={e}")
            return self._failure_result("error")
        except Exception as e:
            if native_deposit:
                return self._note_deposit_result(
                    self._deposit_failure(
                        "deposit_request_failed",
                        outcome="unknown" if request_dispatched or readonly_ticket is not None else "not_deposited",
                    ), context,
                )
            logger.error(f"In-call HTTP tool unexpected error: {self.config.name} error={e}", exc_info=True)
            return self._failure_result("error")
    
    async def _build_substitution_context(
        self,
        ai_params: Dict[str, Any],
        context: ToolExecutionContext
    ) -> Dict[str, str]:
        """
        Build combined substitution context from call context, pre-call results, and AI parameters.
        
        Context variables (auto-injected):
        - caller_number, called_number, caller_name
        - context_name, call_id
        
        Pre-call variables (from pre-call HTTP lookups):
        - Any variables fetched by pre-call tools (e.g., customer_name, account_id)
        
        AI parameters (provided by AI during function call):
        - Whatever parameters are defined in the tool config
        """
        sub = {
            "caller_number": context.caller_number or "",
            "called_number": context.called_number or "",
            "caller_name": context.caller_name or "",
            "context_name": context.context_name or "",
            "call_id": context.call_id or "",
        }
        
        # Add pre-call tool results (fetched before call started)
        # These are stored in session.pre_call_results by pre-call HTTP lookup tools
        try:
            if context.session_store:
                session = await context.session_store.get_by_call_id(context.call_id)
                if session:
                    pre_call_results = getattr(session, 'pre_call_results', None) or {}
                    for key, value in pre_call_results.items():
                        # Don't override built-in context variables
                        if key not in sub:
                            sub[key] = str(value) if value is not None else ""
                    if pre_call_results:
                        logger.debug(
                            f"Added pre-call variables to in-call tool context: {list(pre_call_results.keys())}",
                            extra={"tool": self.config.name, "call_id": context.call_id}
                        )
        except Exception as e:
            logger.warning(f"Failed to load pre-call results for in-call tool: {e}")
        
        # Add AI-provided parameters (these override pre-call vars if same name)
        for key, value in ai_params.items():
            if value is not None:
                sub[key] = str(value)
            else:
                sub[key] = ""
        
        return sub
    
    def _substitute_variables(
        self,
        template: str,
        context: Dict[str, str],
        *,
        json_escape: bool = False,
    ) -> str:
        """
        Substitute variables in template string.
        
        Supports:
        - {variable} - Context or AI parameter
        - ${ENV_VAR} - Environment variable
        """
        result = template
        
        # Context/parameter variables: {var_name}
        def replacement(value: Any) -> str:
            rendered = str(value)
            if json_escape:
                # The template owns the surrounding JSON quotes.
                return json.dumps(rendered, ensure_ascii=False)[1:-1]
            return rendered

        for key, value in context.items():
            result = result.replace(f"{{{key}}}", replacement(value))
        
        # Environment variables: ${VAR_NAME}
        env_pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
        def env_replacer(match):
            var_name = match.group(1)
            return replacement(os.environ.get(var_name, ""))
        
        result = re.sub(env_pattern, env_replacer, result)
        
        return result
    
    def _extract_output_variables(self, data: Any) -> Dict[str, Any]:
        """
        Extract output variables from JSON response.

        List/dict results are JSON-serialized; scalars preserved as-is.
        """
        results = {}

        for var_name, path in self.config.output_variables.items():
            try:
                value = self._extract_path(data, path)
                if value is None:
                    results[var_name] = ""
                elif isinstance(value, (list, dict)):
                    results[var_name] = json.dumps(value)
                else:
                    results[var_name] = value
            except Exception as e:
                logger.debug(f"Failed to extract variable {var_name}: {e}")
                results[var_name] = ""

        return results

    def _extract_path(self, data: Any, path: str) -> Any:
        """Extract value from nested data using dot notation path.

        Delegates to the shared ``extract_path`` utility which supports
        simple keys, numeric indices, and ``[*]`` wildcards.
        """
        return extract_path(data, path)
    
    def _build_result_message(self, data: Dict[str, Any]) -> str:
        """
        Build a human-readable message from extracted data.
        """
        if not data:
            return "No data retrieved."
        
        # Simple key-value format
        parts = []
        for key, value in data.items():
            if value is not None and value != "":
                readable_key = key.replace('_', ' ').title()
                parts.append(f"{readable_key}: {value}")
        
        if parts:
            return "Retrieved: " + ", ".join(parts)
        return "Data retrieved successfully."
    
    def _redact_url(self, url: str) -> str:
        """Redact sensitive parts of URL for logging."""
        redacted = re.sub(
            r'(api_key|apikey|key|token|auth|password)=([^&]+)',
            r'\1=***',
            url,
            flags=re.IGNORECASE
        )
        return redacted


def create_in_call_http_tool(name: str, config_dict: Dict[str, Any]) -> InCallHTTPTool:
    """
    Factory function to create an in-call HTTP tool from YAML config.
    
    Args:
        name: Tool name from YAML key
        config_dict: Tool configuration dictionary
    
    Returns:
        Configured InCallHTTPTool instance
    """
    config = InCallHTTPConfig(
        name=name,
        description=config_dict.get('description', ''),
        enabled=config_dict.get('enabled', True),
        is_global=config_dict.get('is_global', False),
        timeout_ms=config_dict.get('timeout_ms', 5000),
        hold_audio_file=config_dict.get('hold_audio_file'),
        hold_audio_threshold_ms=config_dict.get('hold_audio_threshold_ms', 500),
        url=config_dict.get('url', ''),
        method=config_dict.get('method', 'POST'),
        headers=config_dict.get('headers', {}),
        query_params=config_dict.get('query_params', {}),
        body_template=config_dict.get('body_template'),
        parameters=config_dict.get('parameters', []),
        output_variables=config_dict.get('output_variables', {}),
        return_raw_json=config_dict.get('return_raw_json', False),
        direct_response_json_path=config_dict.get("direct_response_json_path"),
        direct_failure_message=config_dict.get("direct_failure_message"),
        caller_wait_ambience=bool(config_dict.get("caller_wait_ambience", False)),
        max_response_size_bytes=config_dict.get('max_response_size_bytes', 65536),
        error_message=config_dict.get('error_message', "I'm sorry, I couldn't retrieve that information right now."),
    )
    
    return InCallHTTPTool(config)
