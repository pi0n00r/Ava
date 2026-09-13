from __future__ import annotations

import os
from typing import Any, Dict, Optional

import structlog

from src.tools.business.resend_client import send_email as send_resend_email
from src.tools.business.smtp_client import send_email as send_smtp_email

logger = structlog.get_logger(__name__)


def _provider_from_config(tool_config: Dict[str, Any]) -> str:
    return str(tool_config.get("provider") or "resend").strip().lower()


def _smtp_configured() -> bool:
    return bool(str(os.getenv("SMTP_HOST") or "").strip())


def _resend_configured() -> bool:
    return bool(str(os.getenv("RESEND_API_KEY") or "").strip())


def resolve_context_value(
    *,
    tool_config: Dict[str, Any],
    key: str,
    context_name: Optional[str],
    default: Any = None,
) -> Any:
    by_context_key = f"{key}_by_context"
    mapping = tool_config.get(by_context_key)
    if context_name and isinstance(mapping, dict):
        v = mapping.get(context_name)
        if v is not None:
            return v
    return tool_config.get(key, default)


async def send_email(
    *,
    email_data: Dict[str, Any],
    tool_config: Dict[str, Any],
    call_id: str,
    log_label: str,
    recipient: str,
) -> Optional[Dict[str, Any]]:
    provider = _provider_from_config(tool_config)

    if provider == "smtp":
        return await send_smtp_email(
            email_data=email_data,
            call_id=call_id,
            log_label=log_label,
            recipient=recipient,
        )

    if provider == "resend":
        return await send_resend_email(
            email_data=email_data,
            call_id=call_id,
            log_label=log_label,
            recipient=recipient,
        )

    if provider == "auto":
        if _smtp_configured():
            return await send_smtp_email(
                email_data=email_data,
                call_id=call_id,
                log_label=log_label,
                recipient=recipient,
            )
        if _resend_configured():
            return await send_resend_email(
                email_data=email_data,
                call_id=call_id,
                log_label=log_label,
                recipient=recipient,
            )
        logger.error("No email provider configured (SMTP_HOST or RESEND_API_KEY required)", call_id=call_id)
        return None

    logger.warning("Unknown email provider; falling back to Resend", call_id=call_id, provider=provider)
    return await send_resend_email(
        email_data=email_data,
        call_id=call_id,
        log_label=log_label,
        recipient=recipient,
    )

