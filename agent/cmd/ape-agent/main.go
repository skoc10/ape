// Command ape-agent is the read-only Data Plane agent that runs inside a
// customer cluster and connects outbound to the Control Plane.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/skoc10/ape/agent/internal/config"
	"github.com/skoc10/ape/agent/internal/protocol"
	"github.com/skoc10/ape/agent/internal/telemetry"
	"github.com/skoc10/ape/agent/internal/transport"
)

// version is overridden at build time with -ldflags "-X main.version=...".
var version = "dev"

func main() {
	logger := telemetry.NewLogger(os.Stdout)
	slog.SetDefault(logger)
	if err := run(logger); err != nil && !errors.Is(err, context.Canceled) {
		logger.Error("ape-agent stopped", "error", err.Error())
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	tracing, err := telemetry.NewTracerProvider(ctx, version, cfg.OTLPEndpoint)
	if err != nil {
		return err
	}
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = tracing.Shutdown(shutdownCtx)
	}()

	metrics := telemetry.NewMetrics()
	server := &http.Server{Addr: cfg.MetricsAddr, Handler: metrics.Handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("metrics server failed", "error", err.Error())
		}
	}()
	defer func() { _ = server.Close() }()

	// The endpoint is logged, the token never is.
	logger.Info("ape-agent starting", "version", version, "endpoint", cfg.Endpoint, "metrics_addr", cfg.MetricsAddr)
	client := &transport.Client{
		Endpoint: cfg.Endpoint,
		Token:    cfg.Token,
		Hello: func(context.Context) protocol.HelloPayload {
			// Cluster facts and collectors arrive with task 2.4.
			return protocol.HelloPayload{AgentVersion: version, K8SVersion: "unknown", NodeCount: 0, Capabilities: []string{}}
		},
		Metrics: metrics,
		Logger:  logger,
		Tracer:  tracing.Tracer("ape-agent"),
	}
	return client.Run(ctx)
}
