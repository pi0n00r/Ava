import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import GoogleLiveProvider


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_emits_provider_disconnected_event():
    events = []

    async def on_event(e):
        events.append(e)

    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=on_event)
    provider._call_id = "call-1"

    await provider._emit_provider_disconnected(code=1011, reason="Internal error occurred.")

    assert events == [
        {
            "type": "ProviderDisconnected",
            "call_id": "call-1",
            "provider": "google_live",
            "code": 1011,
            "reason": "Internal error occurred.",
        }
    ]
