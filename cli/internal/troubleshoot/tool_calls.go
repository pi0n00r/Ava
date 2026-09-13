package troubleshoot

import (
	"regexp"
	"strings"
)

type ToolCallRecord struct {
	Name      string `json:"name"`
	Status    string `json:"status,omitempty"`
	Message   string `json:"message,omitempty"`
	Arguments string `json:"arguments,omitempty"`
}

var (
	toolCallRe = regexp.MustCompile(`(?i)tool call:\s*([a-zA-Z0-9_]+)\((.*)\)`)
	toolExecRe = regexp.MustCompile(`(?i)Tool\s+([a-zA-Z0-9_]+)\s+executed:\s*([a-zA-Z0-9_]+)`)
)

// ExtractToolCalls parses log data to extract tool call invocations and results.
func ExtractToolCalls(logData string) []ToolCallRecord {
	lines := strings.Split(logData, "\n")
	records := make([]ToolCallRecord, 0, 8)
	pendingByID := make(map[string]int)
	pendingByName := make(map[string][]int)

	for _, line := range lines {
		_, event, fields, ok := parseLogLine(line)
		if !ok {
			continue
		}

		if m := toolCallRe.FindStringSubmatch(event); len(m) > 2 {
			name := strings.TrimSpace(m[1])
			args := strings.TrimSpace(m[2])
			rec := ToolCallRecord{
				Name:      name,
				Arguments: args,
			}
			records = append(records, rec)
			idx := len(records) - 1
			if id := strings.TrimSpace(fields["function_call_id"]); id != "" {
				pendingByID[id] = idx
			} else {
				pendingByName[name] = append(pendingByName[name], idx)
			}
			continue
		}

		if m := toolExecRe.FindStringSubmatch(event); len(m) > 2 {
			name := strings.TrimSpace(m[1])
			status := strings.TrimSpace(m[2])
			idx := -1
			if id := strings.TrimSpace(fields["function_call_id"]); id != "" {
				if v, ok := pendingByID[id]; ok {
					idx = v
					delete(pendingByID, id)
				}
			}
			if idx == -1 {
				queue := pendingByName[name]
				if len(queue) > 0 {
					idx = queue[0]
					if len(queue) > 1 {
						pendingByName[name] = queue[1:]
					} else {
						delete(pendingByName, name)
					}
				}
			}
			if idx == -1 {
				records = append(records, ToolCallRecord{Name: name})
				idx = len(records) - 1
			}
			rec := records[idx]
			rec.Status = status
			if msg := strings.TrimSpace(fields["message"]); msg != "" {
				rec.Message = msg
			}
			records[idx] = rec
		}
	}

	return records
}
