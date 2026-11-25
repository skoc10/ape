package telemetry

import (
	"net/http"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Metrics are the agent's Prometheus metrics plus the health numbers that are
// piggybacked on every ping, so the Control Plane sees them without scraping.
type Metrics struct {
	Registry       *prometheus.Registry
	Connected      prometheus.Gauge
	Reconnects     prometheus.Counter
	CollectSeconds *prometheus.HistogramVec
	Objects        *prometheus.CounterVec

	errorCount         atomic.Int64
	lastCollectSeconds atomic.Int64 // milliseconds; -1 = never
}

// NewMetrics registers all metrics on a private registry.
func NewMetrics() *Metrics {
	m := &Metrics{
		Registry: prometheus.NewRegistry(),
		Connected: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "ape_agent_connected", Help: "1 while the agent has a live session with the Control Plane.",
		}),
		Reconnects: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "ape_agent_reconnects_total", Help: "Connection attempts after the first one.",
		}),
		CollectSeconds: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "ape_agent_collect_seconds", Help: "Time spent listing one resource kind.",
			Buckets: prometheus.DefBuckets,
		}, []string{"kind"}),
		Objects: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "ape_agent_objects_total", Help: "Objects sent to the Control Plane by kind.",
		}, []string{"kind"}),
	}
	m.lastCollectSeconds.Store(-1)
	m.Registry.MustRegister(m.Connected, m.Reconnects, m.CollectSeconds, m.Objects)
	return m
}

// RecordError counts an error for the health report.
func (m *Metrics) RecordError() { m.errorCount.Add(1) }

// RecordCollect stores the duration of the last complete collection.
func (m *Metrics) RecordCollect(d time.Duration) { m.lastCollectSeconds.Store(d.Milliseconds()) }

// Health returns the last collection duration in seconds (nil if none yet) and the error count.
func (m *Metrics) Health() (lastCollectSeconds *float64, errorCount int) {
	if ms := m.lastCollectSeconds.Load(); ms >= 0 {
		seconds := float64(ms) / 1000
		lastCollectSeconds = &seconds
	}
	return lastCollectSeconds, int(m.errorCount.Load())
}

// Handler serves /metrics and /healthz.
func (m *Metrics) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(m.Registry, promhttp.HandlerOpts{}))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	return mux
}
