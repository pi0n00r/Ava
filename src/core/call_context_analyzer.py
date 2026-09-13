"""
Call Context Analyzer - stub used for future adaptive VAD features.
Currently inert unless adaptive features are enabled.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict

import structlog

logger = structlog.get_logger(__name__)


class CallEnvironment(Enum):
    QUIET_OFFICE = "quiet_office"
    NOISY_ENVIRONMENT = "noisy_environment"
    MOBILE_CALL = "mobile_call"
    CONFERENCE_CALL = "conference_call"
    UNKNOWN = "unknown"


@dataclass
class CallConditions:
    environment: CallEnvironment
    noise_level: float
    speech_ratio: float
    interruption_rate: float
    call_duration: float
    quality_score: float


class CallContextAnalyzer:
    """Lightweight analyzer placeholder for future adaptive VAD tuning."""

    def __init__(self) -> None:
        self._history: Dict[str, Dict[str, Any]] = {}

    def update_call_event(self, call_id: str, event_type: str, data: Dict[str, Any]) -> None:
        hist = self._history.setdefault(
            call_id,
            {
                "start_time": time.time(),
                "interruption_count": 0,
                "total_turns": 0,
                "speech_events": [],
            },
        )
        if event_type == "barge_in":
            hist["interruption_count"] += 1
        elif event_type == "turn_complete":
            hist["total_turns"] += 1
        elif event_type == "speech_event":
            hist["speech_events"].append({"timestamp": time.time(), "data": data})

    def analyze_call_conditions(self, call_id: str, vad_stats: Dict[str, Any]) -> CallConditions:
        hist = self._history.setdefault(
            call_id,
            {
                "start_time": time.time(),
                "interruption_count": 0,
                "total_turns": 0,
                "speech_events": [],
            },
        )
        duration = max(time.time() - hist["start_time"], 1.0)
        speech_ratio = float(vad_stats.get("speech_ratio", 0.0))
        interruption_rate = float(hist.get("interruption_count", 0)) / max(hist.get("total_turns", 1), 1)
        noise_level = float(vad_stats.get("noise_level", 0.5))
        environment = self._classify_environment(noise_level, speech_ratio, interruption_rate)
        quality_score = (1.0 - noise_level + min(speech_ratio * 2, 1.0) + (1.0 - min(interruption_rate * 2, 1.0))) / 3.0
        return CallConditions(
            environment=environment,
            noise_level=noise_level,
            speech_ratio=speech_ratio,
            interruption_rate=interruption_rate,
            call_duration=duration,
            quality_score=quality_score,
        )

    def cleanup_call(self, call_id: str) -> None:
        self._history.pop(call_id, None)

    @staticmethod
    def _classify_environment(noise_level: float, speech_ratio: float, interruption_rate: float) -> CallEnvironment:
        if noise_level < 0.3 and interruption_rate < 0.2:
            return CallEnvironment.QUIET_OFFICE
        if noise_level > 0.7:
            return CallEnvironment.NOISY_ENVIRONMENT
        if interruption_rate > 0.4 or speech_ratio > 0.7:
            return CallEnvironment.CONFERENCE_CALL
        if noise_level > 0.4 and speech_ratio < 0.4:
            return CallEnvironment.MOBILE_CALL
        return CallEnvironment.UNKNOWN
