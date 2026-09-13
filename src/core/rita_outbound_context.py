# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Project=Ava
"""Private, non-actuating Rita context projection for the native main entrance."""
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


def rita_context_url(existing_url):
    parts = urlsplit(existing_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        raise ValueError("rita_context_unconfigured")
    if "/v1/" not in parts.path:
        raise ValueError("rita_context_unconfigured")
    prefix = parts.path.rsplit("/v1/", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, prefix + "/v1/internal-calls/context", "", ""))


def unknown_rita_context(error_class):
    return {
        "status": "unknown", "source": "rita", "direction": "outbound",
        "purpose": None, "pin_verified": None,
        "ext6_auth_pass_observed": False, "ext6_auth_observation_available": False,
        "ext6_auth_observation_error": None,
        "human_acknowledgement": "unproven", "error_class": error_class,
    }


def project_rita_context(value, *, private_values=()):
    required = {
        "ok", "source", "direction", "purpose", "pin_verified",
        "ext6_auth_pass_observed", "ext6_auth_observation_available",
        "ext6_auth_observation_error", "human_acknowledgement",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("rita_context_malformed")
    if value["ok"] is not True or value["source"] != "rita" or value["direction"] != "outbound":
        raise ValueError("rita_context_malformed")
    purpose = value["purpose"]
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose.encode("utf-8")) > 700:
        raise ValueError("rita_context_malformed")
    if any(secret and secret in purpose for secret in private_values):
        raise ValueError("rita_context_private_value")
    available = value["ext6_auth_observation_available"]
    observed = value["ext6_auth_pass_observed"]
    pin = value["pin_verified"]
    if type(available) is not bool or type(observed) is not bool:
        raise ValueError("rita_context_malformed")
    if pin is not None and pin is not True:
        raise ValueError("rita_context_malformed")
    if pin is True and not (available and observed):
        raise ValueError("rita_context_malformed")
    if value["human_acknowledgement"] != "unproven":
        raise ValueError("rita_context_malformed")
    display_name = value.get("target_display_label")
    if display_name is not None:
        if (not isinstance(display_name, str) or not display_name.strip()
                or len(display_name.encode("utf-8")) > 96
                or any(unicodedata.category(char) == "Cc" for char in display_name)):
            raise ValueError("rita_context_malformed")
        if any(secret and secret in display_name for secret in private_values):
            raise ValueError("rita_context_private_value")
        display_name = " ".join(display_name.split())
    label_error = value.get("target_display_label_error")
    if label_error is not None and (not isinstance(label_error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", label_error)):
        label_error = "freepbx_target_label_unavailable"
    error = value["ext6_auth_observation_error"]
    if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error)):
        error = "unclassified_native_observation_error"
    return {
        "status": "ready", "source": "rita", "direction": "outbound",
        "purpose": purpose.strip(), "pin_verified": pin,
        "ext6_auth_pass_observed": observed, "ext6_auth_observation_available": available,
        "ext6_auth_observation_error": error,
        "human_acknowledgement": "unproven", "error_class": None,
        "target_display_label": display_name,
        "target_display_label_error": label_error,
    }


def rita_outbound_greeting(context):
    label = context.get("target_display_label")
    greeting = f"Hi {label}, it's AIm\u00e8e." if label else "Hi, it's AIm\u00e8e."
    if context.get("status") == "ready":
        greeting += " I'm calling because " + " ".join(context["purpose"].split())
    return greeting
