import sys
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api import config  # noqa: E402


class _ProviderResponse:
    status_code = 200

    @staticmethod
    def json():
        return {"data": [{"id": "test-model"}]}


def _provider_test_client(monkeypatch, *, failure=False):
    calls = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            calls.append(url)
            if failure:
                raise OSError("offline test connection failure")
            return _ProviderResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")
    return TestClient(app), calls


def test_openai_provider_endpoint_uses_configured_base_exactly(monkeypatch):
    client, calls = _provider_test_client(monkeypatch)
    configured = "  http://validation-api.future.example:9443/tenant/v9///  "

    response = client.post(
        "/api/config/providers/test",
        json={
            "name": "future-compatible",
            "config": {
                "type": "openai",
                "api_key": "test-only",
                "chat_base_url": configured,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert calls == ["http://validation-api.future.example:9443/tenant/v9/models"]


def test_openai_provider_endpoint_connection_failure_never_calls_default(monkeypatch):
    client, calls = _provider_test_client(monkeypatch, failure=True)

    response = client.post(
        "/api/config/providers/test",
        json={
            "name": "future-compatible",
            "config": {
                "type": "openai",
                "api_key": "test-only",
                "chat_base_url": "http://unavailable.future.example:9555/api/v2",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert calls == ["http://unavailable.future.example:9555/api/v2/models"]
    assert all("api.openai.com" not in url for url in calls)


@pytest.mark.parametrize("provider_config", [{"type": "openai"}, {"type": "openai", "chat_base_url": "   "}])
def test_openai_provider_endpoint_blank_or_omitted_base_uses_stock_default(
    monkeypatch, provider_config
):
    client, calls = _provider_test_client(monkeypatch)
    provider_config["api_key"] = "test-only"

    response = client.post(
        "/api/config/providers/test",
        json={"name": "stock-openai", "config": provider_config},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert calls == ["https://api.openai.com/v1/models"]


def test_get_config_returns_merged_structured_config(monkeypatch):
    monkeypatch.setattr(
        config,
        "_read_merged_config_dict",
        lambda: {"providers": {"local": {"type": "local"}}},
    )

    app = FastAPI()
    app.include_router(config.router, prefix="/api/config")

    response = TestClient(app).get("/api/config")

    assert response.status_code == 200
    assert response.json() == {"providers": {"local": {"type": "local"}}}


def test_health_api_token_impacts_local_ai_when_used_as_live_status_fallback():
    assert config._local_ai_env_key("HEALTH_API_TOKEN") is True


@pytest.mark.parametrize(
    ("mutate", "location"),
    [
        (
            lambda parsed: parsed["providers"]["openai_realtime"].update(
                output_resampler="not-a-mode"
            ),
            "providers.openai_realtime.output_resampler",
        ),
        (
            lambda parsed: parsed["pipelines"]["local_hybrid"]
            .setdefault("options", {})
            .setdefault("tts", {})
            .update(output_resampler="not-a-mode"),
            "pipelines.local_hybrid.options.tts.output_resampler",
        ),
        (
            lambda parsed: parsed["pipelines"]["local_hybrid"]
            .setdefault("options", {})
            .setdefault("tts", {})
            .update(streaming_overlap="false"),
            "pipelines.local_hybrid.options.tts.streaming_overlap",
        ),
        (
            lambda parsed: parsed["pipelines"]["local_hybrid"]
            .setdefault("options", {})
            .setdefault("stt", {})
            .update(segment_silence_ms=50),
            "pipelines.local_hybrid.options.stt.segment_silence_ms",
        ),
    ],
)
def test_yaml_validation_rejects_invalid_audio_transport_policy(mutate, location):
    parsed = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    mutate(parsed)

    with pytest.raises(HTTPException) as exc_info:
        config._validate_ai_agent_config(yaml.safe_dump(parsed, sort_keys=False))

    assert exc_info.value.status_code == 400
    assert location in str(exc_info.value.detail)


def test_profile_usage_guard_blocks_mutating_agent_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("ava-demo", "Ava Demo", "telephony_ulaw_8k"),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        }
    }
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin"}},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 409
    assert "Ava Demo" in str(exc_info.value.detail)


def test_profile_usage_guard_allows_new_or_unreferenced_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("ava-demo", "Ava Demo", "telephony_ulaw_8k"),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {"profiles": {"default": "telephony_ulaw_8k"}}
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "experimental": {"transport_out": {"encoding": "slin16"}},
        }
    }

    config._assert_in_use_audio_profiles_unchanged(old, new)


def test_profile_usage_guard_blocks_default_inherited_agent(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.executemany(
            "INSERT INTO agents VALUES (?, ?, ?)",
            [
                ("default-agent", "Default Agent", None),
                ("blank-agent", "Blank Agent", "  "),
            ],
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        }
    }
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin"}},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 409
    assert "Default Agent" in str(exc_info.value.detail)
    assert "Blank Agent" in str(exc_info.value.detail)


def test_profile_usage_guard_blocks_default_only_change_for_inherited_agent(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("default-agent", "Default Agent", None),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    profiles = {
        "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        "telephony_enhanced_8k": {"transport_out": {"encoding": "ulaw"}},
    }
    old = {"profiles": {"default": "telephony_ulaw_8k", **profiles}}
    new = {"profiles": {"default": "telephony_enhanced_8k", **profiles}}

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 409
    assert "profiles.default" in str(exc_info.value.detail)
    assert "Default Agent" in str(exc_info.value.detail)


def test_profile_usage_guard_allows_default_change_without_inherited_agents(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "agents.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE agents (slug TEXT, display_name TEXT, audio_profile TEXT)"
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?)",
            ("explicit-agent", "Explicit Agent", "telephony_ulaw_8k"),
        )
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    profiles = {
        "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        "telephony_enhanced_8k": {"transport_out": {"encoding": "ulaw"}},
    }
    old = {"profiles": {"default": "telephony_ulaw_8k", **profiles}}
    new = {"profiles": {"default": "telephony_enhanced_8k", **profiles}}

    config._assert_in_use_audio_profiles_unchanged(old, new)


def test_profile_usage_guard_fails_closed_when_agent_store_is_invalid(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "agents.db"
    db_path.write_text("not a sqlite database", encoding="utf-8")
    monkeypatch.setenv("AGENTS_DB_PATH", str(db_path))
    old = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "ulaw"}},
        }
    }
    new = {
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {"transport_out": {"encoding": "slin"}},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        config._assert_in_use_audio_profiles_unchanged(old, new)

    assert exc_info.value.status_code == 503
