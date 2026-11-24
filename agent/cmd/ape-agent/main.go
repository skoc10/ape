// Command ape-agent is the read-only Data Plane agent that runs inside a
// customer cluster and connects outbound to the Control Plane.
package main

import (
	"log/slog"
	"os"

	"github.com/skoc10/ape/agent/internal/telemetry"
)

// version is overridden at build time with -ldflags "-X main.version=...".
var version = "dev"

func main() {
	logger := telemetry.NewLogger(os.Stdout)
	slog.SetDefault(logger)
	logger.Info("ape-agent starting", "version", version)
}
