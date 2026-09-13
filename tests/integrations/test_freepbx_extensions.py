import base64
import json
from copy import deepcopy

import pytest

from src.integrations.freepbx_extensions import (
    FREEPBX_QUERY,
    FreePBXInventoryError,
    hydrate_freepbx_extension_inventory,
)
from src.tools.telephony.live_agent_transfer import LiveAgentTransferTool


def _config():
    return {
        "tools": {
            "extensions": {
                "inventory": {
                    "provider": "freepbx_graphql",
                    "base_url": "https://pbx.example.test",
                    "client_id_env": "FREEPBX_API_CLIENT_ID",
                    "client_secret_env": "FREEPBX_API_CLIENT_SECRET",
                    "scope": "gql:core",
                    "dialplan_context": "from-internal",
                },
                "internal": {
                    "0": {"name": "Operator", "transfer": True, "dialplan_context": "from-internal"},
                },
            },
            "transfer": {"enabled": True, "defer_until_playback_complete": False},
        }
    }


def _response(rows):
    return {
        "data": {
            "fetchAllExtensions": {
                "status": True,
                "message": "ok",
                "totalCount": len(rows),
                "extension": rows,
            }
        }
    }


def _transport(rows, calls, *, token="short-lived-token"):
    def request(url, *, body, headers, timeout):
        calls.append((url, body, headers, timeout))
        if url.endswith("/token"):
            return {"access_token": token, "token_type": "Bearer", "expires_in": 3600}
        return _response(rows)
    return request


def _env(secret="client-secret"):
    return {"FREEPBX_API_CLIENT_ID": "client-id", "FREEPBX_API_CLIENT_SECRET": secret}


def test_m2m_token_and_minimal_fetchall_query_merge_supplemental_routes():
    calls = []
    config = _config()
    hydrate_freepbx_extension_inventory(
        config,
        environ=_env(),
        request_json=_transport(
            [
                {"extensionId": "2", "user": {"name": "  Sat   Bajaj  "}},
                {"extensionId": "1", "user": {"name": "Gary Bajaj"}},
                {"extensionId": "6", "user": {"name": "JD"}},
                {"extensionId": "7", "user": {"name": "AImèe"}},
            ],
            calls,
        ),
    )

    token_url, token_body, token_headers, timeout = calls[0]
    assert token_url == "https://pbx.example.test/admin/api/api/token"
    assert token_body == b"grant_type=client_credentials&scope=gql%3Acore"
    assert token_headers["Authorization"] == "Basic " + base64.b64encode(
        b"client-id:client-secret"
    ).decode("ascii")
    assert timeout == 5

    gql_url, gql_body, gql_headers, _ = calls[1]
    assert gql_url == "https://pbx.example.test/admin/api/api/gql"
    assert gql_headers["Authorization"] == "Bearer short-lived-token"
    assert json.loads(gql_body)["query"] == FREEPBX_QUERY
    assert "password" not in gql_body.decode("utf-8").lower()

    internal = config["tools"]["extensions"]["internal"]
    assert list(internal) == ["0", "1", "2", "6", "7"]
    assert internal["2"]["name"] == "Sat Bajaj"
    assert internal["1"]["dialplan_context"] == "from-internal"
    assert "dial_string" not in internal["1"]
    assert internal["6"]["name"] == "JD"
    assert internal["7"]["name"] == "AImèe"


def test_refresh_removes_deleted_core_entry_and_updates_name():
    config = _config()
    hydrate_freepbx_extension_inventory(
        config,
        environ=_env(),
        request_json=_transport(
            [
                {"extensionId": "1", "user": {"name": "Gary"}},
                {"extensionId": "2", "user": {"name": "Sat"}},
            ],
            [],
        ),
    )
    hydrate_freepbx_extension_inventory(
        config,
        environ=_env(),
        request_json=_transport(
            [{"extensionId": "1", "user": {"name": "Gary Bajaj"}}],
            [],
        ),
    )
    internal = config["tools"]["extensions"]["internal"]
    assert list(internal) == ["0", "1"]
    assert internal["1"]["name"] == "Gary Bajaj"


def test_duplicate_core_id_and_core_supplemental_conflict_fail_closed():
    config = _config()
    duplicate = [
        {"extensionId": "1", "user": {"name": "Gary"}},
        {"extensionId": "1", "user": {"name": "Other"}},
    ]
    with pytest.raises(FreePBXInventoryError, match="duplicate extensionId 1"):
        hydrate_freepbx_extension_inventory(
            config, environ=_env(), request_json=_transport(duplicate, [])
        )

    conflict = _config()
    conflict["tools"]["extensions"]["internal"]["6"] = {
        "name": "Stale supplemental JD",
        "transfer": True,
    }
    with pytest.raises(FreePBXInventoryError, match="supplemental extension.*6"):
        hydrate_freepbx_extension_inventory(
            conflict,
            environ=_env(),
            request_json=_transport(
                [{"extensionId": "6", "user": {"name": "Core Six"}}], []
            ),
        )


def test_duplicate_names_are_ambiguous_but_numeric_ids_remain_deterministic():
    config = _config()
    hydrate_freepbx_extension_inventory(
        config,
        environ=_env(),
        request_json=_transport(
            [
                {"extensionId": "1", "user": {"name": "Same Name"}},
                {"extensionId": "2", "user": {"name": "Same Name"}},
            ],
            [],
        ),
    )
    internal = config["tools"]["extensions"]["internal"]
    extension, _, source = LiveAgentTransferTool._resolve_explicit_target_extension(
        target="Same Name", extensions_cfg=internal
    )
    assert extension is None
    assert source == "extensions.internal.target_name_ambiguous"
    extension, _, source = LiveAgentTransferTool._resolve_explicit_target_extension(
        target="2", extensions_cfg=internal
    )
    assert extension == "2"
    assert source == "parameter.target.extension"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"data": {"fetchAllExtensions": {"status": False, "extension": [], "totalCount": 0}}},
        {"data": {"fetchAllExtensions": {"status": True, "extension": {}, "totalCount": 0}}},
        _response([{"extensionId": "not-an-extension", "user": {"name": "Bad"}}]),
        _response([{"extensionId": "1", "user": "bad"}]),
        _response([{"extensionId": "1", "user": {"name": "bad\u0000name"}}]),
    ],
)
def test_malformed_response_fails_without_partial_mutation(payload):
    config = _config()
    before = deepcopy(config)

    def request(url, **kwargs):
        if url.endswith("/token"):
            return {"access_token": "token"}
        return payload

    with pytest.raises(FreePBXInventoryError):
        hydrate_freepbx_extension_inventory(config, environ=_env(), request_json=request)
    assert config == before


def test_api_failure_and_missing_credentials_are_secret_free():
    secret = "do-not-log-this-secret"

    def exploding_request(*args, **kwargs):
        raise RuntimeError(f"transport included {secret}")

    with pytest.raises(FreePBXInventoryError) as captured:
        hydrate_freepbx_extension_inventory(
            _config(), environ=_env(secret), request_json=exploding_request
        )
    assert secret not in str(captured.value)

    with pytest.raises(FreePBXInventoryError, match="credentials are missing") as missing:
        hydrate_freepbx_extension_inventory(_config(), environ={}, request_json=exploding_request)
    assert "FREEPBX_API_CLIENT_SECRET" not in str(missing.value)
