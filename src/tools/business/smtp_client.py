from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Dict, Optional, Sequence, Union

import structlog

logger = structlog.get_logger(__name__)


class _SMTPRateLimiter:
    def __init__(self, max_per_second: float = 5.0) -> None:
        if max_per_second <= 0:
            max_per_second = 1.0
        self._min_interval = 1.0 / max_per_second
        self._lock = asyncio.Lock()
        self._last_sent_at: float = 0.0

    async def wait_turn(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_sent_at
            sleep_for = self._min_interval - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._last_sent_at = time.monotonic()


_limiter = _SMTPRateLimiter(max_per_second=float(os.getenv("SMTP_MAX_RPS", "5") or "5"))
_dedupe_lock = asyncio.Lock()
_recent_send_keys: Dict[str, float] = {}


def _smtp_configured() -> bool:
    return bool(str(os.getenv("SMTP_HOST") or "").strip())


def _as_addr_list(value: Union[str, Sequence[str], None]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    return [str(v).strip() for v in value if str(v).strip()]


def _dedupe_ttl_seconds() -> float:
    raw = os.getenv("SMTP_DEDUPE_TTL_SECONDS") or os.getenv("RESEND_DEDUPE_TTL_SECONDS") or "900"
    try:
        ttl = float(raw)
    except Exception:
        ttl = 900.0
    return ttl if ttl > 0 else 0.0


async def _dedupe_should_send(email_data: Dict[str, Any], *, call_id: str, log_label: str, recipient: str) -> bool:
    ttl = _dedupe_ttl_seconds()
    if ttl <= 0:
        return True
    subject = str(email_data.get("subject") or "")
    to_addr = str(email_data.get("to") or recipient or "")
    key = f"{log_label}|{call_id}|{to_addr}|{subject}"
    now = time.monotonic()
    async with _dedupe_lock:
        ts = _recent_send_keys.get(key)
        if ts is not None and (now - float(ts)) < ttl:
            return False
        cutoff = now - ttl
        for k, v in list(_recent_send_keys.items()):
            if (now - float(v)) > ttl:
                _recent_send_keys.pop(k, None)
        _recent_send_keys[key] = now
    return True


def _build_message(email_data: Dict[str, Any]) -> EmailMessage:
    msg = EmailMessage()
    msg_id = make_msgid()
    msg["Message-ID"] = msg_id

    from_value = str(email_data.get("from") or "").strip()
    to_values = _as_addr_list(email_data.get("to"))
    subject = str(email_data.get("subject") or "").strip()

    if not from_value:
        raise ValueError("email_data.from is required for SMTP")
    if not to_values:
        raise ValueError("email_data.to is required for SMTP")
    if not subject:
        subject = "(no subject)"

    msg["From"] = from_value
    msg["To"] = ", ".join(to_values)
    msg["Subject"] = subject

    cc_values = _as_addr_list(email_data.get("cc"))
    bcc_values = _as_addr_list(email_data.get("bcc"))
    reply_to = str(email_data.get("reply_to") or email_data.get("reply-to") or "").strip()

    if cc_values:
        msg["Cc"] = ", ".join(cc_values)
    if reply_to:
        msg["Reply-To"] = reply_to

    html_body = str(email_data.get("html") or "").strip()
    text_body = str(email_data.get("text") or "").strip()

    if text_body:
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
    elif html_body:
        msg.set_content("This email requires an HTML-capable email client.")
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content("")

    msg._aava_all_recipients = to_values + cc_values + bcc_values  # type: ignore[attr-defined]
    return msg


def _smtp_send_sync(msg: EmailMessage) -> None:
    host = str(os.getenv("SMTP_HOST") or "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST not configured")

    tls_mode = (os.getenv("SMTP_TLS_MODE") or "starttls").strip().lower()
    username = str(os.getenv("SMTP_USERNAME") or "").strip() or None
    password = str(os.getenv("SMTP_PASSWORD") or "").strip() or None
    if tls_mode == "none" and username and password:
        allow_insecure = str(os.getenv("SMTP_ALLOW_INSECURE_AUTH", "false") or "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not allow_insecure:
            raise RuntimeError("Refusing SMTP auth without TLS (set SMTP_TLS_MODE=starttls/smtps)")

    port_raw = str(os.getenv("SMTP_PORT") or "").strip()
    if port_raw:
        port = int(port_raw)
    else:
        port = 465 if tls_mode == "smtps" else 587

    timeout_s = float(os.getenv("SMTP_TIMEOUT_SECONDS", "10") or "10")
    tls_verify = str(os.getenv("SMTP_TLS_VERIFY", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}

    context = ssl.create_default_context()
    if not tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    recipients = getattr(msg, "_aava_all_recipients", None)  # type: ignore[attr-defined]
    if not recipients:
        recipients = _as_addr_list(msg.get("To")) + _as_addr_list(msg.get("Cc")) + _as_addr_list(msg.get("Bcc"))

    if tls_mode == "smtps":
        with smtplib.SMTP_SSL(host=host, port=port, timeout=timeout_s, context=context) as smtp:
            smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg, to_addrs=recipients)
        return

    with smtplib.SMTP(host=host, port=port, timeout=timeout_s) as smtp:
        smtp.ehlo()
        if tls_mode == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(msg, to_addrs=recipients)


async def send_email(
    *,
    email_data: Dict[str, Any],
    call_id: str,
    log_label: str,
    recipient: str,
    max_retries: int = 1,
) -> Optional[Dict[str, Any]]:
    if not _smtp_configured():
        logger.error(
            "SMTP not configured (SMTP_HOST missing). Set SMTP_HOST in .env and force-recreate ai_engine to apply env_file changes.",
            call_id=call_id,
        )
        return None

    if not await _dedupe_should_send(email_data, call_id=call_id, log_label=log_label, recipient=recipient):
        logger.info(
            f"{log_label} duplicate suppressed",
            call_id=call_id,
            recipient=recipient,
            subject=str(email_data.get("subject") or ""),
        )
        return {"skipped": True}

    msg = _build_message(email_data)

    for attempt in range(max_retries + 1):
        await _limiter.wait_turn()
        try:
            await asyncio.to_thread(_smtp_send_sync, msg)
            msg_id = msg.get("Message-ID")
            logger.info(
                f"{log_label} sent successfully (SMTP)",
                call_id=call_id,
                recipient=recipient,
                message_id=msg_id,
            )
            return {"message_id": msg_id}
        except Exception as exc:  # noqa: BLE001
            if attempt < max_retries:
                backoff = 0.5 * (2**attempt)
                logger.warning(
                    f"{log_label} SMTP send failed; retrying",
                    call_id=call_id,
                    recipient=recipient,
                    error=str(exc),
                    retry_in_seconds=backoff,
                )
                await asyncio.sleep(backoff)
                continue

            logger.error(
                f"Failed to send {log_label.lower()} (SMTP)",
                call_id=call_id,
                recipient=recipient,
                error=str(exc),
                exc_info=True,
            )
            return None
