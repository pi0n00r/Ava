from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from backends.interface import BackendInterface


class BackendRegistry:
    def __init__(self, kind: str):
        self._kind = kind
        self._backends: Dict[str, Type[BackendInterface]] = {}

    def register(self, cls: Type[BackendInterface]) -> Type[BackendInterface]:
        self._backends[cls.name()] = cls
        return cls

    def get(self, name: str) -> Optional[Type[BackendInterface]]:
        return self._backends.get(name)

    def names(self) -> List[str]:
        return list(self._backends.keys())

    def all(self) -> Dict[str, Type[BackendInterface]]:
        return dict(self._backends)

    def info(self) -> List[Dict[str, Any]]:
        result = []
        for name, cls in self._backends.items():
            try:
                available = cls.is_available()
            except Exception:
                available = False
            try:
                schema = cls.config_schema()
            except Exception:
                schema = {}
            result.append({
                "name": name,
                "available": available,
                "schema": schema,
            })
        return result


STT_REGISTRY = BackendRegistry("stt")
TTS_REGISTRY = BackendRegistry("tts")
LLM_REGISTRY = BackendRegistry("llm")
