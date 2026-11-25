package transport

import (
	"testing"
	"time"
)

func TestBackoffGrowsWithJitterAndIsCapped(t *testing.T) {
	b := DefaultBackoff()
	ceilings := []time.Duration{1, 2, 4, 8, 16, 32, 60, 60, 60}
	for i, c := range ceilings {
		ceiling := c * time.Second
		d := b.Next()
		if d < ceiling/2 || d > ceiling {
			t.Fatalf("attempt %d: delay %s outside [%s, %s]", i, d, ceiling/2, ceiling)
		}
	}
	for range 100 {
		if d := b.Next(); d > 60*time.Second {
			t.Fatalf("delay %s exceeds the 60 s cap", d)
		}
	}
}

func TestBackoffIsJittered(t *testing.T) {
	seen := map[time.Duration]bool{}
	for range 50 {
		b := Backoff{Base: time.Second, Max: 60 * time.Second, attempt: 5}
		seen[b.Next()] = true
	}
	if len(seen) < 10 {
		t.Fatalf("only %d distinct delays in 50 draws: not jittered", len(seen))
	}
}

func TestBackoffResetAndSaturate(t *testing.T) {
	b := DefaultBackoff()
	for range 6 {
		b.Next()
	}
	b.Reset()
	if d := b.Next(); d > time.Second {
		t.Fatalf("after Reset the delay is %s, want <= 1s", d)
	}
	b.Saturate()
	if d := b.Next(); d < 30*time.Second {
		t.Fatalf("after Saturate the delay is %s, want >= 30s", d)
	}
}
