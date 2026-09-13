"""
Adaptive Streaming - Intelligent Provider-Agnostic Buffer Management

This module implements a 3-layer adaptive architecture:

Layer 1: Stream Characterization (auto-detect provider output pattern)
Layer 2: Multi-Factor Buffer Calculation (considers rate, pattern, config)
Layer 3: Real-Time Adaptive Control (runtime adjustments)
Layer 4: Provider Pattern Learning (persistent cache)

Key Features:
- Auto-detects provider output patterns (steady/moderate/bursty)
- Calculates optimal buffer based on sample rate, downsample ratio, and pattern
- Adapts in real-time to underruns and over-buffering
- Learns and caches provider patterns across calls
- WebRTC-compliant (40-200ms range)
- Provider-agnostic (works with any future provider)

References:
- WebRTC jitter buffer: 40ms initial, 100ms max
- ITU-T G.114: 50ms jitter compensation standard
- Google Gemini Live: 24kHz bursty output
- Deepgram: 8kHz/16kHz steady output
"""

from __future__ import annotations

import math
import statistics
import time
import json
import os
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class StreamPattern:
    """Characterized stream pattern from provider"""
    type: str  # "steady", "moderate", "bursty", "unknown"
    mean_interval_ms: float
    max_gap_ms: float
    variance: float
    optimal_buffer_ms: int


class StreamCharacterizer:
    """
    Automatically detect provider output characteristics during first 500ms.
    
    Uses statistical analysis of chunk arrival times to classify providers as:
    - steady: Low variance (CoV < 0.2) - e.g., Deepgram
    - moderate: Medium variance (CoV < 0.5) - e.g., OpenAI Realtime
    - bursty: High variance (CoV >= 0.5) - e.g., Google Live
    """
    
    def __init__(self):
        self.chunk_timestamps: List[float] = []
        self.chunk_sizes: List[int] = []
        self.characterization_done = False
        self.start_time = time.time()
        
    def add_chunk(self, chunk_size: int, timestamp: Optional[float] = None):
        """Record chunk arrival"""
        if self.characterization_done:
            return
            
        if timestamp is None:
            timestamp = time.time() - self.start_time
            
        self.chunk_timestamps.append(timestamp)
        self.chunk_sizes.append(chunk_size)
        
    def should_analyze(self) -> bool:
        """Check if we have enough data to analyze (500ms elapsed, 10+ chunks)"""
        if self.characterization_done:
            return False
            
        elapsed = time.time() - self.start_time
        return elapsed >= 0.5 and len(self.chunk_timestamps) >= 10
        
    def analyze(self) -> Optional[StreamPattern]:
        """Analyze collected data and classify stream pattern"""
        
        if len(self.chunk_timestamps) < 10:
            logger.warning(
                "Insufficient data for stream characterization",
                chunks_collected=len(self.chunk_timestamps)
            )
            return None
            
        try:
            # Calculate inter-arrival times (in milliseconds)
            intervals = [
                (t2 - t1) * 1000  # Convert to ms
                for t1, t2 in zip(self.chunk_timestamps[:-1], self.chunk_timestamps[1:])
            ]
            
            if not intervals:
                return None
                
            # Statistical analysis
            mean_interval = statistics.mean(intervals)
            variance = statistics.variance(intervals) if len(intervals) > 1 else 0
            max_gap = max(intervals)
            
            # Coefficient of Variation (normalized measure of dispersion)
            coefficient_of_variation = math.sqrt(variance) / mean_interval if mean_interval > 0 else 0
            
            # Classify pattern based on CoV
            if coefficient_of_variation < 0.2:
                pattern_type = "steady"
                safety_multiplier = 1.5  # 50% safety margin
            elif coefficient_of_variation < 0.5:
                pattern_type = "moderate"
                safety_multiplier = 2.0  # 100% safety margin
            else:
                pattern_type = "bursty"
                safety_multiplier = 2.5  # 150% safety margin
                
            # Calculate optimal buffer: max observed gap + safety margin
            optimal_buffer_ms = int(max_gap * safety_multiplier)
            
            # WebRTC compliance: clamp to 40-200ms range
            optimal_buffer_ms = max(40, min(200, optimal_buffer_ms))
            
            pattern = StreamPattern(
                type=pattern_type,
                mean_interval_ms=mean_interval,
                max_gap_ms=max_gap,
                variance=variance,
                optimal_buffer_ms=optimal_buffer_ms
            )
            
            self.characterization_done = True
            
            logger.info(
                "Stream pattern characterized",
                pattern_type=pattern_type,
                mean_interval_ms=f"{mean_interval:.1f}",
                max_gap_ms=f"{max_gap:.1f}",
                coefficient_of_variation=f"{coefficient_of_variation:.3f}",
                optimal_buffer_ms=optimal_buffer_ms,
                chunks_analyzed=len(intervals)
            )
            
            return pattern
            
        except Exception as e:
            logger.error("Failed to analyze stream pattern", error=str(e))
            return None


def calculate_optimal_buffer(
    stream_pattern: Optional[StreamPattern],
    wire_sample_rate: int,
    provider_sample_rate: int,
    base_config_ms: int
) -> int:
    """
    Calculate optimal buffer considering ALL factors.
    
    Args:
        stream_pattern: Characterized provider pattern (or None for unknown)
        wire_sample_rate: Output sample rate (e.g., 8000 for slin, 16000 for slin16)
        provider_sample_rate: Provider's output rate (e.g., 24000 for Google Live)
        base_config_ms: User's config hint (e.g., greeting_min_start_ms)
        
    Returns:
        Optimal buffer size in milliseconds (40-200ms range)
        
    Factors considered:
    1. Provider pattern (steady/moderate/bursty)
    2. Wire sample rate (higher rate = faster drain = more buffering)
    3. Downsample ratio (more aggressive = smoother = less buffering)
    4. User config (as baseline hint)
    """
    
    # Factor 1: Provider pattern requirement
    if stream_pattern and stream_pattern.optimal_buffer_ms > 0:
        pattern_buffer = stream_pattern.optimal_buffer_ms
    else:
        # Unknown provider - use conservative default
        pattern_buffer = 100  # Middle ground
        
    # Factor 2: Wire sample rate multiplier
    # Higher wire rate = faster buffer drain = need more buffering
    # Normalize to 8kHz baseline
    rate_multiplier = wire_sample_rate / 8000.0
    rate_adjusted_buffer = base_config_ms * rate_multiplier
    
    # Factor 3: Downsample ratio consideration
    # More aggressive downsample (24k→8k = 3x) provides smoother output
    # Less aggressive downsample (24k→16k = 1.5x) preserves jitter more
    if provider_sample_rate > 0 and wire_sample_rate > 0:
        downsample_ratio = provider_sample_rate / wire_sample_rate
        # Use sqrt to dampen the effect (3x becomes 1.73x factor)
        downsample_factor = 1.0 / math.sqrt(downsample_ratio)
    else:
        downsample_factor = 1.0
    
    # Combine all factors
    # Use max to ensure we meet all requirements
    optimal_buffer = max(
        pattern_buffer,           # Provider needs
        rate_adjusted_buffer,     # Wire rate needs
        base_config_ms           # User minimum
    ) * downsample_factor
    
    # WebRTC compliance: 40ms minimum, 200ms maximum
    optimal_buffer = max(40, min(200, int(optimal_buffer)))
    
    logger.debug(
        "Calculated optimal buffer",
        pattern_buffer=pattern_buffer if stream_pattern else "unknown",
        rate_adjusted=int(rate_adjusted_buffer),
        downsample_factor=f"{downsample_factor:.2f}",
        optimal_buffer_ms=optimal_buffer
    )
    
    return optimal_buffer


class AdaptiveBufferController:
    """
    Real-time adaptive control based on observed performance.
    
    Monitors:
    - Underrun events (buffer running dry)
    - Over-buffering (consistently high buffer levels)
    
    Actions:
    - Increase buffer on repeated underruns
    - Decrease buffer when over-provisioned (optimize latency)
    """
    
    def __init__(self, initial_buffer_ms: int):
        self.current_buffer_ms = initial_buffer_ms
        self.underrun_events: List[float] = []
        self.last_adjustment_time = time.time()
        self.adjustment_count = 0
        
    def on_underrun(self, call_id: str):
        """
        Buffer ran dry - may need more buffering.
        
        Only adjusts if we see 3+ underruns within 1 second window.
        This prevents over-reacting to isolated hiccups.
        """
        now = time.time()
        self.underrun_events.append(now)
        
        # Remove old events (>1 second ago)
        cutoff = now - 1.0
        self.underrun_events = [t for t in self.underrun_events if t > cutoff]
        
        # If 3+ underruns in 1 second, increase buffer
        if len(self.underrun_events) >= 3:
            old_buffer = self.current_buffer_ms
            self.current_buffer_ms = int(self.current_buffer_ms * 1.5)
            self.current_buffer_ms = min(self.current_buffer_ms, 200)  # Cap at WebRTC max
            self.adjustment_count += 1
            
            logger.warning(
                "🔧 Adaptive buffer increase (underruns detected)",
                call_id=call_id,
                old_buffer_ms=old_buffer,
                new_buffer_ms=self.current_buffer_ms,
                underrun_count=len(self.underrun_events),
                adjustment_num=self.adjustment_count
            )
            
            self.underrun_events.clear()
            self.last_adjustment_time = now
            return True  # Buffer changed
            
        return False  # No change
        
    def on_stable_period(self, call_id: str, avg_buffered_chunks: int, target_chunks: int):
        """
        System is stable - optimize buffer down if over-provisioned.
        
        Only adjusts every 5 seconds to avoid oscillation.
        """
        now = time.time()
        
        # Only adjust every 5 seconds
        if now - self.last_adjustment_time < 5.0:
            return False
            
        # If consistently over-buffered (2x target), reduce
        if avg_buffered_chunks > target_chunks * 2:
            old_buffer = self.current_buffer_ms
            self.current_buffer_ms = int(self.current_buffer_ms * 0.8)
            self.current_buffer_ms = max(self.current_buffer_ms, 40)  # Min WebRTC minimum
            self.adjustment_count += 1
            
            logger.info(
                "🔧 Adaptive buffer decrease (over-provisioned)",
                call_id=call_id,
                old_buffer_ms=old_buffer,
                new_buffer_ms=self.current_buffer_ms,
                avg_buffered=avg_buffered_chunks,
                target=target_chunks,
                adjustment_num=self.adjustment_count
            )
            
            self.last_adjustment_time = now
            return True  # Buffer changed
            
        return False  # No change


class ProviderPatternCache:
    """
    Learn and cache provider patterns across calls.
    
    Uses exponential moving average to merge new observations with cached data.
    Persists to JSON file for cross-session learning.
    """
    
    def __init__(self, cache_file: str = "data/provider_patterns.json"):
        self.cache_file = cache_file
        self.patterns: Dict[str, StreamPattern] = {}
        self._load_cache()
        
    def _load_cache(self):
        """Load cached patterns from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    for key, pattern_dict in data.items():
                        self.patterns[key] = StreamPattern(**pattern_dict)
                logger.info(
                    "Loaded provider pattern cache",
                    patterns_count=len(self.patterns)
                )
        except Exception as e:
            logger.warning("Failed to load provider pattern cache", error=str(e))
            
    def _save_cache(self):
        """Save patterns to disk"""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w') as f:
                data = {key: asdict(pattern) for key, pattern in self.patterns.items()}
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save provider pattern cache", error=str(e))
            
    def update_pattern(self, provider: str, sample_rate: int, pattern: StreamPattern):
        """
        Update learned pattern with exponential moving average.
        
        70% weight to existing pattern, 30% to new observation.
        This provides stability while adapting to changes.
        """
        key = f"{provider}@{sample_rate}Hz"
        
        if key in self.patterns:
            # Merge with existing pattern
            old = self.patterns[key]
            merged = StreamPattern(
                type=pattern.type,  # Use latest classification
                mean_interval_ms=old.mean_interval_ms * 0.7 + pattern.mean_interval_ms * 0.3,
                max_gap_ms=max(old.max_gap_ms, pattern.max_gap_ms),  # Keep worst-case
                variance=old.variance * 0.7 + pattern.variance * 0.3,
                optimal_buffer_ms=int(old.optimal_buffer_ms * 0.7 + pattern.optimal_buffer_ms * 0.3)
            )
            self.patterns[key] = merged
            logger.debug(
                "Updated cached provider pattern",
                key=key,
                old_buffer_ms=old.optimal_buffer_ms,
                new_buffer_ms=merged.optimal_buffer_ms
            )
        else:
            # First observation
            self.patterns[key] = pattern
            logger.info(
                "Cached new provider pattern",
                key=key,
                pattern_type=pattern.type,
                optimal_buffer_ms=pattern.optimal_buffer_ms
            )
            
        self._save_cache()
        
    def get_hint(self, provider: str, sample_rate: int) -> Optional[StreamPattern]:
        """Get cached pattern as initial hint"""
        key = f"{provider}@{sample_rate}Hz"
        pattern = self.patterns.get(key)
        
        if pattern:
            logger.debug(
                "Using cached provider pattern hint",
                key=key,
                pattern_type=pattern.type,
                optimal_buffer_ms=pattern.optimal_buffer_ms
            )
            
        return pattern


# Global instance (singleton)
_pattern_cache: Optional[ProviderPatternCache] = None


def get_pattern_cache() -> ProviderPatternCache:
    """Get global pattern cache singleton"""
    global _pattern_cache
    if _pattern_cache is None:
        _pattern_cache = ProviderPatternCache()
    return _pattern_cache
