from __future__ import annotations

from typing import Any, Dict

from backends.interface import TTSBackendInterface


class PiperBackend(TTSBackendInterface):
    def __init__(self):
        self._voice = None

    @classmethod
    def name(cls) -> str:
        return "piper"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model_path": {"type": "string", "required": True, "description": "Path to Piper ONNX model"},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            from piper import PiperVoice
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._voice = None

    def synthesize(self, text: str) -> bytes:
        return b""

    def status(self) -> Dict[str, Any]:
        return {"backend": "piper", "loaded": self._voice is not None}
