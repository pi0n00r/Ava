package troubleshoot

import "testing"

func TestExtractToolCalls(t *testing.T) {
	t.Parallel()

	logData := "" +
		"2026-01-30T17:21:43.227800-07:00 [info     ] 🔧 Deepgram tool call: check_extension_status({'extension': '2765'}) [src.tools.adapters.deepgram] call_id=1769818882.1484 function_call_id=call_AkCimSaNLM4lXmdND1WrA38y\n" +
		"2026-01-30T17:21:43.228552-07:00 [info     ] ✅ Tool check_extension_status executed: success [src.tools.adapters.deepgram] call_id=1769818882.1484 function_call_id=call_AkCimSaNLM4lXmdND1WrA38y message=available\n"

	calls := ExtractToolCalls(logData)
	if len(calls) != 1 {
		t.Fatalf("expected 1 tool call, got %d", len(calls))
	}
	if calls[0].Name != "check_extension_status" {
		t.Fatalf("name=%q", calls[0].Name)
	}
	if calls[0].Status != "success" {
		t.Fatalf("status=%q", calls[0].Status)
	}
	if calls[0].Message != "available" {
		t.Fatalf("message=%q", calls[0].Message)
	}
}
