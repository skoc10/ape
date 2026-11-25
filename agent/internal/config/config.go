// Package config reads the agent configuration from environment variables.
package config

import (
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
)

// Config is everything the agent needs to start.
type Config struct {
	// Endpoint is the Control Plane gateway, e.g. wss://cp.example.com/v1/agent.
	Endpoint string
	// Token authenticates this agent. It is never logged.
	Token string
	// MetricsAddr is where /metrics and /healthz listen. Loopback by default:
	// nothing outside the pod can reach it unless the chart opts in.
	MetricsAddr string
	// OTLPEndpoint enables trace export when set.
	OTLPEndpoint string
}

// Load reads and validates the configuration.
func Load() (Config, error) {
	cfg := Config{
		Endpoint:     os.Getenv("APE_ENDPOINT"),
		Token:        os.Getenv("APE_TOKEN"),
		MetricsAddr:  envOr("APE_METRICS_ADDR", "127.0.0.1:9090"),
		OTLPEndpoint: os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
	}
	if path := os.Getenv("APE_TOKEN_FILE"); cfg.Token == "" && path != "" {
		data, err := os.ReadFile(path) //nolint:gosec // operator-provided path to the mounted Secret
		if err != nil {
			return cfg, fmt.Errorf("read token file: %w", err)
		}
		cfg.Token = strings.TrimSpace(string(data))
	}
	if cfg.Token == "" {
		return cfg, errors.New("APE_TOKEN or APE_TOKEN_FILE is required")
	}
	insecure := os.Getenv("APE_ALLOW_INSECURE") == "true"
	if err := ValidateEndpoint(cfg.Endpoint, insecure); err != nil {
		return cfg, err
	}
	return cfg, nil
}

// ValidateEndpoint accepts wss:// URLs. Plain ws:// sends the token in clear
// text, so it needs an explicit opt-in that is only meant for local development.
func ValidateEndpoint(endpoint string, allowInsecure bool) error {
	u, err := url.Parse(endpoint)
	if err != nil || u.Host == "" {
		return errors.New("APE_ENDPOINT must be a URL like wss://cp.example.com/v1/agent")
	}
	switch u.Scheme {
	case "wss":
		return nil
	case "ws":
		if allowInsecure {
			return nil
		}
		return errors.New("APE_ENDPOINT must use wss:// (set APE_ALLOW_INSECURE=true only for local development)")
	default:
		return fmt.Errorf("APE_ENDPOINT has unsupported scheme %q", u.Scheme)
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
