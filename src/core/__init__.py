"""
Core modules for the Asterisk AI Voice Agent.

This package contains the centralized state management and playback
management components that replace the dict soup in the original engine.
"""

from .models import CallSession, PlaybackRef, ProviderSession, TransportConfig
from .session_store import SessionStore
from .playback_manager import PlaybackManager
from .conversation_coordinator import ConversationCoordinator

__all__ = [
    'CallSession',
    'PlaybackRef', 
    'ProviderSession',
    'TransportConfig',
    'SessionStore',
    'PlaybackManager',
    'ConversationCoordinator'
]
