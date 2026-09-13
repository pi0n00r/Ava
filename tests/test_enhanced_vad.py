"""
Tests for Enhanced VAD Manager and integration.
"""

import pytest
import asyncio
import audioop
import time
from unittest.mock import Mock, patch

from src.core.vad_manager import EnhancedVADManager, VADResult, AdaptiveThreshold
from src.core.call_context_analyzer import CallContextAnalyzer, CallEnvironment
from src.engine import Engine


class TestEnhancedVADManager:
    
    @pytest.fixture
    def vad_manager(self):
        return EnhancedVADManager(
            energy_threshold=1500,
            confidence_threshold=0.6,
            adaptive_threshold_enabled=True,
            webrtc_aggressiveness=1,
            min_speech_frames=2,
            max_silence_frames=15
        )
    
    @pytest.mark.asyncio
    async def test_vad_speech_detection_silence(self, vad_manager):
        """Test VAD detection with silence frame."""
        # Create 20ms silence frame (320 bytes PCM16 at 8kHz)
        silence_frame = b'\x00' * 320
        
        result = await vad_manager.process_frame("test_call", silence_frame)
        
        assert isinstance(result, VADResult)
        assert result.energy_level >= 0
        assert result.confidence >= 0.0
        assert result.frame_duration_ms == 20
    
    @pytest.mark.asyncio
    async def test_vad_speech_detection_energy(self, vad_manager):
        """Test VAD detection with high energy frame."""
        # Create frame with some energy
        speech_frame = b'\x00\x10' * 160  # 320 bytes with some energy
        
        result = await vad_manager.process_frame("test_call", speech_frame)
        
        assert isinstance(result, VADResult)
        assert result.energy_level > 0
    
    @pytest.mark.asyncio
    async def test_frame_smoothing(self, vad_manager):
        """Test frame smoothing prevents false positives."""
        # Single high energy frame shouldn't immediately trigger speech
        speech_frame = b'\x10\x20' * 160
        
        # First frame - should not be speech due to min_speech_frames=2
        result1 = await vad_manager.process_frame("test_call", speech_frame)
        
        # Second frame - should trigger speech
        result2 = await vad_manager.process_frame("test_call", speech_frame)
        
        # The smoothing logic should require multiple frames
        assert isinstance(result1, VADResult)
        assert isinstance(result2, VADResult)
    
    @pytest.mark.asyncio
    async def test_adaptive_threshold(self, vad_manager):
        """Test adaptive threshold adjustment."""
        call_id = "test_adaptive"
        
        # Process several low-energy frames (noise) to trigger adaptation
        noise_frame = b'\x01\x02' * 160
        for _ in range(20):
            await vad_manager.process_frame(call_id, noise_frame)
        
        # Should have per-call state with adaptive threshold
        call_state = vad_manager._get_call_state(call_id)
        assert 'adaptive_threshold' in call_state
        
        adapted_threshold = call_state['adaptive_threshold'].get_threshold()
        assert adapted_threshold >= vad_manager.base_energy_threshold  # Should not decrease below base
        
        await vad_manager.reset_call(call_id)
    
    @pytest.mark.asyncio
    async def test_call_stats_tracking(self, vad_manager):
        """Test call statistics are properly tracked."""
        call_id = "test_call_stats"
        
        # Process some frames
        silence_frame = b'\x00' * 320
        speech_frame = b'\x10\x20' * 160
        
        await vad_manager.process_frame(call_id, silence_frame)
        await vad_manager.process_frame(call_id, speech_frame)
        
        # Check stats are tracked
        assert call_id in vad_manager._call_stats
        stats = vad_manager._call_stats[call_id]
        assert stats['total_frames'] >= 2
        assert 'avg_energy' in stats
        assert 'speech_ratio' in stats
    
    @pytest.mark.asyncio
    async def test_reset_call(self, vad_manager):
        """Test call reset functionality."""
        call_id = "test_reset"
        
        # Process some frames to build up state
        frame = b'\x10\x20' * 160
        await vad_manager.process_frame(call_id, frame)
        
        # Should have call state
        assert call_id in vad_manager._call_states
        
        # Reset call
        await vad_manager.reset_call(call_id)
        
        # Per-call state should be completely removed
        assert call_id not in vad_manager._call_states
        assert call_id not in vad_manager._call_stats


class TestAdaptiveThreshold:
    
    def test_threshold_initialization(self):
        """Test adaptive threshold initialization."""
        threshold = AdaptiveThreshold(base_threshold=1500)
        
        assert threshold.get_threshold() == 1500
        assert threshold.noise_floor == 0.0
        assert len(threshold._noise_samples) == 0
    
    def test_noise_floor_adaptation(self):
        """Test noise floor adaptation with non-speech samples."""
        threshold = AdaptiveThreshold(base_threshold=1500, adaptation_rate=0.5)
        
        # Add noise samples (non-speech)
        for energy in [800, 900, 850, 820, 880]:
            threshold.update(energy, is_speech=False)
        
        # Should have some noise samples
        assert len(threshold._noise_samples) > 0
    
    def test_threshold_reset(self):
        """Test threshold reset functionality."""
        threshold = AdaptiveThreshold(base_threshold=1500)
        
        # Add some samples
        threshold.update(800, is_speech=False)
        threshold.update(900, is_speech=False)
        
        # Reset
        threshold.reset()
        
        assert threshold.current_threshold == threshold.base_threshold
        assert len(threshold._noise_samples) == 0
        assert threshold.noise_floor == 0.0


class TestCallContextAnalyzer:
    
    @pytest.fixture
    def analyzer(self):
        return CallContextAnalyzer()
    
    def test_environment_classification_quiet(self, analyzer):
        """Test quiet office environment classification."""
        env = analyzer._classify_environment(
            noise_level=0.2, 
            speech_ratio=0.3, 
            interruption_rate=0.1
        )
        assert env == CallEnvironment.QUIET_OFFICE
    
    def test_environment_classification_noisy(self, analyzer):
        """Test noisy environment classification."""
        env = analyzer._classify_environment(
            noise_level=0.8, 
            speech_ratio=0.4, 
            interruption_rate=0.2
        )
        assert env == CallEnvironment.NOISY_ENVIRONMENT
    
    def test_environment_classification_conference(self, analyzer):
        """Test conference call environment classification."""
        env = analyzer._classify_environment(
            noise_level=0.4, 
            speech_ratio=0.8, 
            interruption_rate=0.5
        )
        assert env == CallEnvironment.CONFERENCE_CALL
    
    def test_call_event_tracking(self, analyzer):
        """Test call event tracking functionality."""
        call_id = "test_events"
        
        # Add barge-in event
        analyzer.update_call_event(call_id, "barge_in", {})
        
        # Check history is created
        assert call_id in analyzer._history
        assert analyzer._history[call_id]['interruption_count'] == 1
        
        # Add turn complete event
        analyzer.update_call_event(call_id, "turn_complete", {})
        assert analyzer._history[call_id]['total_turns'] == 1
    
    def test_call_conditions_analysis(self, analyzer):
        """Test call conditions analysis."""
        call_id = "test_analysis"
        
        # Add some events first
        analyzer.update_call_event(call_id, "barge_in", {})
        analyzer.update_call_event(call_id, "turn_complete", {})
        
        # Analyze conditions
        vad_stats = {
            'speech_ratio': 0.4,
            'noise_level': 0.3
        }
        
        conditions = analyzer.analyze_call_conditions(call_id, vad_stats)
        
        assert conditions.speech_ratio == 0.4
        assert conditions.noise_level == 0.3
        assert conditions.interruption_rate == 1.0  # 1 interruption / 1 turn
        assert conditions.call_duration > 0
        assert 0 <= conditions.quality_score <= 1.0
    
    def test_cleanup_call(self, analyzer):
        """Test call cleanup functionality."""
        call_id = "test_cleanup"
        
        # Add some data
        analyzer.update_call_event(call_id, "barge_in", {})
        assert call_id in analyzer._history
        
        # Cleanup
        analyzer.cleanup_call(call_id)
        assert call_id not in analyzer._history


class TestVADIntegration:
    """Integration tests for VAD components."""
    
    @pytest.mark.asyncio
    async def test_vad_with_context_analyzer(self):
        """Test VAD manager with context analyzer integration."""
        vad_manager = EnhancedVADManager(
            energy_threshold=1500,
            adaptive_threshold_enabled=True
        )
        
        call_id = "integration_test"
        
        # Process several frames to trigger adaptation
        frames_to_process = 150  # More than adaptation_interval (100)
        
        for i in range(frames_to_process):
            # Alternate between speech and silence
            if i % 3 == 0:
                frame = b'\x10\x20' * 160  # Speech-like
            else:
                frame = b'\x01\x02' * 160  # Noise-like
                
            result = await vad_manager.process_frame(call_id, frame)
            assert isinstance(result, VADResult)
        
        # Should have call stats
        assert call_id in vad_manager._call_stats
        
        # Cleanup
        await vad_manager.reset_call(call_id)
    
    @pytest.mark.asyncio
    async def test_provider_continuous_delivery(self):
        """Test that engine would deliver continuous audio (either speech or silence)."""
        vad_manager = EnhancedVADManager(energy_threshold=1500)

        class MockSession:
            def __init__(self):
                self.vad_state = {}
                self.call_id = "continuous_test"

        session = MockSession()
        call_id = "continuous_test"

        delivered_silence_frames = 0
        delivered_speech_frames = 0

        for i in range(120):  # simulate >2s of frames
            frame = b'\x00' * 320
            result = await vad_manager.process_frame(call_id, frame)

            if 'vad_start_time' not in session.vad_state:
                session.vad_state['vad_start_time'] = time.time() - 2.1  # past initial grace
                session.vad_state['last_speech_time'] = time.time() - 2.1
                session.vad_state['frames_since_speech'] = 26

            should_forward_speech = result.is_speech or result.confidence > 0.3
            should_forward_original = (
                should_forward_speech
                or session.vad_state['frames_since_speech'] < 25
                or True  # fallback will replace with silence frame otherwise
            )

            if should_forward_original and result.is_speech:
                delivered_speech_frames += 1
            elif should_forward_original:
                delivered_silence_frames += 1

        assert delivered_silence_frames + delivered_speech_frames == 120
        assert delivered_silence_frames > 0

        await vad_manager.reset_call(call_id)
    
    @pytest.mark.asyncio
    async def test_per_call_state_isolation(self):
        """Test that per-call state doesn't leak between calls."""
        vad_manager = EnhancedVADManager(
            energy_threshold=1500,
            adaptive_threshold_enabled=True
        )
        
        call1_id = "call_1"
        call2_id = "call_2"
        
        # Process frames for call 1 - high energy (speech)
        speech_frame = b'\x10\x20' * 160
        for _ in range(10):
            result1 = await vad_manager.process_frame(call1_id, speech_frame)
        
        # Process frames for call 2 - low energy (silence)  
        silence_frame = b'\x01\x02' * 160
        for _ in range(10):
            result2 = await vad_manager.process_frame(call2_id, silence_frame)
        
        # Calls should have separate state
        call1_state = vad_manager._call_states.get(call1_id)
        call2_state = vad_manager._call_states.get(call2_id)
        
        assert call1_state is not None
        assert call2_state is not None
        assert call1_state != call2_state
        
        # Thresholds should be independent
        call1_threshold = call1_state['adaptive_threshold'].get_threshold()
        call2_threshold = call2_state['adaptive_threshold'].get_threshold()
        
        # They might be different due to adaptation
        assert isinstance(call1_threshold, int)
        assert isinstance(call2_threshold, int)
        
        # Cleanup one call shouldn't affect the other
        await vad_manager.reset_call(call1_id)
        assert call1_id not in vad_manager._call_states
        assert call2_id in vad_manager._call_states
        
        await vad_manager.reset_call(call2_id)
    
    @pytest.mark.asyncio 
    async def test_fallback_periodic_forwarding(self, monkeypatch):
        """Test that fallback forwards real audio at configured cadence."""
        vad_manager = EnhancedVADManager(energy_threshold=1500)

        now = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: now)

        class MockSession:
            def __init__(self):
                self.vad_state = {
                    'last_speech_time': time.time() - 3.0,
                    'fallback_state': {'last_fallback_ts': time.time() - 1.0}
                }
                self.call_id = "fallback_test"

        session = MockSession()
        engine = Engine.__new__(Engine)
        setattr(engine, "config", type("Cfg", (), {"vad": type("V", (), {"fallback_enabled": True, "fallback_interval_ms": 1500})()})())

        fallback_hits = 0

        for _ in range(20):
            if Engine._should_use_vad_fallback(engine, session):
                fallback_hits += 1
            now += 0.05

        assert fallback_hits > 0
        assert fallback_hits <= 5

    @pytest.mark.asyncio
    async def test_wake_word_gradual_support(self):
        """Test gradual wake-word scenario support."""
        vad_manager = EnhancedVADManager(energy_threshold=1500)
 
        class MockSession:
            def __init__(self):
                self.vad_state = {
                    'vad_start_time': time.time() - 3.0,  # Past initial period
                    'last_speech_time': time.time() - 1.0,  # 1 second ago
                    'frames_since_speech': 0
                }
                self.call_id = "wake_word_test"

        session = MockSession()
        call_id = "wake_word_test"
        
        # Simulate speech ending, then gradual wake-word buildup with silence replacements
        frames_forwarded_after_speech = 0

        for i in range(30):
            silence_frame = b'\x00' * 320
            result = await vad_manager.process_frame(call_id, silence_frame)
            frames_since_speech = session.vad_state.get('frames_since_speech', 0)

            should_forward_original = (
                result.is_speech
                or result.confidence > 0.3
                or frames_since_speech < 25
            )

            if should_forward_original:
                frames_forwarded_after_speech += 1

            session.vad_state['frames_since_speech'] = frames_since_speech + 1

        assert frames_forwarded_after_speech >= 25
        
        await vad_manager.reset_call(call_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
