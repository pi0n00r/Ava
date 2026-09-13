import asyncio
import hashlib
import json
from copy import deepcopy

import pytest

from src.config import AppConfig, OpenAIProviderConfig
from src.pipelines.base import LLMResponse
from src.pipelines.openai import (
    OpenAILLMAdapter,
    _make_http_headers,
    _make_ws_headers,
    _session_user_for_call,
)

RAW_ID = "rita-int-a-" + "a" * 64
OTHER_ID = "rita-int-a-" + "b" * 64
ENABLED = {"session_user_from_call_id": True, "call_id_header_enabled": True}
MESSAGES = [{"role": "system", "content": "fixture prompt"}, {"role": "user", "content": "hello"}]


class _Lines:
    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        await asyncio.sleep(0)
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n'
        yield b"data: [DONE]\n"


class _Response:
    status = 200

    def __init__(self):
        self.content = _Lines()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return json.dumps({"choices": [{"message": {"content": "hello"}}]})


class _Session:
    def __init__(self):
        self.closed = False
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": deepcopy(json), "headers": dict(headers), "timeout": timeout})
        return _Response()

    async def close(self):
        self.closed = True


def adapter_fixture(defaults=None):
    app = AppConfig(
        default_provider="openai",
        providers={"openai": {"api_key": "fixture-key", "chat_base_url": "https://relay.fixture.invalid/v1", "chat_model": "gpt-4o-mini"}},
        asterisk={"host": "pbx.fixture.invalid", "username": "fixture", "password": "fixture"},
        llm={"initial_greeting": "hello", "prompt": "fixture prompt", "model": "fixture"},
        audio_transport="externalmedia",
        downstream_mode="stream",
    )
    session = _Session()
    adapter = OpenAILLMAdapter(
        "fixture-llm", app, OpenAIProviderConfig(**app.providers["openai"]),
        defaults or {}, session_factory=lambda: session,
    )
    return adapter, session


async def request(adapter, path, call_id=RAW_ID, options=None):
    # The actual method receives transport identity separately from model messages.
    context = {"messages": deepcopy(MESSAGES), "call_id": "untrusted-context-id"}
    if path == "generate":
        result = await adapter.generate(call_id, "hello", context, options or {})
        return result.text if isinstance(result, LLMResponse) else result
    return "".join([chunk async for chunk in adapter.generate_stream(call_id, "hello", context, options or {})])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
async def test_both_restored_http_paths_bind_raw_header_to_hashed_user_only(path):
    adapter, session = adapter_fixture()
    options = dict(ENABLED, extra_body={"user": "untrusted-extra-user", "custom": "kept"})
    assert await request(adapter, path, options=options) == "hello"
    assert len(session.requests) == 1
    sent = session.requests[0]
    expected = "asterisk-call-" + hashlib.sha256(RAW_ID.encode("utf-8")).hexdigest()[:32]
    assert sent["headers"]["X-Ava-Call-Id"] == RAW_ID
    assert sent["headers"]["Authorization"] == "Bearer fixture-key"
    assert sent["json"]["user"] == expected == _session_user_for_call(RAW_ID)
    assert sent["json"]["messages"] == MESSAGES
    assert RAW_ID not in json.dumps(sent["json"])
    assert "untrusted-context-id" not in json.dumps(sent["json"])
    assert sent["json"]["custom"] == "kept"
    assert options["extra_body"]["user"] == "untrusted-extra-user"
    assert bool(sent["json"].get("stream")) == (path == "generate_stream")
    assert "tools" not in sent["json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
async def test_restored_default_mini_request_is_unchanged_and_no_identity_opt_in(path):
    adapter, session = adapter_fixture()
    await request(adapter, path, options={"extra_body": {"user": "retained-user"}})
    sent = session.requests[0]
    assert sent["json"]["user"] == "retained-user"
    assert "X-Ava-Call-Id" not in sent["headers"]
    assert sent["json"]["model"] == "gpt-4o-mini"
    assert sent["json"]["messages"] == MESSAGES


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "false", [], {"enabled": True}])
async def test_identity_options_require_literal_true_not_truthiness(path, value):
    adapter, session = adapter_fixture()
    await request(adapter, path, options={"call_id_header_enabled": value, "session_user_from_call_id": value})
    sent = session.requests[0]
    assert "user" not in sent["json"]
    assert "X-Ava-Call-Id" not in sent["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
async def test_hash_only_opt_in_does_not_enable_truthy_raw_header(path):
    adapter, session = adapter_fixture()
    await request(adapter, path, options={"session_user_from_call_id": True, "call_id_header_enabled": "true"})
    assert session.requests[0]["json"]["user"] == _session_user_for_call(RAW_ID)
    assert "X-Ava-Call-Id" not in session.requests[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
async def test_raw_header_requires_literal_hashed_user_opt_in_before_http(path, value):
    adapter, session = adapter_fixture()
    with pytest.raises(RuntimeError, match="requires call-scoped user identity"):
        await request(adapter, path, options={"call_id_header_enabled": True, "session_user_from_call_id": value})
    assert not session.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
@pytest.mark.parametrize("call_id", ["", "short", "x" * 129, "valid-id\r\nInjected: value", "valid-id space", "valid/id", "valid?query", "nonascii-\u00e9", None, 12345678])
async def test_invalid_raw_identity_is_rejected_without_http_or_value_in_error(path, call_id):
    adapter, session = adapter_fixture()
    with pytest.raises(RuntimeError, match="invalid transport call identity") as error:
        await request(adapter, path, call_id, ENABLED)
    assert str(error.value) == "invalid transport call identity"
    assert not session.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
@pytest.mark.parametrize("call_id", ["12345678", "x" * 128, "valid_.:-123"])
async def test_raw_identity_ascii_bounds_and_alphabet(path, call_id):
    adapter, session = adapter_fixture()
    await request(adapter, path, call_id, ENABLED)
    assert session.requests[0]["headers"]["X-Ava-Call-Id"] == call_id
    assert session.requests[0]["json"]["user"] == _session_user_for_call(call_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
async def test_raw_header_rejects_realtime_and_missing_authentication_before_transport(path):
    for options, message in [(dict(ENABLED, use_realtime=True), "requires HTTP chat completions"), (dict(ENABLED, api_key=""), "requires an API key")]:
        adapter, session = adapter_fixture()
        with pytest.raises(RuntimeError, match=message):
            await request(adapter, path, options=options)
        assert not session.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
async def test_runtime_false_can_disable_pipeline_identity_defaults(path):
    adapter, session = adapter_fixture(ENABLED)
    await request(adapter, path, options={"session_user_from_call_id": False, "call_id_header_enabled": False})
    assert "user" not in session.requests[0]["json"]
    assert "X-Ava-Call-Id" not in session.requests[0]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["generate", "generate_stream"])
async def test_main_opt_in_does_not_leak_to_mini_or_another_simultaneous_call(path):
    adapter, session = adapter_fixture()
    await asyncio.gather(
        request(adapter, path, RAW_ID, ENABLED),
        request(adapter, path, OTHER_ID, ENABLED),
        request(adapter, path, "mini-fixture-call", {}),
    )
    assert len(session.requests) == 3
    bound = [entry for entry in session.requests if "X-Ava-Call-Id" in entry["headers"]]
    assert {entry["headers"]["X-Ava-Call-Id"] for entry in bound} == {RAW_ID, OTHER_ID}
    for entry in bound:
        assert entry["json"]["user"] == _session_user_for_call(entry["headers"]["X-Ava-Call-Id"])
    mini = next(entry for entry in session.requests if "X-Ava-Call-Id" not in entry["headers"])
    assert "user" not in mini["json"]
    assert mini["json"]["messages"] == MESSAGES
    assert adapter._pipeline_defaults == {}


def test_generic_speech_and_websocket_header_helpers_do_not_gain_raw_identity():
    options = dict(ENABLED, api_key="fixture-key")
    assert "X-Ava-Call-Id" not in _make_http_headers(options)
    assert "X-Ava-Call-Id" not in dict(_make_ws_headers(options))
