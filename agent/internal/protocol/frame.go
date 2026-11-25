package protocol

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

// Version is the only protocol version this agent speaks.
const Version = 1

// MaxFrameBytes is the largest text frame either side may send.
const MaxFrameBytes = 512 * 1024

// ErrInvalidFrame is returned for frames that are not valid protocol messages.
var ErrInvalidFrame = errors.New("invalid protocol frame")

// Frame is the wire envelope. Payload stays raw until the type is known.
type Frame struct {
	V       int             `json:"v"`
	Type    MessageType     `json:"type"`
	ID      string          `json:"id"`
	TS      time.Time       `json:"ts"`
	Payload json.RawMessage `json:"payload"`
}

// Encode wraps payload into an envelope of the given type.
func Encode(t MessageType, payload any) ([]byte, error) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal %s payload: %w", t, err)
	}
	id, err := NewULID(time.Now())
	if err != nil {
		return nil, err
	}
	data, err := json.Marshal(Frame{V: Version, Type: t, ID: id, TS: time.Now().UTC(), Payload: raw})
	if err != nil {
		return nil, fmt.Errorf("marshal %s frame: %w", t, err)
	}
	if len(data) > MaxFrameBytes {
		return nil, fmt.Errorf("%s frame is %d bytes, limit is %d", t, len(data), MaxFrameBytes)
	}
	return data, nil
}

// Decode parses the envelope and checks version and type. Unknown fields are rejected.
func Decode(data []byte) (Frame, error) {
	var f Frame
	if len(data) > MaxFrameBytes {
		return f, fmt.Errorf("%w: too large", ErrInvalidFrame)
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&f); err != nil {
		return f, fmt.Errorf("%w: malformed envelope", ErrInvalidFrame)
	}
	if f.V != Version {
		return f, fmt.Errorf("%w: unsupported version %d", ErrInvalidFrame, f.V)
	}
	if !knownType(f.Type) {
		return f, fmt.Errorf("%w: unknown type", ErrInvalidFrame)
	}
	return f, nil
}

// DecodePayload strictly unmarshals the frame payload into out.
func (f Frame) DecodePayload(out any) error {
	dec := json.NewDecoder(bytes.NewReader(f.Payload))
	dec.DisallowUnknownFields()
	if err := dec.Decode(out); err != nil {
		return fmt.Errorf("%w: bad %s payload", ErrInvalidFrame, f.Type)
	}
	return nil
}

func knownType(t MessageType) bool {
	switch t {
	case MessageTypeHello, MessageTypeHelloAck, MessageTypePing, MessageTypePong,
		MessageTypeCollectRequest, MessageTypeCollectChunk, MessageTypeCollectDone, MessageTypeError:
		return true
	}
	return false
}

const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// NewULID returns a 26 character ULID: 48 bit millisecond timestamp + 80 random bits.
func NewULID(now time.Time) (string, error) {
	var raw [16]byte
	ms := uint64(now.UnixMilli())                        //nolint:gosec // timestamps after 1970 are non-negative
	binary.BigEndian.PutUint16(raw[0:2], uint16(ms>>32)) //nolint:gosec // upper 16 of 48 bits
	binary.BigEndian.PutUint32(raw[2:6], uint32(ms))     //nolint:gosec // lower 32 of 48 bits
	if _, err := rand.Read(raw[6:]); err != nil {
		return "", fmt.Errorf("ulid entropy: %w", err)
	}
	// 128 bits -> 26 base32 characters (the first carries only 3 bits).
	out := make([]byte, 26)
	hi := binary.BigEndian.Uint64(raw[0:8])
	lo := binary.BigEndian.Uint64(raw[8:16])
	for i := 25; i >= 0; i-- {
		out[i] = crockford[lo&31]
		lo = lo>>5 | hi<<59
		hi >>= 5
	}
	return string(out), nil
}
