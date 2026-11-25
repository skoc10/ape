package telemetry

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
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
	if _, ok := entry["trace_id"]; ok {
		t.Fatal("trace_id present without a span")
	}
}

func TestLoggerAddsTraceIDs(t *testing.T) {
	var buf bytes.Buffer
	provider := sdktrace.NewTracerProvider()
	ctx, span := provider.Tracer("test").Start(context.Background(), "op")
	defer span.End()

	NewLogger(&buf).With("component", "x").InfoContext(ctx, "inside span")

	var entry map[string]any
	if err := json.Unmarshal(buf.Bytes(), &entry); err != nil {
		t.Fatal(err)
	}
	if id, _ := entry["trace_id"].(string); len(id) != 32 {
		t.Fatalf("trace_id missing: %v", entry)
	}
	if id, _ := entry["span_id"].(string); len(id) != 16 {
		t.Fatalf("span_id missing: %v", entry)
	}
}
