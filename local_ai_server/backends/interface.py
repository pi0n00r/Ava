from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BackendInterface(ABC):
    @classmethod
    @abstractmethod
    def name(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def config_schema(cls) -> Dict[str, Any]:
        pass

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        pass

    @abstractmethod
    def initialize(self, config: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        pass


class STTBackendInterface(BackendInterface):
    @abstractmethod
    def process_audio(self, audio_bytes: bytes) -> Optional[str]:
        pass


class TTSBackendInterface(BackendInterface):
    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        pass


class LLMBackendInterface(BackendInterface):
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        pass

    @abstractmethod
    def generate_stream(self, prompt: str, **kwargs):
        pass
