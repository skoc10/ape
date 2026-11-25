package transport

import (
	"math/rand/v2"
	"time"
)

// Backoff is exponential with full jitter: attempt n waits a random duration in
// (base*2^n / 2, base*2^n], capped at Max. Jitter keeps a fleet of agents from
// reconnecting in lockstep after a Control Plane restart.
type Backoff struct {
	Base time.Duration
	Max  time.Duration

	attempt int
}

// DefaultBackoff is the reconnect policy from the protocol: 1 s up to 60 s.
func DefaultBackoff() Backoff { return Backoff{Base: time.Second, Max: 60 * time.Second} }

// Next returns the delay before the next attempt and advances the sequence.
func (b *Backoff) Next() time.Duration {
	ceiling := b.Max
	if b.attempt < 30 { // avoid shifting into overflow
		if d := b.Base << b.attempt; d > 0 && d < b.Max {
			ceiling = d
		}
	}
	b.attempt++
	half := ceiling / 2
	return half + time.Duration(rand.Int64N(int64(half)+1)) //nolint:gosec // jitter, not security
}

// Reset starts the sequence over after a healthy session.
func (b *Backoff) Reset() { b.attempt = 0 }

// Saturate jumps to the maximum delay, for failures that retrying fast cannot fix.
func (b *Backoff) Saturate() { b.attempt = 30 }
