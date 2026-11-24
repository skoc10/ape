// Package telemetry holds logging, tracing and metrics setup for the agent.
package telemetry

import (
	"io"
	"log/slog"
)

// NewLogger returns a JSON structured logger writing to w.
func NewLogger(w io.Writer) *slog.Logger {
	return slog.New(slog.NewJSONHandler(w, &slog.HandlerOptions{Level: slog.LevelInfo}))
}
