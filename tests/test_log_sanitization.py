"""
Test log sanitization for AAVA-37.

Verifies that sensitive information is redacted from logs.
"""

import pytest
import structlog
from src.logging_config import sanitize_secrets


class TestLogSanitization:
    """Tests for secret sanitization processor."""
    
    def test_redact_api_key(self):
        """Should redact api_key field."""
        event_dict = {
            'message': 'Testing',
            'api_key': 'sk-1234567890abcdef',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['api_key'] == 'sk***REDACTED***'
        assert result['message'] == 'Testing'
    
    def test_redact_password(self):
        """Should redact password field."""
        event_dict = {
            'message': 'Login attempt',
            'password': 'SuperSecret123!',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['password'] == 'Su***REDACTED***'
        assert result['message'] == 'Login attempt'
    
    def test_redact_token(self):
        """Should redact token field."""
        event_dict = {
            'message': 'Auth',
            'access_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['access_token'].startswith('ey***REDACTED***')
        assert result['message'] == 'Auth'
    
    def test_redact_authorization_header(self):
        """Should redact authorization header."""
        event_dict = {
            'message': 'HTTP request',
            'authorization': 'Bearer sk-1234567890abcdef',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['authorization'].startswith('Be***REDACTED***')
    
    def test_case_insensitive_matching(self):
        """Should match keys case-insensitively."""
        event_dict = {
            'API_KEY': 'sk-test',
            'Password': 'secret',
            'ACCESS_TOKEN': 'token123',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert 'REDACTED' in result['API_KEY']
        assert 'REDACTED' in result['Password']
        assert 'REDACTED' in result['ACCESS_TOKEN']
    
    def test_nested_dict_sanitization(self):
        """Should sanitize nested dictionaries."""
        event_dict = {
            'message': 'Config loaded',
            'config': {
                'api_key': 'sk-nested',
                'timeout': 30,
                'credentials': {
                    'password': 'secret',
                }
            }
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert 'REDACTED' in result['config']['api_key']
        assert result['config']['timeout'] == 30
        assert 'REDACTED' in result['config']['credentials']['password']
    
    def test_preserve_non_sensitive_data(self):
        """Should not redact non-sensitive fields."""
        event_dict = {
            'message': 'Normal log',
            'user_id': '12345',
            'action': 'login',
            'timestamp': '2025-11-06T10:00:00',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['message'] == 'Normal log'
        assert result['user_id'] == '12345'
        assert result['action'] == 'login'
        assert result['timestamp'] == '2025-11-06T10:00:00'
    
    def test_empty_string_not_redacted(self):
        """Should preserve empty strings."""
        event_dict = {
            'message': 'Test',
            'api_key': '',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['api_key'] == ''
    
    def test_none_values_preserved(self):
        """Should preserve None values."""
        event_dict = {
            'message': 'Test',
            'api_key': None,
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert result['api_key'] is None
    
    def test_list_with_sensitive_data(self):
        """Should sanitize lists containing sensitive data."""
        event_dict = {
            'message': 'Multiple keys',
            'api_keys': ['sk-key1', 'sk-key2', 'sk-key3'],
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert all('REDACTED' in key for key in result['api_keys'])
    
    def test_hyphenated_key_names(self):
        """Should match hyphenated key names."""
        event_dict = {
            'client-secret': 'secret123',
            'api-key': 'sk-test',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert 'REDACTED' in result['client-secret']
        assert 'REDACTED' in result['api-key']
    
    def test_no_false_positive_on_passthrough(self):
        """Should NOT redact passthrough_providers (false positive prevention)."""
        event_dict = {
            'message': 'Audio gating',
            'passthrough_providers': ['deepgram', 'local', 'hybrid_support'],
            'gated_providers': ['openai_realtime'],
        }
        result = sanitize_secrets(None, None, event_dict)
        
        # Should NOT be redacted (passthrough contains "pass" but shouldn't match)
        assert result['passthrough_providers'] == ['deepgram', 'local', 'hybrid_support']
        assert result['gated_providers'] == ['openai_realtime']
    
    def test_actual_password_field_still_redacted(self):
        """Should still redact actual password fields."""
        event_dict = {
            'user_password': 'secret123',
            'password': 'secret456',
            'pass': 'secret789',
        }
        result = sanitize_secrets(None, None, event_dict)
        
        assert 'REDACTED' in result['user_password']
        assert 'REDACTED' in result['password']
        assert 'REDACTED' in result['pass']
