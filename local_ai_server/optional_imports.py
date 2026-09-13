from __future__ import annotations

# Optional backend imports. These may be excluded in minimal images built with
# INCLUDE_VOSK/INCLUDE_LLAMA/INCLUDE_PIPER false. Guard imports so the server
# can still start for other modes/backends.
#
# NOTE: Some CUDA stacks are sensitive to import order across native extensions.
# In particular, `llama_cpp` (llama-cpp-python) may load an older `libcudart.so.12`
# from the base CUDA runtime image, which can cause later `torch` imports (e.g.
# Kokoro TTS) to fail with missing CUDA symbols.
#
# Preloading torch (when present) before importing llama_cpp avoids this class
# of failure and enables switching to Kokoro TTS at runtime.
try:  # pragma: no cover
    import torch  # noqa: F401
except Exception:  # noqa: BLE001 - best-effort preload
    pass

try:
    from vosk import Model as VoskModel, KaldiRecognizer  # type: ignore
except ImportError:  # pragma: no cover
    VoskModel = None  # type: ignore[assignment]
    KaldiRecognizer = None  # type: ignore[assignment]

try:
    from faster_whisper import WhisperModel as FasterWhisperModel  # type: ignore
except ImportError:  # pragma: no cover
    FasterWhisperModel = None  # type: ignore[assignment]

try:
    from llama_cpp import Llama  # type: ignore
except ImportError:  # pragma: no cover
    Llama = None  # type: ignore[assignment]

try:
    from piper import PiperVoice  # type: ignore
except ImportError:  # pragma: no cover
    PiperVoice = None  # type: ignore[assignment]

try:
    from melo.api import TTS as MeloTTS  # type: ignore
except ImportError:  # pragma: no cover
    MeloTTS = None  # type: ignore[assignment]
