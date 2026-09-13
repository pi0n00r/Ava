package troubleshoot

import "testing"

func TestExtractRCAHeaderFromConsoleLog(t *testing.T) {
	t.Parallel()

	logData := "2026-01-30T12:00:00.000000-07:00 [info     ] RCA_CALL_START [src.engine] call_id=1769799752.1415 caller_number=15555550123 called_number=2765 context_name=demo_google provider_name=google_live audio_transport=externalmedia tp_encoding=ulaw tp_sample_rate=8000 streaming_sample_rate=8000\n"

	h := ExtractRCAHeader(logData)
	if h == nil {
		t.Fatalf("expected header, got nil")
	}
	if h.CallID != "1769799752.1415" {
		t.Fatalf("call_id=%q", h.CallID)
	}
	if h.CallerNumber != "15555550123" {
		t.Fatalf("caller_number=%q", h.CallerNumber)
	}
	if h.CalledNumber != "2765" {
		t.Fatalf("called_number=%q", h.CalledNumber)
	}
	if h.ProviderName != "google_live" {
		t.Fatalf("provider_name=%q", h.ProviderName)
	}
	if h.ContextName != "demo_google" {
		t.Fatalf("context_name=%q", h.ContextName)
	}
	if h.AudioTransport != "externalmedia" {
		t.Fatalf("audio_transport=%q", h.AudioTransport)
	}
	if h.TransportProfileEncoding != "ulaw" || h.TransportProfileSampleRate != 8000 {
		t.Fatalf("transport_profile=%s@%d", h.TransportProfileEncoding, h.TransportProfileSampleRate)
	}
	if h.StreamingSampleRate != 8000 {
		t.Fatalf("streaming_sample_rate=%d", h.StreamingSampleRate)
	}
}

func TestIsBenignRCAErrorLine(t *testing.T) {
	t.Parallel()

	line := "[error    ] ARI command failed [src.ari_client] component=src.ari_client method=GET reason='{\"message\":\"Provided variable was not found\"}' service=ai-engine status=404 url=https://127.0.0.1:8089/ari/channels/1769719558.1020/variable"
	if !isBenignRCAErrorLine(line) {
		t.Fatalf("expected benign ARI variable 404 to be ignored")
	}
}
