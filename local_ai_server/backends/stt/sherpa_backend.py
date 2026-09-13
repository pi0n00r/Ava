from __future__ import annotations

from typing import Any, Dict, Optional

from backends.interface import STTBackendInterface


class SherpaBackend(STTBackendInterface):
    def __init__(self):
        self._model = None

    @classmethod
    def name(cls) -> str:
        return "sherpa"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model_path": {"type": "string", "required": True, "description": "Path to Sherpa ONNX model"},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            import sherpa_onnx
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
        return {"backend": "sherpa", "loaded": self._model is not None}
