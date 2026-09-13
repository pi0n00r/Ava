from backends.interface import (
    BackendInterface,
    STTBackendInterface,
    TTSBackendInterface,
    LLMBackendInterface,
)
from backends.registry import STT_REGISTRY, TTS_REGISTRY, LLM_REGISTRY


def load_builtin_backends():
    from backends import stt, tts, llm
