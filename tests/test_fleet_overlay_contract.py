import json
import sqlite3
from pathlib import Path

from src.config import load_config
from src.core.legacy_agent_migration import ensure_legacy_contexts_imported
from src.tools.runtime_config import resolve_agent_tool_config


def _mock_freepbx(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return self.payload

    def fake_urlopen(request, timeout):
        assert timeout == 5
        if request.full_url.endswith("/admin/api/api/token"):
            return Response({"access_token": "test-token"})
        assert request.full_url.endswith("/admin/api/api/gql")
        return Response(
            {
                "data": {
                    "fetchAllExtensions": {
                        "status": True,
                        "message": "ok",
                        "totalCount": 4,
                        "extension": [
                            {"extensionId": "1", "user": {"name": "HH Gary Bajaj"}},
                            {"extensionId": "2", "user": {"name": "Sat Bajaj"}},
                            {"extensionId": "6", "user": {"name": "JD"}},
                            {"extensionId": "7", "user": {"name": "AImèe"}},
                        ],
                    }
                }
            }
        )

    monkeypatch.setattr(
        "src.integrations.freepbx_extensions.urlopen",
        fake_urlopen,
    )


def test_fleet_overlay_contract(monkeypatch):
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("LOCAL_WS_AUTH_TOKEN", "test")
    monkeypatch.setenv("FREEPBX_GRAPHQL_BASE_URL", "https://vip.bajaj.com")
    monkeypatch.setenv("FREEPBX_API_CLIENT_ID", "test-client")
    monkeypatch.setenv("FREEPBX_API_CLIENT_SECRET", "test-secret")
    _mock_freepbx(monkeypatch)

    root = Path(__file__).resolve().parents[1]
    config = load_config(str(root / "config" / "ai-agent.yaml"))

    assert config.audiosocket.host == ""
    assert config.audiosocket.advertise_host == "ava.bajaj.com"
    extra_body = config.pipelines["local_hybrid"].options["llm"]["extra_body"]
    assert extra_body == {"reasoning_effort": "none", "keep_alive": -1}
    assert "chat_template_kwargs" not in extra_body

    destinations = config.tools["transfer"]["destinations"]
    assert destinations == {}
    extensions = config.tools["extensions"]["internal"]
    assert set(extensions) == {"0", "1", "2", "6", "7"}
    assert extensions["1"]["name"] == "HH Gary Bajaj"
    assert extensions["2"]["name"] == "Sat Bajaj"
    assert extensions["0"]["name"] == "Operator"
    assert extensions["6"]["name"] == "JD"
    assert extensions["7"]["name"] == "AImèe"
    assert all("dial_string" not in entry for entry in extensions.values())

    assert config.contexts["jd"]["tools"] == ["hangup_call"]
    assert config.contexts["aimee"]["tools"] == ["live_agent_transfer", "hangup_call"]
    assert config.contexts["aimee"]["tool_configs"]["transfer"] == {
        "destination_policy": "inherit",
    }


def test_aimee_policy_survives_agent_migration_and_runtime_normalization(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("LOCAL_WS_AUTH_TOKEN", "test")
    monkeypatch.setenv("FREEPBX_GRAPHQL_BASE_URL", "https://vip.bajaj.com")
    monkeypatch.setenv("FREEPBX_API_CLIENT_ID", "test-client")
    monkeypatch.setenv("FREEPBX_API_CLIENT_SECRET", "test-secret")
    _mock_freepbx(monkeypatch)
    root = Path(__file__).resolve().parents[1]
    config = load_config(str(root / "config" / "ai-agent.yaml"))
    contexts = config.contexts
    database = tmp_path / "agents.db"
    ensure_legacy_contexts_imported(contexts, db_path=str(database))

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "select tools_json, tool_configs_json from agents where slug='aimee'"
        ).fetchone()
    assert json.loads(stored[0]) == ["live_agent_transfer", "hangup_call"]
    assert json.loads(stored[1]) == {
        "transfer": {"destination_policy": "inherit", "destination_keys": []}
    }

    effective = resolve_agent_tool_config(config.model_dump(), None)
    assert effective.config["tools"]["transfer"]["destinations"] == {}
    assert set(effective.config["tools"]["extensions"]["internal"]) == {
        "0",
        "1",
        "2",
        "6",
        "7",
    }
