from __future__ import annotations

from typing import Any, Dict, Optional

from backends.interface import STTBackendInterface


class KrokoBackend(STTBackendInterface):
    def __init__(self):
        self._client = None

    @classmethod
    def name(cls) -> str:
        return "kroko"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "url": {"type": "string", "required": False, "description": "Kroko WebSocket URL"},
            "language": {"type": "string", "required": False, "default": "en-US"},
            "embedded": {"type": "boolean", "required": False, "default": False},
            "port": {"type": "integer", "required": False, "default": 6006},
        }

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._client = None

    def process_audio(self, audio_bytes: bytes) -> Optional[str]:
        return None

    def status(self) -> Dict[str, Any]:
        return {"backend": "kroko", "connected": self._client is not None}
