"""
HTTP-based tools for pre-call and post-call execution.

- GenericHTTPLookupTool: Pre-call CRM/API lookups
- GenericWebhookTool: Post-call webhook notifications
"""

from .generic_lookup import GenericHTTPLookupTool
from .generic_webhook import GenericWebhookTool

__all__ = ["GenericHTTPLookupTool", "GenericWebhookTool"]
