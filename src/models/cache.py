"""
Thread-safe model cache to enable hot-loading and reuse of AI models
across concurrent calls. Prevents repeated expensive loads and keeps
models warm for low-latency use.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Callable, Dict


class ModelCache:
    """Process-wide cache for heavy AI models.

    Usage:
        model = model_cache.get_model("vosk-small-en", lambda: vosk.Model(path))
    """

    _instance: "ModelCache" | None = None
    _instance_lock: Lock = Lock()

    def __new__(cls) -> "ModelCache":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._models = {}
                cls._instance._lock = Lock()
        return cls._instance

    def get_model(self, model_name: str, loader_func: Callable[[], Any]) -> Any:
        """Get or load a model by name.

        - If the model exists, returns it immediately.
        - If not, loads it using `loader_func` under lock, stores it, and returns it.
        """
        # Fast path without lock
        model = self._models.get(model_name)
        if model is not None:
            return model

        # Slow path with lock for first-time loads
        with self._lock:
            model = self._models.get(model_name)
            if model is not None:
                return model
            loaded = loader_func()
            self._models[model_name] = loaded
            return loaded


# Singleton instance for global access
model_cache = ModelCache()


