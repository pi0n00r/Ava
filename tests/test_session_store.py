"""
Unit tests for SessionStore.

Tests the atomic operations and invariants of the SessionStore.
"""

import pytest
import asyncio
from src.core.models import CallSession, PlaybackRef
from src.core.session_store import SessionStore


class TestSessionStore:
    """Test SessionStore atomic operations and invariants."""
    
    @pytest.fixture
    async def session_store(self):
        """Create a SessionStore instance for testing."""
        return SessionStore()
    
    @pytest.fixture
    def sample_session(self):
        """Create a sample CallSession for testing."""
        return CallSession(
            call_id="test_call_123",
            caller_channel_id="1758498324.399",
            local_channel_id="Local/test@ai-agent-media-fork/n",
            bridge_id="bridge_123",
            provider_name="local",
            conversation_state="greeting"
        )
    
    @pytest.mark.asyncio
    async def test_upsert_and_get_by_call_id(self, session_store, sample_session):
        """Test basic upsert and retrieval by call_id."""
        # Upsert session
        await session_store.upsert_call(sample_session)
        
        # Retrieve by call_id
        retrieved = await session_store.get_by_call_id("test_call_123")
        assert retrieved is not None
        assert retrieved.call_id == "test_call_123"
        assert retrieved.caller_channel_id == "1758498324.399"
        assert retrieved.bridge_id == "bridge_123"
    
    @pytest.mark.asyncio
    async def test_get_by_channel_id(self, session_store, sample_session):
        """Test retrieval by various channel IDs."""
        await session_store.upsert_call(sample_session)
        
        # Test caller_channel_id
        retrieved = await session_store.get_by_channel_id("1758498324.399")
        assert retrieved is not None
        assert retrieved.call_id == "test_call_123"
        
        # Test local_channel_id
        retrieved = await session_store.get_by_channel_id("Local/test@ai-agent-media-fork/n")
        assert retrieved is not None
        assert retrieved.call_id == "test_call_123"
    
    @pytest.mark.asyncio
    async def test_remove_call(self, session_store, sample_session):
        """Test call removal and cleanup of all channel mappings."""
        await session_store.upsert_call(sample_session)
        
        # Verify session exists
        assert await session_store.get_by_call_id("test_call_123") is not None
        assert await session_store.get_by_channel_id("1758498324.399") is not None
        assert await session_store.get_by_channel_id("Local/test@ai-agent-media-fork/n") is not None
        
        # Remove call
        removed = await session_store.remove_call("test_call_123")
        assert removed is not None
        assert removed.call_id == "test_call_123"
        
        # Verify all mappings are cleaned up
        assert await session_store.get_by_call_id("test_call_123") is None
        assert await session_store.get_by_channel_id("1758498324.399") is None
        assert await session_store.get_by_channel_id("Local/test@ai-agent-media-fork/n") is None
    
    @pytest.mark.asyncio
    async def test_gating_token_operations(self, session_store, sample_session):
        """Test TTS gating token add/remove operations."""
        await session_store.upsert_call(sample_session)
        
        # Initial state
        session = await session_store.get_by_call_id("test_call_123")
        assert not session.tts_playing
        assert session.tts_active_count == 0
        assert session.audio_capture_enabled == False  # Default from CallSession
        
        # Add gating token
        success = await session_store.set_gating_token("test_call_123", "playback_1")
        assert success
        
        # Verify gating state
        session = await session_store.get_by_call_id("test_call_123")
        assert session.tts_playing
        assert session.tts_active_count == 1
        assert session.audio_capture_enabled == False
        assert "playback_1" in session.tts_tokens

        # Duplicate add should be idempotent
        success = await session_store.set_gating_token("test_call_123", "playback_1")
        assert success
        session = await session_store.get_by_call_id("test_call_123")
        assert session.tts_active_count == 1
        
        # Add second token
        success = await session_store.set_gating_token("test_call_123", "playback_2")
        assert success
        
        session = await session_store.get_by_call_id("test_call_123")
        assert session.tts_active_count == 2
        assert "playback_1" in session.tts_tokens
        assert "playback_2" in session.tts_tokens
        
        # Remove first token
        success = await session_store.clear_gating_token("test_call_123", "playback_1")
        assert success
        
        session = await session_store.get_by_call_id("test_call_123")
        assert session.tts_active_count == 1
        assert "playback_1" not in session.tts_tokens
        assert "playback_2" in session.tts_tokens
        assert session.tts_playing  # Still playing (active_count > 0)
        assert not session.audio_capture_enabled  # Still disabled (active_count > 0)
        
        # Remove second token
        success = await session_store.clear_gating_token("test_call_123", "playback_2")
        assert success
        
        session = await session_store.get_by_call_id("test_call_123")
        assert session.tts_active_count == 0
        assert "playback_2" not in session.tts_tokens
        assert not session.tts_playing  # No longer playing (active_count == 0)
        assert session.audio_capture_enabled  # Re-enabled (active_count == 0)

        # Duplicate clear should be idempotent
        success = await session_store.clear_gating_token("test_call_123", "playback_2")
        assert success
        session = await session_store.get_by_call_id("test_call_123")
        assert session.tts_active_count == 0
        assert session.audio_capture_enabled
    
    @pytest.mark.asyncio
    async def test_playback_references(self, session_store):
        """Test playback reference add/remove operations."""
        playback_ref = PlaybackRef(
            playback_id="test_playback_123",
            call_id="test_call_123",
            channel_id="1758498324.399",
            bridge_id="bridge_123",
            media_uri="sound:ai-generated/test",
            audio_file="/tmp/test.ulaw"
        )
        
        # Add playback reference
        await session_store.add_playback(playback_ref)
        
        # Verify it exists
        retrieved = await session_store.get_playback("test_playback_123")
        assert retrieved is not None
        assert retrieved.playback_id == "test_playback_123"
        assert retrieved.call_id == "test_call_123"
        
        # Remove playback reference
        removed = await session_store.pop_playback("test_playback_123")
        assert removed is not None
        assert removed.playback_id == "test_playback_123"
        
        # Verify it's gone
        assert await session_store.get_playback("test_playback_123") is None
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(self, session_store):
        """Test concurrent operations are atomic."""
        # Create multiple sessions concurrently
        sessions = []
        for i in range(10):
            session = CallSession(
                call_id=f"test_call_{i}",
                caller_channel_id=f"channel_{i}",
                provider_name="local"
            )
            sessions.append(session)
        
        # Upsert all sessions concurrently
        await asyncio.gather(*[
            session_store.upsert_call(session) for session in sessions
        ])
        
        # Verify all sessions exist
        for i in range(10):
            retrieved = await session_store.get_by_call_id(f"test_call_{i}")
            assert retrieved is not None
            assert retrieved.call_id == f"test_call_{i}"
    
    @pytest.mark.asyncio
    async def test_session_stats(self, session_store, sample_session):
        """Test session statistics."""
        # Initial stats
        stats = await session_store.get_session_stats()
        assert stats["active_calls"] == 0
        assert stats["active_playbacks"] == 0
        
        # Add session and playback
        await session_store.upsert_call(sample_session)
        await session_store.add_playback(PlaybackRef(
            playback_id="test_playback",
            call_id="test_call_123",
            channel_id="1758498324.399",
            bridge_id="bridge_123",
            media_uri="sound:test",
            audio_file="/tmp/test.ulaw"
        ))
        
        # Check updated stats
        stats = await session_store.get_session_stats()
        assert stats["active_calls"] == 1
        assert stats["active_playbacks"] == 1
