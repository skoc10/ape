package telemetry

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestNewLoggerWritesJSON(t *testing.T) {
	var buf bytes.Buffer
	NewLogger(&buf).Info("hello", "kind", "Pod")

	var entry map[string]any
	if err := json.Unmarshal(buf.Bytes(), &entry); err != nil {
		t.Fatalf("log line is not JSON: %v", err)
	}
	if entry["msg"] != "hello" || entry["kind"] != "Pod" {
		t.Fatalf("unexpected log entry: %v", entry)
	}
}
