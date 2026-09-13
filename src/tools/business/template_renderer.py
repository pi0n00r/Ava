from __future__ import annotations

from typing import Any, Dict, Optional

import structlog
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

logger = structlog.get_logger(__name__)

_ENV = SandboxedEnvironment(autoescape=True)


def _normalize_template(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s or None


# Keys whose values are already HTML-escaped by the calling tool and
# contain intentional markup (e.g. <br/> for line breaks). These are wrapped
# in Markup() so Jinja's autoescape does not double-escape them.
#
# Keep this list narrow: plain-text variables (for example `transcript`) must
# continue to be autoescaped by Jinja.
_PRE_ESCAPED_KEYS = frozenset({"transcript_html"})


def render_html_template(
    *,
    html_template: str,
    variables: Dict[str, Any],
) -> str:
    if not isinstance(html_template, str):
        raise TypeError("html_template must be a string")
    if len(html_template) > 200_000:
        raise ValueError("html_template too large")
    safe_vars = {}
    for k, v in (variables or {}).items():
        if k in _PRE_ESCAPED_KEYS and isinstance(v, str):
            safe_vars[k] = Markup(v)
        else:
            safe_vars[k] = v
    template = _ENV.from_string(html_template)
    return template.render(**safe_vars)


def render_html_template_with_fallback(
    *,
    template_override: Any,
    default_template: str,
    variables: Dict[str, Any],
    call_id: str,
    tool_name: str,
) -> str:
    override = _normalize_template(template_override)
    if override:
        try:
            return render_html_template(html_template=override, variables=variables)
        except Exception as e:
            logger.warning(
                "Email template override render failed; falling back to default",
                call_id=call_id,
                tool=tool_name,
                error=str(e),
            )
    return render_html_template(html_template=default_template, variables=variables)
