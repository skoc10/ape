// Command ape-agent is the read-only Data Plane agent that runs inside a
// customer cluster and connects outbound to the Control Plane.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"k8s.io/client-go/discovery"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"

	"github.com/skoc10/ape/agent/internal/collect"
	"github.com/skoc10/ape/agent/internal/config"
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
	kube, err := rest.InClusterConfig()
	if err != nil {
		return fmt.Errorf("in-cluster config: %w", err)
	}
	kube.UserAgent = "ape-agent/" + version
	dyn, err := dynamic.NewForConfig(kube)
	if err != nil {
		return fmt.Errorf("kubernetes client: %w", err)
	}
	disco, err := discovery.NewDiscoveryClientForConfig(kube)
	if err != nil {
		return fmt.Errorf("discovery client: %w", err)
	}
	collector := &collect.Collector{Dynamic: dyn, Discovery: disco, Metrics: metrics, Logger: logger, Version: version}

	client := &transport.Client{
		Endpoint:  cfg.Endpoint,
		Token:     cfg.Token,
		Hello:     collector.Hello,
		OnCollect: collector.Handle,
		Metrics:   metrics,
		Logger:    logger,
		Tracer:    tracing.Tracer("ape-agent"),
	}
	return client.Run(ctx)
}
