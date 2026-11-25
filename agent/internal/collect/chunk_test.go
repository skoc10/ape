package collect

import (
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"testing"
)

func gunzipArray(t *testing.T, data []byte) []json.RawMessage {
	t.Helper()
	zr, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(zr)
	if err != nil {
		t.Fatal(err)
	}
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil {
		t.Fatalf("chunk is not a JSON array of whole objects: %v", err)
	}
	return items
}

func incompressible(t *testing.T, n int) string {
	t.Helper()
	buf := make([]byte, n/2)
	if _, err := rand.Read(buf); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(buf)
}

func TestPackKeepsObjectsWholeAndWithinTheLimit(t *testing.T) {
	const limit = 20 * 1024
	var objects [][]byte
	for i := range 200 {
		obj, _ := json.Marshal(map[string]any{"i": i, "blob": incompressible(t, 2048)})
		objects = append(objects, obj)
	}
	chunks, skipped, err := pack(objects, limit)
	if err != nil || skipped != 0 {
		t.Fatalf("pack: err=%v skipped=%d", err, skipped)
	}
	if len(chunks) < 2 {
		t.Fatalf("expected several chunks, got %d", len(chunks))
	}
	next := 0
	for _, ch := range chunks {
		if len(ch.gzip) > limit {
			t.Fatalf("chunk of %d bytes exceeds the %d limit", len(ch.gzip), limit)
		}
		items := gunzipArray(t, ch.gzip)
		if len(items) != ch.count {
			t.Fatalf("count = %d, chunk holds %d objects", ch.count, len(items))
		}
		for _, item := range items {
			var decoded struct{ I int }
			if err := json.Unmarshal(item, &decoded); err != nil || decoded.I != next {
				t.Fatalf("object %d is missing, split or out of order (%v)", next, err)
			}
			next++
		}
	}
	if next != 200 {
		t.Fatalf("got %d objects back, want 200", next)
	}
}

func TestPackSkipsAnObjectThatCannotFit(t *testing.T) {
	small, _ := json.Marshal(map[string]any{"name": "small"})
	huge, _ := json.Marshal(map[string]any{"blob": incompressible(t, 64*1024)})
	chunks, skipped, err := pack([][]byte{small, huge, small}, 8*1024)
	if err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, ch := range chunks {
		total += ch.count
	}
	if skipped != 1 || total != 2 {
		t.Fatalf("skipped=%d kept=%d, want 1 and 2", skipped, total)
	}
}

func TestPackEmpty(t *testing.T) {
	chunks, skipped, err := pack(nil, 1024)
	if err != nil || len(chunks) != 0 || skipped != 0 {
		t.Fatalf("pack(nil) = %v, %d, %v", chunks, skipped, err)
	}
}

func TestDefaultChunkFitsInAFrame(t *testing.T) {
	// base64 expands by 4/3; the envelope and payload fields need the rest.
	if encoded := (maxChunkGzipBytes + 2) / 3 * 4; encoded+2048 > 512*1024 {
		t.Fatalf("a full chunk (%d base64 bytes) does not fit a 512 KiB frame", encoded)
	}
}
