"""
Default HTML templates + variable reference for email tools.

This module intentionally has no heavy imports so it can be reused by:
- AI Engine tools (send_email_summary / request_transcript)
- Admin UI backend (template preview / defaults)
"""

# -----------------------------------------------------------------------------
# Default templates (HTML)
# -----------------------------------------------------------------------------

DEFAULT_SEND_EMAIL_SUMMARY_HTML_TEMPLATE = """
<html>
<head>
  <style>
    body {
      font-family: Arial, sans-serif;
      line-height: 1.6;
      color: #333;
      max-width: 800px;
      margin: 0 auto;
    }
    .header {
      background: #4F46E5;
      color: white;
      padding: 20px;
      border-radius: 5px 5px 0 0;
    }
    .content {
      padding: 20px;
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-top: none;
      border-radius: 0 0 5px 5px;
    }
    .greeting {
      font-size: 16px;
      margin-bottom: 20px;
    }
    .metadata {
      background: #F3F4F6;
      padding: 15px;
      border-radius: 5px;
      margin-bottom: 20px;
    }
    .metadata p {
      margin: 5px 0;
    }
    .transcript {
      background: #FAFAFA;
      padding: 15px;
      border-left: 3px solid #4F46E5;
      margin-top: 20px;
      font-family: monospace;
      word-wrap: break-word;
    }
    .footer {
      margin-top: 20px;
      padding-top: 20px;
      border-top: 1px solid #e5e7eb;
      color: #6b7280;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div class="header">
    <h2>📞 Call Summary</h2>
  </div>
  <div class="content">
    <div class="greeting">
      {% if caller_name %}
      <p>Hello {{ caller_name }},</p>
      {% else %}
      <p>Hello,</p>
      {% endif %}
      <p>This is a summary of your recent call with our AI Voice Agent.</p>
    </div>
    
    <div class="metadata">
      <p><strong>Date:</strong> {{ call_date }}</p>
      <p><strong>Duration:</strong> {{ duration }}</p>
      {% if caller_number %}
      <p><strong>Caller:</strong> {{ caller_number }}</p>
      {% endif %}
      {% if outcome %}
      <p><strong>Outcome:</strong> {{ outcome }}</p>
      {% endif %}
    </div>
    
    {% if include_transcript and transcript %}
    <h3>Conversation Transcript</h3>
    <div class="transcript">{{ transcript_html }}</div>
    {% if transcript_note %}
    <p style="color: #6b7280; font-size: 12px; margin-top: 10px;">
      <em>{{ transcript_note }}</em>
    </p>
    {% endif %}
    {% endif %}
    
    <div class="footer">
      <p><em>Powered by AI Voice Agent</em></p>
    </div>
  </div>
</body>
</html>
"""


DEFAULT_REQUEST_TRANSCRIPT_HTML_TEMPLATE = """
<html>
<head>
  <style>
    body {
      font-family: Arial, sans-serif;
      line-height: 1.6;
      color: #333;
      max-width: 800px;
      margin: 0 auto;
    }
    .header {
      background: #10B981;
      color: white;
      padding: 20px;
      border-radius: 5px 5px 0 0;
    }
    .content {
      padding: 20px;
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-top: none;
      border-radius: 0 0 5px 5px;
    }
    .greeting {
      font-size: 16px;
      margin-bottom: 20px;
    }
    .metadata {
      background: #F0FDF4;
      padding: 15px;
      border-radius: 5px;
      margin-bottom: 20px;
    }
    .metadata p {
      margin: 5px 0;
    }
    .transcript {
      background: #FAFAFA;
      padding: 15px;
      border-left: 3px solid #10B981;
      margin-top: 20px;
      font-family: monospace;
      word-wrap: break-word;
    }
    .footer {
      margin-top: 20px;
      padding-top: 20px;
      border-top: 1px solid #e5e7eb;
      color: #6b7280;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div class="header">
    <h2>📧 Your Call Transcript</h2>
  </div>
  <div class="content">
    <div class="greeting">
      {% if caller_name %}
      <p>Hello {{ caller_name }},</p>
      {% else %}
      <p>Hello,</p>
      {% endif %}
      <p>Thank you for your call. As requested, here is the transcript of your conversation with our AI Voice Agent.</p>
    </div>
    
    <div class="metadata">
      <p><strong>Date:</strong> {{ call_date }}</p>
      <p><strong>Duration:</strong> {{ duration }}</p>
      {% if caller_number %}
      <p><strong>Caller:</strong> {{ caller_number }}</p>
      {% endif %}
    </div>
    
    <h3>Conversation Transcript</h3>
    <div class="transcript">{{ transcript_html }}</div>
    
    <div class="footer">
      <p>If you have any questions or need assistance, please don't hesitate to contact us.</p>
      <p><em>Powered by AI Voice Agent</em></p>
    </div>
  </div>
</body>
</html>
"""


# -----------------------------------------------------------------------------
# Variable reference (Jinja2)
# -----------------------------------------------------------------------------
#
# Email templates are rendered with Jinja2 (sandboxed). Variables below are
# provided to both templates (some may be empty depending on context/tool).

EMAIL_TEMPLATE_VARIABLES = [
    {"name": "call_id", "description": "Unique call identifier (e.g., 1770333362.2115)."},
    {"name": "context_name", "description": "Resolved context name for the call (e.g., support, sales)."},
    {"name": "call_date", "description": "Call start datetime (string)."},
    {"name": "call_start_time", "description": "Call start datetime (string, ISO-ish)."},
    {"name": "call_end_time", "description": "Call end datetime (string, ISO-ish)."},
    {"name": "duration", "description": "Human-readable duration (e.g., 2m 13s)."},
    {"name": "duration_seconds", "description": "Duration in seconds (number)."},
    {"name": "caller_name", "description": "Caller name (string, may be empty)."},
    {"name": "caller_number", "description": "Caller phone number (string)."},
    {"name": "called_number", "description": "Called phone number (string, may be empty)."},
    {"name": "outcome", "description": "Call outcome (string). For hangup tracking this is typically 'caller_hangup' or 'agent_hangup' (or 'transferred')."},
    {"name": "call_outcome", "description": "Alias for outcome (string)."},
    {"name": "hangup_initiator", "description": "Derived from outcome: 'caller' | 'agent' | 'system' | ''."},
    {"name": "include_transcript", "description": "Whether transcript is included (boolean)."},
    {"name": "transcript", "description": "Transcript as plain text (string)."},
    {"name": "transcript_html", "description": "Transcript rendered as HTML with <br/> newlines (string)."},
    {"name": "transcript_note", "description": "Optional transcript note (string, may be empty)."},
    {"name": "recipient_email", "description": "Request Transcript only: caller recipient email (string)."},
]
