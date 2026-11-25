package telemetry

import (
	"context"
	"fmt"
	"strings"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// NewTracerProvider always creates spans (so logs carry trace ids) and exports
// them over OTLP/HTTP only when an endpoint is configured.
func NewTracerProvider(ctx context.Context, version, otlpEndpoint string) (*sdktrace.TracerProvider, error) {
	res := resource.NewSchemaless(
		attribute.String("service.name", "ape-agent"),
		attribute.String("service.version", version),
	)
	opts := []sdktrace.TracerProviderOption{sdktrace.WithResource(res)}
	if otlpEndpoint != "" {
		exporter, err := otlptracehttp.New(ctx,
			otlptracehttp.WithEndpointURL(strings.TrimRight(otlpEndpoint, "/")+"/v1/traces"))
		if err != nil {
			return nil, fmt.Errorf("otlp exporter: %w", err)
		}
		opts = append(opts, sdktrace.WithBatcher(exporter))
	}
	return sdktrace.NewTracerProvider(opts...), nil
}
