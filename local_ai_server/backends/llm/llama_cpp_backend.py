from __future__ import annotations

from typing import Any, Dict

from backends.interface import LLMBackendInterface


class LlamaCppBackend(LLMBackendInterface):
    def __init__(self):
        self._llm = None

    @classmethod
    def name(cls) -> str:
        return "llama_cpp"

    @classmethod
    def config_schema(cls) -> Dict[str, Any]:
        return {
            "model_path": {"type": "string", "required": True, "description": "Path to GGUF model"},
            "threads": {"type": "integer", "required": False, "default": 4},
            "context": {"type": "integer", "required": False, "default": 768},
            "batch": {"type": "integer", "required": False, "default": 128},
            "max_tokens": {"type": "integer", "required": False, "default": 64},
            "temperature": {"type": "number", "required": False, "default": 0.4},
            "top_p": {"type": "number", "required": False, "default": 0.85},
            "repeat_penalty": {"type": "number", "required": False, "default": 1.05},
            "gpu_layers": {"type": "integer", "required": False, "default": 0},
            "use_mlock": {"type": "boolean", "required": False, "default": False},
        }

    @classmethod
    def is_available(cls) -> bool:
        try:
            from llama_cpp import Llama
            return True
        except ImportError:
            return False

    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    def shutdown(self) -> None:
        self._llm = None

    def generate(self, prompt: str, **kwargs) -> str:
        return ""

    def generate_stream(self, prompt: str, **kwargs):
        yield ""

    def status(self) -> Dict[str, Any]:
        return {"backend": "llama_cpp", "loaded": self._llm is not None}
