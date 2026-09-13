import asyncio
import json
from unittest.mock import MagicMock

import pytest

from src.config import OpenAIProviderConfig
from src.pipelines.openai import (
    OpenAILLMAdapter,
    _could_be_compatibility_tool_prefix,
    _parse_compatibility_tool_text,
)
from src.tools.base import ToolPhase
from tests.test_pipeline_openai_adapters import _FakeSession, _build_app_config


HANGUP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hangup_call",
        "description": "End the call",
        "parameters": {
            "type": "object",
            "properties": {"farewell_message": {"type": "string"}},
            "required": [],
        },
    },
}

DEPOSIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "pbx_message_deposit",
        "description": "Deposit one confirmed message",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["target", "message"],
        },
    },
}


def test_qwen_complete_json_tool_literal_is_normalized_only_when_enabled():
    text = 'hangup_call{"farewell_message":"You are very welcome."}'
    assert _parse_compatibility_tool_text(text, [HANGUP_SCHEMA]) == [
        {
            "id": "",
            "name": "hangup_call",
            "parameters": {"farewell_message": "You are very welcome."},
            "type": "function",
        }
    ]
    assert _could_be_compatibility_tool_prefix("hangup_call{", [HANGUP_SCHEMA])
    assert _parse_compatibility_tool_text(text, [DEPOSIT_SCHEMA]) == []


@pytest.mark.parametrize(
    "text",
    [
        'I might say hangup_call{"farewell_message":"Goodbye."}',
        'unknown_tool{"farewell_message":"Goodbye."}',
        'hangup_call{"farewell_message":"Goodbye."',
        'hangup_call{"unexpected":"Goodbye."}',
        'hangup_call["Goodbye."]',
        'hangup_call{} trailing words',
    ],
)
def test_textual_tool_compatibility_fails_closed(text):
    assert _parse_compatibility_tool_text(text, [HANGUP_SCHEMA]) == []


def test_existing_python_keyword_literal_remains_supported():
    assert _parse_compatibility_tool_text(
        "call(target='Gary', message='The sky is blue.')", [DEPOSIT_SCHEMA]
    )[0]["parameters"] == {"target": "Gary", "message": "The sky is blue."}


@pytest.mark.asyncio
async def test_serial_adapter_normalizes_complete_qwen_hangup_literal():
    app_config = _build_app_config()
    provider = OpenAIProviderConfig(**app_config.providers["openai"])
    body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": 'hangup_call{"farewell_message":"You are very welcome."}'
                    }
                }
            ]
        }
    ).encode("utf-8")
    fake_session = _FakeSession(body)
    adapter = OpenAILLMAdapter(
        "openai_llm",
        app_config,
        provider,
        {"tools": ["hangup_call"]},
        session_factory=lambda: fake_session,
    )
    definition = MagicMock()
    definition.phase = ToolPhase.IN_CALL
    definition.to_openai_schema.return_value = HANGUP_SCHEMA
    registry = MagicMock()
    registry.get.return_value = MagicMock(definition=definition)
    adapter.bind_tool_registry(registry)

    response = await adapter.generate("call-serial", "goodbye", {}, {})

    assert response.text == ""
    assert response.tool_calls == [
        {
            "id": "",
            "name": "hangup_call",
            "parameters": {"farewell_message": "You are very welcome."},
            "type": "function",
        }
    ]


@pytest.mark.asyncio
async def test_http_force_close_survives_misreported_keepalive_for_eight_requests():
    connections = 0

    async def handler(reader, writer):
        nonlocal connections
        connections += 1
        headers = await reader.readuntil(b"\r\n\r\n")
        content_length = 0
        for line in headers.decode("latin-1").split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
        if content_length:
            await reader.readexactly(content_length)
        body = (
            b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Keep-Alive: timeout=5, max=100\r\n\r\n"
            + f"{len(body):X}\r\n".encode("ascii")
            + body
            + b"\r\n0\r\n\r\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    app_config = _build_app_config()
    provider = OpenAIProviderConfig(**app_config.providers["openai"])
    adapter = OpenAILLMAdapter(
        "openai_llm",
        app_config,
        provider,
        {
            "base_url": f"http://127.0.0.1:{port}/v1",
            "http_force_close": True,
            "model": "gpt-4o-mini",
        },
    )
    try:
        for index in range(8):
            chunks = [
                chunk
                async for chunk in adapter.generate_stream(
                    f"call-{index}", "hello", {}, {}
                )
            ]
            assert chunks == ["OK"]
        assert connections == 8
        assert adapter._session.connector.force_close is True
    finally:
        await adapter.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_force_close_defaults_off_and_reuses_session():
    app_config = _build_app_config()
    provider = OpenAIProviderConfig(**app_config.providers["openai"])
    adapter = OpenAILLMAdapter("openai_llm", app_config, provider, {})
    assert adapter._compose_options({})["http_force_close"] is False
    try:
        await adapter._ensure_session(False)
        first = adapter._session
        assert first.connector.force_close is False
        await adapter._ensure_session(False)
        assert adapter._session is first
    finally:
        await adapter.stop()
