package protocol

import (
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

const examplesDir = "../../../packages/protocol/examples"

func payloadFor(t MessageType) any {
	switch t {
	case MessageTypeHello:
		return &HelloPayload{}
	case MessageTypeHelloAck:
		return &HelloAckPayload{}
	case MessageTypePing:
		return &PingPayload{}
	case MessageTypePong:
		return &PongPayload{}
	case MessageTypeCollectRequest:
		return &CollectRequestPayload{}
	case MessageTypeCollectChunk:
		return &CollectChunkPayload{}
	case MessageTypeCollectDone:
		return &CollectDonePayload{}
	case MessageTypeError:
		return &ErrorPayload{}
	}
	return nil
}

// The same example files are decoded by the Python test suite.
func TestExamplesDecode(t *testing.T) {
	files, err := filepath.Glob(filepath.Join(examplesDir, "*.json"))
	if err != nil || len(files) != 8 {
		t.Fatalf("expected 8 example files, got %d (%v)", len(files), err)
	}
	for _, file := range files {
		name := strings.TrimSuffix(filepath.Base(file), ".json")
		t.Run(name, func(t *testing.T) {
			data, err := os.ReadFile(file) //nolint:gosec // test fixture path
			if err != nil {
				t.Fatal(err)
			}
			frame, err := Decode(data)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}
			if string(frame.Type) != name {
				t.Fatalf("type = %q, want %q", frame.Type, name)
			}
			if err := frame.DecodePayload(payloadFor(frame.Type)); err != nil {
				t.Fatalf("payload: %v", err)
			}
		})
	}
}

func TestEncodeRoundTrip(t *testing.T) {
	in := HelloPayload{AgentVersion: "0.1.0", K8SVersion: "v1.31.2", NodeCount: 3, Capabilities: []string{"metrics"}}
	data, err := Encode(MessageTypeHello, in)
	if err != nil {
		t.Fatal(err)
	}
	frame, err := Decode(data)
	if err != nil {
		t.Fatal(err)
	}
	var out HelloPayload
	if err := frame.DecodePayload(&out); err != nil {
		t.Fatal(err)
	}
	if out.AgentVersion != in.AgentVersion || out.NodeCount != 3 || len(out.Capabilities) != 1 {
		t.Fatalf("round trip mismatch: %+v", out)
	}
	if !regexp.MustCompile(`^[0-9A-HJKMNP-TV-Z]{26}$`).MatchString(frame.ID) {
		t.Fatalf("bad ULID %q", frame.ID)
	}
}

func TestDecodeRejectsInvalidFrames(t *testing.T) {
	cases := map[string]string{
		"not json":      `nope`,
		"wrong version": `{"v":2,"type":"pong","id":"x","ts":"2026-11-25T05:00:00Z","payload":{}}`,
		"unknown type":  `{"v":1,"type":"exec","id":"x","ts":"2026-11-25T05:00:00Z","payload":{}}`,
		"extra field":   `{"v":1,"type":"pong","id":"x","ts":"2026-11-25T05:00:00Z","payload":{},"cmd":"rm"}`,
		"too large":     strings.Repeat(" ", MaxFrameBytes+1),
	}
	for name, input := range cases {
		if _, err := Decode([]byte(input)); !errors.Is(err, ErrInvalidFrame) {
			t.Errorf("%s: err = %v, want ErrInvalidFrame", name, err)
		}
	}
	frame, err := Decode([]byte(`{"v":1,"type":"collect_request","id":"x","ts":"2026-11-25T05:00:00Z","payload":{"run_id":"r","resources":[],"exclude_namespaces":[],"command":"kubectl delete"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if err := frame.DecodePayload(&CollectRequestPayload{}); !errors.Is(err, ErrInvalidFrame) {
		t.Errorf("unknown payload field accepted: %v", err)
	}
}

func TestEncodeRejectsOversizedFrames(t *testing.T) {
	big := CollectChunkPayload{Data: strings.Repeat("A", MaxFrameBytes)}
	if _, err := Encode(MessageTypeCollectChunk, big); err == nil {
		t.Fatal("oversized frame was encoded")
	}
}

func TestULIDsAreUniqueAndTimeOrdered(t *testing.T) {
	seen := map[string]bool{}
	for range 1000 {
		id, err := NewULID(time.Now())
		if err != nil {
			t.Fatal(err)
		}
		if seen[id] {
			t.Fatalf("duplicate ULID %s", id)
		}
		seen[id] = true
	}
	early, _ := NewULID(time.UnixMilli(1_700_000_000_000))
	late, _ := NewULID(time.UnixMilli(1_800_000_000_000))
	if early[:10] >= late[:10] {
		t.Fatalf("ULIDs are not time ordered: %s >= %s", early, late)
	}
}
