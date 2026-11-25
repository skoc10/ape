package collect

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"fmt"
)

// A frame is at most 512 KiB of JSON. The chunk travels base64-encoded (4/3)
// next to a small envelope, so the gzip payload is kept well below 3/4 of that.
const maxChunkGzipBytes = 360 * 1024

// Roughly how much raw JSON is packed before compressing; gzip usually brings
// Kubernetes objects down by 5-10x, the splitter below handles the rest.
const targetRawBytes = 1024 * 1024

// chunk is one batch of whole objects, ready to send.
type chunk struct {
	count int
	gzip  []byte
}

func (c chunk) base64() string { return base64.StdEncoding.EncodeToString(c.gzip) }

// pack turns JSON-encoded objects into chunks whose gzip size is within limit.
// An object is never split; one that cannot fit on its own is reported in skipped.
func pack(objects [][]byte, limit int) (chunks []chunk, skipped int, err error) {
	for start := 0; start < len(objects); {
		end, raw := start, 0
		for end < len(objects) && (end == start || raw+len(objects[end]) <= targetRawBytes) {
			raw += len(objects[end])
			end++
		}
		packed, dropped, err := packRange(objects[start:end], limit)
		if err != nil {
			return nil, 0, err
		}
		chunks = append(chunks, packed...)
		skipped += dropped
		start = end
	}
	return chunks, skipped, nil
}

func packRange(objects [][]byte, limit int) ([]chunk, int, error) {
	if len(objects) == 0 {
		return nil, 0, nil
	}
	compressed, err := gzipArray(objects)
	if err != nil {
		return nil, 0, err
	}
	if len(compressed) <= limit {
		return []chunk{{count: len(objects), gzip: compressed}}, 0, nil
	}
	if len(objects) == 1 {
		return nil, 1, nil // a single object larger than a frame: leave it out
	}
	mid := len(objects) / 2
	left, leftSkipped, err := packRange(objects[:mid], limit)
	if err != nil {
		return nil, 0, err
	}
	right, rightSkipped, err := packRange(objects[mid:], limit)
	if err != nil {
		return nil, 0, err
	}
	return append(left, right...), leftSkipped + rightSkipped, nil
}

// gzipArray compresses the objects as one JSON array.
func gzipArray(objects [][]byte) ([]byte, error) {
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	write := func(p []byte) error {
		_, err := zw.Write(p)
		return err
	}
	if err := write([]byte("[")); err != nil {
		return nil, fmt.Errorf("gzip: %w", err)
	}
	for i, obj := range objects {
		if i > 0 {
			if err := write([]byte(",")); err != nil {
				return nil, fmt.Errorf("gzip: %w", err)
			}
		}
		if err := write(obj); err != nil {
			return nil, fmt.Errorf("gzip: %w", err)
		}
	}
	if err := write([]byte("]")); err != nil {
		return nil, fmt.Errorf("gzip: %w", err)
	}
	if err := zw.Close(); err != nil {
		return nil, fmt.Errorf("gzip: %w", err)
	}
	return buf.Bytes(), nil
}
