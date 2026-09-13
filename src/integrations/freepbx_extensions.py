"""Hydrate AVA's stock live-agent inventory from FreePBX GraphQL.

The generated entries intentionally contain no SIP/PJSIP dial strings. Stock
``live_agent_transfer`` hands the selected extension to FreePBX through the
configured dialplan context, keeping the PBX authoritative for execution.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FREEPBX_PROVIDER = "freepbx_graphql"
FREEPBX_QUERY = """query {
  fetchAllExtensions {
    status
    message
    totalCount
    extension {
      extensionId
      user { name }
    }
  }
}"""
_SOURCE_MARKER = "_inventory_source"
_EXTENSION_ID = re.compile(r"^[0-9]+$")


class FreePBXInventoryError(RuntimeError):
    """A bounded, secret-free inventory refresh failure."""


def _request_json(
    url: str,
    *,
    body: bytes,
    headers: Mapping[str, str],
    timeout: float,
) -> dict[str, Any]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - operator URL
            payload = response.read(1024 * 1024 + 1)
    except Exception as exc:
        raise FreePBXInventoryError("FreePBX inventory request failed") from exc
    if len(payload) > 1024 * 1024:
        raise FreePBXInventoryError("FreePBX inventory response exceeded 1 MiB")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreePBXInventoryError("FreePBX inventory response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise FreePBXInventoryError("FreePBX inventory response was not an object")
    return parsed


def _clean_name(value: Any, extension_id: str) -> str:
    raw = str(value or "")
    if any(ord(character) < 32 and character not in "\t\r\n" for character in raw):
        raise FreePBXInventoryError(
            f"FreePBX extension {extension_id} contains control characters"
        )
    cleaned = " ".join(raw.split())
    return cleaned or f"Extension {extension_id}"


def _inventory_config(config_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    tools = config_data.get("tools")
    if not isinstance(tools, dict):
        return {}, {}
    extensions = tools.get("extensions")
    if not isinstance(extensions, dict):
        return {}, {}
    source = extensions.get("inventory")
    if not isinstance(source, dict) or source.get("enabled") is False:
        return {}, {}
    provider = str(source.get("provider") or "").strip().lower()
    if provider != FREEPBX_PROVIDER:
        raise FreePBXInventoryError(f"Unsupported extension inventory provider: {provider or 'empty'}")
    return extensions, source


def fetch_freepbx_extension_inventory(
    source: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    request_json: Callable[..., dict[str, Any]] = _request_json,
) -> dict[str, dict[str, Any]]:
    env = os.environ if environ is None else environ
    base_url = str(source.get("base_url") or "").strip().rstrip("/")
    if not base_url or "://" not in base_url:
        raise FreePBXInventoryError("FreePBX inventory base_url is required")

    client_id_env = str(source.get("client_id_env") or "FREEPBX_API_CLIENT_ID").strip()
    client_secret_env = str(source.get("client_secret_env") or "FREEPBX_API_CLIENT_SECRET").strip()
    client_id = str(env.get(client_id_env) or "")
    client_secret = str(env.get(client_secret_env) or "")
    if not client_id or not client_secret:
        raise FreePBXInventoryError("FreePBX inventory OAuth credentials are missing")

    try:
        timeout = float(source.get("timeout_seconds", 5))
    except (TypeError, ValueError) as exc:
        raise FreePBXInventoryError("FreePBX inventory timeout_seconds is invalid") from exc
    if timeout <= 0:
        raise FreePBXInventoryError("FreePBX inventory timeout_seconds must be positive")

    token_url = str(source.get("token_url") or f"{base_url}/admin/api/api/token").strip()
    graphql_url = str(source.get("graphql_url") or f"{base_url}/admin/api/api/gql").strip()
    scope = str(source.get("scope") or "gql:core").strip()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    try:
        token_response = request_json(
            token_url,
            body=urlencode({"grant_type": "client_credentials", "scope": scope}).encode("ascii"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=timeout,
        )
    except FreePBXInventoryError:
        raise
    except Exception as exc:
        raise FreePBXInventoryError("FreePBX inventory request failed") from exc
    access_token = token_response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise FreePBXInventoryError("FreePBX inventory OAuth response omitted access_token")

    try:
        response = request_json(
            graphql_url,
            body=json.dumps({"query": FREEPBX_QUERY}, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except FreePBXInventoryError:
        raise
    except Exception as exc:
        raise FreePBXInventoryError("FreePBX inventory request failed") from exc
    if response.get("errors"):
        raise FreePBXInventoryError("FreePBX inventory GraphQL returned errors")
    data = response.get("data")
    fetched = data.get("fetchAllExtensions") if isinstance(data, dict) else None
    if not isinstance(fetched, dict) or fetched.get("status") is not True:
        raise FreePBXInventoryError("FreePBX fetchAllExtensions did not succeed")
    rows = fetched.get("extension")
    if not isinstance(rows, list):
        raise FreePBXInventoryError("FreePBX fetchAllExtensions omitted extension rows")
    total_count = fetched.get("totalCount")
    if not isinstance(total_count, int) or total_count != len(rows):
        raise FreePBXInventoryError("FreePBX fetchAllExtensions totalCount mismatch")

    context = str(source.get("dialplan_context") or "from-internal").strip()
    if not context:
        raise FreePBXInventoryError("FreePBX inventory dialplan_context is required")
    inventory: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FreePBXInventoryError("FreePBX extension row was not an object")
        extension_id = str(row.get("extensionId") or "").strip()
        if not _EXTENSION_ID.fullmatch(extension_id):
            raise FreePBXInventoryError("FreePBX returned an invalid extensionId")
        if extension_id in inventory:
            raise FreePBXInventoryError(f"FreePBX returned duplicate extensionId {extension_id}")
        user = row.get("user")
        if user is not None and not isinstance(user, dict):
            raise FreePBXInventoryError(f"FreePBX extension {extension_id} user was malformed")
        name = _clean_name((user or {}).get("name"), extension_id)
        inventory[extension_id] = {
            "name": name,
            "description": f"FreePBX Core extension {extension_id}",
            "transfer": True,
            "dialplan_context": context,
            _SOURCE_MARKER: FREEPBX_PROVIDER,
        }
    return inventory


def _supplemental_inventory(extensions: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = extensions.get("internal") or {}
    if not isinstance(raw, dict):
        raise FreePBXInventoryError("tools.extensions.internal must be an object")
    supplementals: dict[str, dict[str, Any]] = {}
    for raw_key, raw_entry in raw.items():
        key = str(raw_key or "").strip()
        if isinstance(raw_entry, dict) and raw_entry.get(_SOURCE_MARKER) == FREEPBX_PROVIDER:
            continue
        if not _EXTENSION_ID.fullmatch(key) or not isinstance(raw_entry, dict):
            raise FreePBXInventoryError("Supplemental FreePBX extension inventory is malformed")
        supplementals[key] = dict(raw_entry)
    return supplementals


def hydrate_freepbx_extension_inventory(
    config_data: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    request_json: Callable[..., dict[str, Any]] = _request_json,
) -> None:
    """Refresh and atomically merge Core entries with FreePBX-owned supplements."""
    extensions, source = _inventory_config(config_data)
    if not source:
        return
    supplementals = _supplemental_inventory(extensions)
    fetched = fetch_freepbx_extension_inventory(
        source, environ=environ, request_json=request_json
    )
    conflicts = sorted(set(supplementals).intersection(fetched), key=lambda value: (int(value), value))
    if conflicts:
        raise FreePBXInventoryError(
            "FreePBX Core inventory conflicts with supplemental extension(s): "
            + ", ".join(conflicts)
        )
    merged = {**fetched, **supplementals}
    extensions["internal"] = {
        key: merged[key] for key in sorted(merged, key=lambda value: (int(value), value))
    }
