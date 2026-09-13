import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.http.in_call_lookup import (
    InCallHTTPConfig,
    InCallHTTPTool,
    create_in_call_http_tool,
)


class _Content:
    def __init__(self, payload):
        self.payload = payload

    async def iter_chunked(self, _size):
        yield json.dumps(self.payload).encode("utf-8")


def _context():
    return SimpleNamespace(
        call_id="call-secret-id",
        caller_number="+15551234567",
        called_number="+15557654321",
        caller_name="Caller",
        context_name="support",
        caller_channel_id="PJSIP/secret-channel",
        session_store=None,
    )


async def _execute(config, payload, status=200):
    response = AsyncMock()
    response.status = status
    response.headers = {}
    response.charset = "utf-8"
    response.content = _Content(payload)
    request_cm = AsyncMock()
    request_cm.__aenter__ = AsyncMock(return_value=response)
    request_cm.__aexit__ = AsyncMock(return_value=None)
    session = AsyncMock()
    session.request = MagicMock(return_value=request_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    with patch("aiohttp.ClientSession", return_value=session_cm):
        return await InCallHTTPTool(config).execute({}, _context())


@pytest.mark.asyncio
async def test_opt_in_selects_only_vetted_spoken_response():
    result = await _execute(
        InCallHTTPConfig(
            name="deposit",
            url="https://rita.example/deposit",
            return_raw_json=True,
            direct_response_json_path="spoken_response",
        ),
        {
            "ok": True,
            "spoken_response": "  Thank you.  I have your message for Gary. ",
            "call_id": "must-not-be-spoken",
            "token": "secret-token",
        },
    )
    assert result["status"] == "success"
    assert result["_direct_response_text"] == "Thank you. I have your message for Gary."
    assert "must-not-be-spoken" not in result["_direct_response_text"]
    assert "secret-token" not in result["_direct_response_text"]


@pytest.mark.asyncio
async def test_default_tool_result_is_unchanged():
    result = await _execute(
        InCallHTTPConfig(name="lookup", url="https://example.test", return_raw_json=True),
        {"spoken_response": "Not opted in", "value": 7},
    )
    assert result["status"] == "success"
    assert "_direct_response_text" not in result
    assert result["data"]["value"] == 7


@pytest.mark.asyncio
async def test_missing_vetted_phrase_fails_explicitly():
    result = await _execute(
        InCallHTTPConfig(
            name="deposit",
            url="https://rita.example/deposit",
            direct_response_json_path="spoken_response",
            direct_failure_message="I am sorry, I could not complete that message.",
        ),
        {"ok": True, "call_id": "must-not-be-spoken"},
    )
    assert result == {
        "status": "failed",
        "message": "I'm sorry, I couldn't retrieve that information right now.",
        "_direct_response_text": "I am sorry, I could not complete that message.",
    }


@pytest.mark.asyncio
async def test_http_failure_uses_explicit_direct_failure_message():
    result = await _execute(
        InCallHTTPConfig(
            name="deposit",
            url="https://rita.example/deposit",
            direct_response_json_path="spoken_response",
            direct_failure_message="I am sorry, I could not complete that message.",
        ),
        {"error": "secret backend detail"},
        status=503,
    )
    assert result["status"] == "failed"
    assert result["_direct_response_text"] == "I am sorry, I could not complete that message."
    assert "secret backend detail" not in result["_direct_response_text"]


def test_factory_preserves_opt_in_direct_response_fields():
    tool = create_in_call_http_tool(
        "deposit",
        {
            "url": "https://rita.example/deposit",
            "direct_response_json_path": "spoken_response",
            "direct_failure_message": "I am sorry, I could not complete that message.",
        },
    )
    assert tool.config.direct_response_json_path == "spoken_response"
    assert tool.config.direct_failure_message == (
        "I am sorry, I could not complete that message."
    )
