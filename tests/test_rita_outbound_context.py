import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.models import CallSession
from src.core.rita_outbound_context import project_rita_context, rita_context_url, rita_outbound_greeting
from src.engine import Engine
from src.tools.http.in_call_lookup import InCallHTTPTool


def _response(**overrides):
    return dict({
        "ok": True, "source": "rita", "direction": "outbound", "purpose": "your requested appointment check.",
        "target_extension": "1", "target_display_label": "Gary",
        "target_display_label_error": None, "pin_verified": None,
        "ext6_auth_pass_observed": False, "ext6_auth_observation_available": False,
        "ext6_auth_observation_error": "ami_auth_observation_unavailable",
        "human_acknowledgement": "unproven",
    }, **overrides)


def test_private_projection_uses_native_display_name_not_destination_or_caller_id():
    value = project_rita_context(dict(_response(), call_id="private-call", request_ref="private-ref", token="private-token"))
    assert value["pin_verified"] is None
    assert value["human_acknowledgement"] == "unproven"
    assert rita_outbound_greeting(value).startswith("Hi Gary,")
    assert not any(secret in json.dumps(value) for secret in ("private-call", "private-ref", "private-token"))
    assert "target_extension" not in value
    assert rita_outbound_greeting(project_rita_context(_response(target_display_label=None))).startswith("Hi,")
    assert rita_outbound_greeting(project_rita_context(_response(target_display_label="Avery", target_extension="1"))).startswith("Hi Avery,")


@pytest.mark.parametrize("label", ["", "x" * 97, "\u00e9" * 49, "Gary\n", "Gary\x00", "Gary\x7f", 1])
def test_native_display_label_is_bounded_not_a_directory(label):
    with pytest.raises(ValueError, match="rita_context_malformed"):
        project_rita_context(_response(target_display_label=label))


def test_unknown_native_label_preserves_authoritative_purpose_and_unknown_pin():
    value = project_rita_context(_response(target_display_label=None,
        target_display_label_error="freepbx_target_label_unavailable"))
    assert rita_outbound_greeting(value) == "Hi, it's AIm\u00e8e. I'm calling because your requested appointment check."
    assert value["pin_verified"] is None
    assert value["target_display_label_error"] == "freepbx_target_label_unavailable"
    value = project_rita_context(_response(target_display_label=None, target_display_label_error="private-path-and-secret"))
    assert value["target_display_label_error"] == "freepbx_target_label_unavailable"


@pytest.mark.parametrize("changes", [
    {"pin_verified": True}, {"pin_verified": False}, {"ext6_auth_pass_observed": 1},
    {"human_acknowledgement": "acknowledged"}, {"source": "caller-id"},
    {"purpose": "x" * 701}, {"purpose": "\u00e9" * 351}, {"purpose": ""},
])
def test_unqualified_or_malformed_context_never_claims_authentication(changes):
    with pytest.raises(ValueError, match="rita_context_malformed"):
        project_rita_context(_response(**changes))


def test_pin_true_requires_native_observed_continuation_and_utf8_limit():
    value = project_rita_context(_response(pin_verified=True, ext6_auth_pass_observed=True,
                                          ext6_auth_observation_available=True, purpose="\u00e9" * 350))
    assert value["pin_verified"] is True
    with pytest.raises(ValueError, match="rita_context_private_value"):
        project_rita_context(_response(purpose="The reference is private-ref"), private_values=("private-ref",))
    value = project_rita_context(_response(ext6_auth_observation_error="Bearer do-not-expose"))
    assert value["ext6_auth_observation_error"] == "unclassified_native_observation_error"


def test_context_url_uses_existing_hostname_and_router_prefix():
    assert rita_context_url("https://vip.bajaj.com/aimee-router/v1/message-deposit") == "https://vip.bajaj.com/aimee-router/v1/internal-calls/context"
    assert rita_context_url("https://vip.bajaj.com/aimee-router/v1/handoff") == "https://vip.bajaj.com/aimee-router/v1/internal-calls/context"


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", ["success", "unavailable", "malformed", "malformed-reference"])
async def test_hydration_reuses_private_auth_without_model_visible_ids(monkeypatch, readback):
    engine = Engine.__new__(Engine)
    ref = "a" * 64
    engine.ari_client = SimpleNamespace(send_command=AsyncMock(return_value={"value": ref if readback != "malformed-reference" else "BAD"}))
    engine.session_store = SimpleNamespace(upsert_call=AsyncMock())
    config = SimpleNamespace(url="https://vip.bajaj.com/aimee-router/v1/message-deposit",
                             headers={"Authorization": "Bearer private-fixture-token"})
    engine._tool_registry_for_session = Mock(return_value=SimpleNamespace(get=lambda name: SimpleNamespace(config=config) if name == "pbx_message_deposit" else None))
    engine._tool_config_for_session = Mock(return_value={})
    observed = []

    async def existing_native_execute(probe, parameters, context):
        observed.append(probe)
        assert parameters == {}
        assert context.call_id == "native-ARI-fixture-001"
        assert probe.config.url == "https://vip.bajaj.com/aimee-router/v1/internal-calls/context"
        assert probe.config.timeout_ms == 5000
        assert probe.config.headers == config.headers
        assert json.loads(probe.config.body_template) == {"call_id": "{call_id}", "request_ref": ref}
        if readback == "unavailable":
            return {"status": "failed"}
        return {"status": "success", "data": _response() if readback == "success" else {"ok": True}}

    monkeypatch.setattr(InCallHTTPTool, "execute", existing_native_execute)
    session = CallSession(call_id="native-ARI-fixture-001", caller_channel_id="native-ARI-fixture-001", is_outbound=True)
    session.provider_overrides = {"baseline": "keep"}
    session.outbound_custom_vars = {"baseline": "keep"}
    options = {"call_id_header_enabled": True, "session_user_from_call_id": True}
    await engine._hydrate_rita_outbound_session(session, options)
    assert session.is_outbound is True
    assert session.rita_outbound_context["pin_verified"] is None
    assert session.rita_outbound_context["human_acknowledgement"] == "unproven"
    assert session.provider_overrides["baseline"] == "keep"
    projected = json.dumps([session.rita_outbound_context, session.outbound_custom_vars, session.provider_overrides])
    assert ref not in projected and session.call_id not in projected and "private-fixture-token" not in projected
    if readback == "success":
        assert session.provider_overrides["greeting"].startswith("Hi Gary,")
        assert session.outbound_custom_vars["purpose"] == _response()["purpose"]
    else:
        assert session.rita_outbound_context["status"] == "unknown"
        assert session.provider_overrides["greeting"] == "Hi, it's AIm\u00e8e."
    await engine._hydrate_rita_outbound_session(session, options)
    assert len(observed) == (0 if readback == "malformed-reference" else 1)


@pytest.mark.asyncio
async def test_hydration_leaves_inbound_and_legacy_sessions_untouched():
    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(send_command=AsyncMock())
    session = CallSession(call_id="legacy-call", caller_channel_id="legacy-call")
    await engine._hydrate_rita_outbound_session(session, {"call_id_header_enabled": True, "session_user_from_call_id": True})
    session.is_outbound = True
    await engine._hydrate_rita_outbound_session(session, {})
    engine.ari_client.send_command.assert_not_awaited()
    assert session.provider_overrides == session.rita_outbound_context == {}
