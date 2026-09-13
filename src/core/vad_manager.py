"""
Enhanced VAD Manager - integrates WebRTC VAD and energy-based detection under a feature flag.
"""

from __future__ import annotations

import asyncio
import audioop
from dataclasses import dataclass
from typing import Dict, Optional, Any
import time

import structlog
from prometheus_client import Counter, Gauge, Histogram
from .call_context_analyzer import CallContextAnalyzer

try:
    import webrtcvad  # pyright: ignore[reportMissingImports]
    WEBRTC_VAD_AVAILABLE = True
except ImportError:  # pragma: no cover
    webrtcvad = None  # type: ignore
    WEBRTC_VAD_AVAILABLE = False

logger = structlog.get_logger(__name__)

# WebRTC VAD supported sample rates
WEBRTC_SUPPORTED_RATES = [8000, 16000, 32000]

# Prometheus metrics ---------------------------------------------------------
_VAD_FRAMES_TOTAL = Counter(
    "ai_agent_vad_frames_total",
    "Total audio frames processed by Enhanced VAD",
    labelnames=("result",),
)

_VAD_CONFIDENCE_HISTOGRAM = Histogram(
    "ai_agent_vad_confidence",
    "Enhanced VAD confidence distribution",
    buckets=(0.1, 0.3, 0.5, 0.7, 0.9, 1.0),
)

_VAD_ADAPTIVE_THRESHOLD = Gauge(
    "ai_agent_vad_adaptive_threshold",
    "Enhanced VAD adaptive energy threshold",
)


@dataclass
class VADResult:
    is_speech: bool
    confidence: float
    energy_level: int
    webrtc_result: bool
    frame_duration_ms: int = 20


class AdaptiveThreshold:
    def __init__(self, base_threshold: int, adaptation_rate: float = 0.1, max_samples: int = 100):
        self.base_threshold = base_threshold
        self.current_threshold = base_threshold
        self.adaptation_rate = adaptation_rate
        self.max_samples = max_samples
        self._noise_samples: list[int] = []
        self.noise_floor: float = 0.0

    def update(self, energy: int, is_speech: bool) -> None:
        if is_speech:
            return
        if len(self._noise_samples) >= self.max_samples:
            return
        self._noise_samples.append(energy)
        if len(self._noise_samples) < 10:
            return
        self.noise_floor = sum(self._noise_samples) / len(self._noise_samples)
        target_threshold = max(self.base_threshold, int(self.noise_floor * 2.5))
        self.current_threshold = int(
            self.current_threshold * (1 - self.adaptation_rate) + target_threshold * self.adaptation_rate
        )

    def get_threshold(self) -> int:
        return max(self.current_threshold, self.base_threshold)

    def reset(self) -> None:
        self.current_threshold = self.base_threshold
        self._noise_samples.clear()
        self.noise_floor = 0.0


class EnhancedVADManager:
    """Feature-flagged enhanced VAD manager used for barge-in heuristics."""

    def __init__(
        self,
        *,
        energy_threshold: int = 1500,
        confidence_threshold: float = 0.6,
        adaptive_threshold_enabled: bool = False,
        noise_adaptation_rate: float = 0.1,
        webrtc_aggressiveness: int = 1,
        min_speech_frames: int = 2,
        max_silence_frames: int = 15,
    ) -> None:
        self.energy_threshold = energy_threshold
        self.confidence_threshold = confidence_threshold
        self.adaptive_threshold_enabled = adaptive_threshold_enabled
        self.webrtc_aggressiveness = webrtc_aggressiveness
        self.min_speech_frames = max(1, min_speech_frames)
        self.max_silence_frames = max(1, max_silence_frames)

        self.webrtc_vad = None
        if WEBRTC_VAD_AVAILABLE:
            try:
                self.webrtc_vad = webrtcvad.Vad(self.webrtc_aggressiveness)
                logger.info("Enhanced VAD - WebRTC initialized", aggressiveness=self.webrtc_aggressiveness)
            except Exception:
                logger.warning("Enhanced VAD - WebRTC initialization failed", exc_info=True)
                self.webrtc_vad = None
        else:
            logger.warning("Enhanced VAD - WebRTC module not available")

        # Base configuration - don't mutate these
        self.base_energy_threshold = energy_threshold
        self.noise_adaptation_rate = noise_adaptation_rate
        
        # Per-call state tracking - no global state!
        self._call_states: Dict[str, Dict[str, Any]] = {}  # call_id -> state
        self._call_stats: Dict[str, Dict[str, float]] = {}
        self._lock = asyncio.Lock()
        
        # Call context analyzer for adaptive behavior
        self.context_analyzer = CallContextAnalyzer()
        self._adaptation_interval = 100  # Adapt every 100 frames (2 seconds)

    async def process_frame(self, call_id: str, audio_frame_pcm16: bytes, sample_rate: int = 8000) -> VADResult:
        if len(audio_frame_pcm16) < 320:
            audio_frame_pcm16 = audio_frame_pcm16.ljust(320, b"\x00")
            
        # Get or create per-call state
        call_state = self._get_call_state(call_id)
        
        # Periodic adaptive parameter adjustment (per-call)
        call_state['frame_count'] += 1
        if self.adaptive_threshold_enabled and call_state['frame_count'] % self._adaptation_interval == 0:
            await self._adapt_vad_parameters(call_id)
            
        energy = audioop.rms(audio_frame_pcm16, 2)
        webrtc_result = False
        if self.webrtc_vad and sample_rate in WEBRTC_SUPPORTED_RATES:
            try:
                webrtc_result = self.webrtc_vad.is_speech(audio_frame_pcm16, sample_rate)
            except Exception:
                logger.debug("Enhanced VAD - WebRTC processing error", exc_info=True, sample_rate=sample_rate)
        elif self.webrtc_vad and sample_rate not in WEBRTC_SUPPORTED_RATES:
            logger.debug(
                "Sample rate not supported by WebRTC VAD, using energy-only detection",
                sample_rate=sample_rate,
                supported_rates=WEBRTC_SUPPORTED_RATES,
                call_id=call_id
            )

        # Use per-call adaptive threshold
        threshold = call_state['adaptive_threshold'].get_threshold() if self.adaptive_threshold_enabled else self.base_energy_threshold
        energy_result = energy >= threshold

        if self.adaptive_threshold_enabled:
            call_state['adaptive_threshold'].update(energy, webrtc_result or energy_result)

        final_speech = self._smooth_frames_per_call(call_id, webrtc_result or energy_result)
        confidence = self._calc_confidence(webrtc_result, energy_result, energy, threshold)

        result = VADResult(
            is_speech=final_speech,
            confidence=confidence,
            energy_level=energy,
            webrtc_result=webrtc_result,
        )

        self._update_metrics(call_id, result, threshold)
        self._update_call_stats(call_id, result)
        return result

    def _get_call_state(self, call_id: str) -> Dict[str, Any]:
        """Get or create per-call state to avoid global mutations."""
        if call_id not in self._call_states:
            self._call_states[call_id] = {
                'adaptive_threshold': AdaptiveThreshold(
                    base_threshold=self.base_energy_threshold,
                    adaptation_rate=self.noise_adaptation_rate,
                ),
                'speech_frames': 0,
                'silence_frames': 0,
                'is_speaking': False,
                'frame_count': 0,
                'last_adaptation_time': time.time(),
            }
        return self._call_states[call_id]

    def _smooth_frames_per_call(self, call_id: str, raw_speech: bool) -> bool:
        """Per-call frame smoothing to avoid global state mutations."""
        call_state = self._get_call_state(call_id)
        
        if raw_speech:
            call_state['speech_frames'] += 1
            call_state['silence_frames'] = 0
            if not call_state['is_speaking'] and call_state['speech_frames'] >= self.min_speech_frames:
                call_state['is_speaking'] = True
                logger.debug("Enhanced VAD - Speech started", call_id=call_id, frames=call_state['speech_frames'])
        else:
            call_state['silence_frames'] += 1
            call_state['speech_frames'] = 0
            if call_state['is_speaking'] and call_state['silence_frames'] >= self.max_silence_frames:
                call_state['is_speaking'] = False
                logger.debug("Enhanced VAD - Speech ended", call_id=call_id, silence_frames=call_state['silence_frames'])
        
        return call_state['is_speaking']

    def _calc_confidence(self, webrtc_result: bool, energy_result: bool, energy: int, threshold: int) -> float:
        confidence = 0.0
        if webrtc_result:
            confidence += 0.4
        if energy_result:
            energy_ratio = min(energy / max(threshold, 1), 3.0)
            confidence += 0.4 * (energy_ratio / 3.0)
        if webrtc_result == energy_result:
            confidence += 0.2
        return min(confidence, 1.0)

    def _update_metrics(self, call_id: str, result: VADResult, threshold: int) -> None:
        try:
            label = "speech" if result.is_speech else "silence"
            _VAD_FRAMES_TOTAL.labels(label).inc()
            _VAD_CONFIDENCE_HISTOGRAM.observe(result.confidence)
            _VAD_ADAPTIVE_THRESHOLD.set(threshold)
        except Exception:
            logger.debug("Enhanced VAD - metrics update failed", exc_info=True)

    async def reset_call(self, call_id: str) -> None:
        """Properly clean up per-call state to prevent leaks."""
        async with self._lock:
            # Remove per-call state completely
            self._call_states.pop(call_id, None)
            self._call_stats.pop(call_id, None)
            logger.debug("VAD call state cleaned up", call_id=call_id)

    async def _adapt_vad_parameters(self, call_id: str) -> None:
        """Adapt VAD parameters based on call conditions - PER CALL, not global."""
        try:
            call_state = self._get_call_state(call_id)
            
            # Cooldown: only adapt every 5 seconds per call
            now = time.time()
            if now - call_state['last_adaptation_time'] < 5.0:
                return
            
            call_state['last_adaptation_time'] = now
            
            # Get current call statistics
            call_stats = self._call_stats.get(call_id, {})
            
            # Analyze call conditions
            conditions = self.context_analyzer.analyze_call_conditions(call_id, call_stats)
            
            adaptive = call_state['adaptive_threshold']
            current_base = int(adaptive.base_threshold)
            target_multiplier = 1.0

            if conditions.noise_level > 0.7:
                target_multiplier = 1.3
            elif conditions.noise_level < 0.3:
                target_multiplier = 0.8

            desired_base = int(self.base_energy_threshold * target_multiplier)

            # Smooth transitions to avoid oscillation between extremes
            smoothed_base = int(current_base * 0.8 + desired_base * 0.2)

            if abs(smoothed_base - current_base) >= 10:
                adaptive.base_threshold = max(1, smoothed_base)
                adaptive.current_threshold = int(adaptive.current_threshold * 0.8 + adaptive.base_threshold * 0.2)
                logger.debug(
                    "🧠 VAD adaptive threshold updated",
                    call_id=call_id,
                    noise_level=conditions.noise_level,
                    previous_base=current_base,
                    new_base=adaptive.base_threshold,
                )

            # Note: WebRTC VAD is shared, so we don't adapt it per-call to avoid conflicts
                    
        except Exception as e:
            logger.debug("VAD parameter adaptation error", call_id=call_id, error=str(e))

    def _update_call_stats(self, call_id: str, result: VADResult) -> None:
        """Update call statistics for adaptive behavior."""
        if call_id not in self._call_stats:
            self._call_stats[call_id] = {
                'total_frames': 0,
                'speech_frames': 0,
                'avg_energy': 0.0,
                'noise_level': 0.5,
                'speech_ratio': 0.0
            }
        
        stats = self._call_stats[call_id]
        stats['total_frames'] += 1
        
        if result.is_speech:
            stats['speech_frames'] += 1
            
        # Update running averages
        total = stats['total_frames']
        stats['avg_energy'] = (stats['avg_energy'] * (total - 1) + result.energy_level) / total
        stats['speech_ratio'] = stats['speech_frames'] / total
        
        # Estimate noise level based on energy during non-speech
        if not result.is_speech and result.energy_level > 0:
            stats['noise_level'] = (stats['noise_level'] * 0.9 + 
                                   min(result.energy_level / self.energy_threshold, 1.0) * 0.1)

    def notify_call_event(self, call_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """Notify context analyzer of call events for adaptive behavior."""
        self.context_analyzer.update_call_event(call_id, event_type, data)

    @staticmethod
    def mu_law_to_pcm16(frame_ulaw: bytes) -> bytes:
        if len(frame_ulaw) == 0:
            return b""
        try:
            return audioop.ulaw2lin(frame_ulaw, 2)
        except Exception:
            logger.debug("Enhanced VAD - ulaw to PCM16 conversion failed", exc_info=True)
            return b""
