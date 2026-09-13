from __future__ import annotations

from typing import Any, Dict

from backends.interface import TTSBackendInterface


class KokoroBackend(TTSBackendInterface):
    def __init__(self):
        self._pipeline = None

    @classmethod
    def name(cls) -> str:
        return "kokoro"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "voice": {"type": "string", "required": False, "default": "af_heart"},
            "lang": {"type": "string", "required": False, "default": "a"},
            "mode": {"type": "string", "required": False, "default": "local", "enum": ["local", "hf", "api"]},
            "model_path": {"type": "string", "required": False},
            "api_base_url": {"type": "string", "required": False},
            "api_key": {"type": "string", "required": False},
            "api_model": {"type": "string", "required": False},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            import kokoro
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._pipeline = None

    def synthesize(self, text: str) -> bytes:
        return b""

    def status(self) -> Dict[str, Any]:
        return {"backend": "kokoro", "loaded": self._pipeline is not None}
