"""
Audio Gating Manager for Echo Prevention

This module provides audio gating functionality to prevent echo/loopback
in providers that use server-side VAD (like OpenAI Realtime API).

The gating is provider-specific and opt-in only. Providers like Deepgram
that handle echo internally are not affected (pass-through).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class GatingState:
    """Per-call gating state for echo prevention.
    
    Attributes:
        agent_is_speaking: Whether the agent is currently generating audio
        last_agent_audio_ts: Timestamp of last agent audio event
        buffered_chunks: Deque of audio chunks buffered during agent speech
        buffer_max_size: Maximum number of chunks to buffer (prevents memory issues)
        total_buffered: Total chunks buffered (for metrics)
        total_dropped: Total chunks dropped due to echo detection (for metrics)
        total_forwarded: Total chunks forwarded (for metrics)
    """
    agent_is_speaking: bool = False
    last_agent_audio_ts: float = 0.0
    buffered_chunks: deque = field(default_factory=lambda: deque(maxlen=25))
    buffer_max_size: int = 25  # 500ms at 20ms chunks
    total_buffered: int = 0
    total_dropped: int = 0
    total_forwarded: int = 0


class AudioGatingManager:
    """Manages audio gating for providers requiring echo prevention.
    
    This manager implements audio gating (half-duplex mode) with local VAD
    for interrupt detection. It's designed to prevent agent echo/loopback
    while maintaining the ability for users to interrupt the agent.
    
    Key Features:
    - Provider-specific configuration (opt-in only)
    - Local VAD for interrupt detection
    - Audio buffering during agent speech
    - Comprehensive logging for debugging
    - Per-call state isolation
    
    Note: This is ONLY active for providers configured with gating_enabled=True.
    All other providers pass through unchanged with minimal overhead.
    """
    
    def __init__(self, vad_manager=None):
        """Initialize the audio gating manager.
        
        Args:
            vad_manager: Optional EnhancedVADManager for interrupt detection.
                        If None, falls back to pure gating without interrupts.
        """
        self._states: Dict[str, GatingState] = {}
        self._vad = vad_manager
        self._lock = asyncio.Lock()
        
        # Provider-specific configuration
        # CRITICAL: Default is gating_enabled=False (pass-through)
        self._provider_configs = {
            'openai_realtime': {
                'gating_enabled': True,
                'vad_threshold': 0.7,  # High confidence needed for interrupt
                'post_speech_grace_ms': 100,  # Grace period after agent stops
                'buffer_on_no_vad': True,  # Buffer if VAD unavailable
                'sample_rate': 24000,  # OpenAI Realtime uses 24kHz
            },
            'deepgram': {
                'gating_enabled': False,  # Deepgram handles echo internally
                'sample_rate': 8000,  # Deepgram uses 8kHz
            },
            'local_only': {
                'gating_enabled': False,  # Local provider doesn't need gating
                'sample_rate': 16000,  # Common for local models
            },
            'hybrid_support': {
                'gating_enabled': False,  # Hybrid mode doesn't need gating
                'sample_rate': 16000,  # Common for hybrid pipelines
            },
            'elevenlabs_agent': {
                'gating_enabled': False,  # ElevenLabs handles VAD/echo internally
                'sample_rate': 16000,  # ElevenLabs uses 16kHz PCM16
            }
        }
        
        logger.info(
            "🎛️ AudioGatingManager initialized",
            vad_available=self._vad is not None,
            gated_providers=[k for k, v in self._provider_configs.items() if v.get('gating_enabled')],
            passthrough_providers=[k for k, v in self._provider_configs.items() if not v.get('gating_enabled')]
        )
    
    async def should_forward_audio(
        self, 
        call_id: str, 
        provider_name: str,
        audio_chunk: bytes,
        audio_format: str = "pcm16"
    ) -> Tuple[bool, Optional[List[bytes]]]:
        """Determine if audio should be forwarded to provider.
        
        This is the main entry point for audio gating logic. It checks if the
        provider needs gating, evaluates current agent state, uses VAD to detect
        interruptions, and decides whether to forward, buffer, or flush audio.
        
        Args:
            call_id: Unique call identifier
            provider_name: Name of the provider (e.g., 'openai_realtime')
            audio_chunk: Audio data to potentially forward
            audio_format: Format of audio (default: 'pcm16' for VAD)
        
        Returns:
            Tuple of (should_forward, buffered_chunks_to_flush)
            - should_forward: True if this chunk should be sent to provider
            - buffered_chunks_to_flush: List of buffered chunks to send first, or None
        
        Examples:
            >>> should_forward, buffered = await manager.should_forward_audio(
            ...     "call123", "openai_realtime", audio_data, "pcm16"
            ... )
            >>> if should_forward:
            ...     if buffered:
            ...         for chunk in buffered:
            ...             await provider.send_audio(chunk)
            ...     await provider.send_audio(audio_data)
        """
        # Check if this provider needs gating
        config = self._provider_configs.get(provider_name, {})
        if not config.get('gating_enabled', False):
            # Pass through for providers that don't need gating
            logger.debug(
                "🔓 Audio pass-through (gating disabled)",
                call_id=call_id,
                provider=provider_name,
            )
            return True, None
        
        # Get or create state for this call
        state = self._get_state(call_id)
        
        # Check if agent is currently speaking
        if state.agent_is_speaking:
            logger.debug(
                "🚪 Audio gate CLOSED (agent speaking)",
                call_id=call_id,
                provider=provider_name,
            )
            
            # Check if user is trying to interrupt
            if self._vad and audio_format == "pcm16":
                try:
                    sample_rate = config.get('sample_rate', 8000)
                    vad_result = await self._vad.process_frame(call_id, audio_chunk, sample_rate)
                    
                    logger.debug(
                        "🎤 VAD interrupt check",
                        call_id=call_id,
                        confidence=round(vad_result.confidence, 3),
                        threshold=config['vad_threshold'],
                        energy=vad_result.energy_level,
                        is_speech=vad_result.is_speech,
                        sample_rate=sample_rate,
                    )
                    
                    if vad_result.confidence > config['vad_threshold']:
                        # High confidence: User IS interrupting!
                        logger.info(
                            "🎤 USER INTERRUPTING AGENT",
                            call_id=call_id,
                            provider=provider_name,
                            vad_confidence=round(vad_result.confidence, 3),
                            energy=vad_result.energy_level,
                            buffered_count=len(state.buffered_chunks),
                        )
                        
                        # Mark agent as no longer speaking
                        state.agent_is_speaking = False
                        
                        # Flush any buffered audio + this chunk
                        buffered = list(state.buffered_chunks)
                        state.buffered_chunks.clear()
                        state.total_forwarded += len(buffered) + 1
                        
                        logger.debug(
                            "📤 Flushing buffer on interrupt",
                            call_id=call_id,
                            buffer_size=len(buffered),
                        )
                        
                        return True, buffered  # Forward this + buffered
                    else:
                        # Low confidence: Probably echo or noise, buffer it
                        state.buffered_chunks.append(audio_chunk)
                        state.total_buffered += 1
                        
                        logger.debug(
                            "📦 Buffering audio (low VAD confidence - likely echo)",
                            call_id=call_id,
                            buffer_size=len(state.buffered_chunks),
                            vad_confidence=round(vad_result.confidence, 3),
                        )
                        
                        return False, None  # Don't forward
                        
                except Exception as e:
                    logger.warning(
                        "⚠️ VAD processing failed, falling back to pure gating",
                        call_id=call_id,
                        error=str(e),
                        exc_info=True,
                    )
                    # Fall through to buffer without VAD
            
            # No VAD available or wrong format: pure gating (buffer everything)
            if config.get('buffer_on_no_vad', True):
                state.buffered_chunks.append(audio_chunk)
                state.total_buffered += 1
                
                logger.debug(
                    "📦 Buffering audio (no VAD available)",
                    call_id=call_id,
                    buffer_size=len(state.buffered_chunks),
                )
                
                return False, None
            else:
                # Drop audio if configured not to buffer
                state.total_dropped += 1
                logger.debug(
                    "🗑️ Dropping audio (no VAD, buffer disabled)",
                    call_id=call_id,
                )
                return False, None
        else:
            # Agent NOT speaking: forward all audio
            logger.debug(
                "🔓 Audio gate OPEN (agent not speaking)",
                call_id=call_id,
                provider=provider_name,
            )
            
            # Check if we have buffered audio to flush
            if state.buffered_chunks:
                buffered = list(state.buffered_chunks)
                state.buffered_chunks.clear()
                state.total_forwarded += len(buffered) + 1
                
                logger.info(
                    "📤 FLUSHING BUFFERED AUDIO (gate opened)",
                    call_id=call_id,
                    buffer_size=len(buffered),
                    total_buffered=state.total_buffered,
                    total_forwarded=state.total_forwarded,
                )
                
                return True, buffered
            
            state.total_forwarded += 1
            return True, None
    
    def set_agent_speaking(self, call_id: str, is_speaking: bool, provider_name: str = "unknown") -> None:
        """Update agent speaking state.
        
        This should be called by the provider when it starts/stops generating audio.
        
        Args:
            call_id: Unique call identifier
            is_speaking: True if agent started speaking, False if finished
            provider_name: Name of the provider (for logging)
        """
        state = self._get_state(call_id)
        
        if is_speaking:
            state.agent_is_speaking = True
            state.last_agent_audio_ts = time.time()
            logger.info(
                "🚪 AUDIO GATE CLOSED - Agent started speaking",
                call_id=call_id,
                provider=provider_name,
            )
        else:
            # Small grace period to let any trailing echo clear
            state.agent_is_speaking = False
            logger.info(
                "🔓 AUDIO GATE OPENED - Agent finished speaking",
                call_id=call_id,
                provider=provider_name,
                buffered_count=len(state.buffered_chunks),
            )
    
    def get_stats(self, call_id: str) -> Dict[str, int]:
        """Get gating statistics for a call.
        
        Args:
            call_id: Unique call identifier
        
        Returns:
            Dictionary with statistics (buffered, dropped, forwarded counts)
        """
        state = self._states.get(call_id)
        if not state:
            return {'total_buffered': 0, 'total_dropped': 0, 'total_forwarded': 0}
        
        return {
            'total_buffered': state.total_buffered,
            'total_dropped': state.total_dropped,
            'total_forwarded': state.total_forwarded,
            'current_buffer_size': len(state.buffered_chunks),
        }
    
    async def cleanup_call(self, call_id: str) -> None:
        """Clean up state when call ends.
        
        Args:
            call_id: Unique call identifier
        """
        async with self._lock:
            state = self._states.pop(call_id, None)
            if state:
                logger.info(
                    "🧹 Audio gating state cleaned up",
                    call_id=call_id,
                    total_buffered=state.total_buffered,
                    total_dropped=state.total_dropped,
                    total_forwarded=state.total_forwarded,
                )
    
    def _get_state(self, call_id: str) -> GatingState:
        """Get or create gating state for call.
        
        Args:
            call_id: Unique call identifier
        
        Returns:
            GatingState for this call
        """
        if call_id not in self._states:
            self._states[call_id] = GatingState()
            logger.debug(
                "📝 Created new gating state",
                call_id=call_id,
            )
        return self._states[call_id]
