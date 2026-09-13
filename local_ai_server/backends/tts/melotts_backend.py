from __future__ import annotations

from typing import Any, Dict

from backends.interface import TTSBackendInterface


class MeloTTSBackend(TTSBackendInterface):
    def __init__(self):
        self._tts = None

    @classmethod
    def name(cls) -> str:
        return "melotts"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "voice": {"type": "string", "required": False, "default": "EN-US"},
            "device": {"type": "string", "required": False, "default": "cpu", "enum": ["cpu", "cuda"]},
            "speed": {"type": "number", "required": False, "default": 1.0},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            from melo.api import TTS
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._tts = None

    def synthesize(self, text: str) -> bytes:
        return b""

    def status(self) -> Dict[str, Any]:
        return {"backend": "melotts", "loaded": self._tts is not None}
