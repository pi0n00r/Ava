from __future__ import annotations

from typing import Any, Dict, Optional

from backends.interface import STTBackendInterface


class VoskBackend(STTBackendInterface):
    def __init__(self):
        self._model = None

    @classmethod
    def name(cls) -> str:
        return "vosk"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model_path": {"type": "string", "required": True, "description": "Path to Vosk model directory"},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            import vosk
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._model = None

    def process_audio(self, audio_bytes: bytes) -> Optional[str]:
        return None

    def status(self) -> Dict[str, Any]:
        return {"backend": "vosk", "loaded": self._model is not None}
