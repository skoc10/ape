package transport

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/prometheus/client_golang/prometheus/testutil"

	"github.com/skoc10/ape/agent/internal/protocol"
	"github.com/skoc10/ape/agent/internal/telemetry"
)

// gateway is a minimal stand-in for the Control Plane.
type gateway struct {
	t           *testing.T
	connections atomic.Int32
	pings       atomic.Int32
	// behaviour of the n-th connection (1-based); nil = serve normally.
	script func(n int32, conn *websocket.Conn, hello protocol.HelloPayload) (handled bool)

	mu       sync.Mutex
	auth     []string
	lastPing protocol.PingPayload
}

func (g *gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	conn, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	defer func() { _ = conn.CloseNow() }()
	n := g.connections.Add(1)
	g.mu.Lock()
	g.auth = append(g.auth, r.Header.Get("Authorization"))
	g.mu.Unlock()
	ctx := r.Context()

	_, data, err := conn.Read(ctx)
	if err != nil {
		return
	}
	frame, err := protocol.Decode(data)
	var hello protocol.HelloPayload
	if err != nil || frame.Type != protocol.MessageTypeHello || frame.DecodePayload(&hello) != nil {
		g.t.Errorf("first message was not a valid hello")
		return
	}
	if g.script != nil && g.script(n, conn, hello) {
		return
	}
	g.send(ctx, conn, protocol.MessageTypeHelloAck, protocol.HelloAckPayload{
		SessionId: "s", HeartbeatIntervalSeconds: 30,
		Limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 1 << 20, MaxFrameBytes: protocol.MaxFrameBytes},
	})
	for {
		_, data, err := conn.Read(ctx)
		if err != nil {
			return
		}
		frame, err := protocol.Decode(data)
		if err != nil {
			g.t.Errorf("invalid frame from agent: %v", err)
			return
		}
		if frame.Type == protocol.MessageTypePing {
			var ping protocol.PingPayload
			_ = frame.DecodePayload(&ping)
			g.mu.Lock()
			g.lastPing = ping
			g.mu.Unlock()
			g.pings.Add(1)
			g.send(ctx, conn, protocol.MessageTypePong, protocol.PongPayload{})
		}
	}
}

func (g *gateway) send(ctx context.Context, conn *websocket.Conn, t protocol.MessageType, payload any) {
	data, err := protocol.Encode(t, payload)
	if err != nil {
		g.t.Errorf("encode: %v", err)
		return
	}
	_ = conn.Write(ctx, websocket.MessageText, data)
}

func startClient(t *testing.T, g *gateway) (*Client, func()) {
	t.Helper()
	server := httptest.NewServer(g)
	client := &Client{
		Endpoint: "ws" + strings.TrimPrefix(server.URL, "http"),
		Token:    "ape_testtoken",
		Hello: func(context.Context) protocol.HelloPayload {
			return protocol.HelloPayload{AgentVersion: "test", K8SVersion: "v1.31.0", NodeCount: 1, Capabilities: []string{}}
		},
		Metrics:           telemetry.NewMetrics(),
		Logger:            slog.New(slog.NewJSONHandler(io.Discard, nil)),
		Backoff:           Backoff{Base: 5 * time.Millisecond, Max: 20 * time.Millisecond},
		heartbeatOverride: 20 * time.Millisecond,
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = client.Run(ctx)
	}()
	return client, func() {
		cancel()
		<-done
		server.Close()
	}
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for: %s", what)
}

func TestConnectsWithBearerTokenAndHeartbeats(t *testing.T) {
	g := &gateway{t: t}
	client, stop := startClient(t, g)
	defer stop()

	eventually(t, "three pings", func() bool { return g.pings.Load() >= 3 })
	g.mu.Lock()
	auth, ping := g.auth[0], g.lastPing
	g.mu.Unlock()
	if auth != "Bearer ape_testtoken" {
		t.Fatalf("Authorization = %q", auth)
	}
	if ping.Health == nil || ping.Health.ErrorCount == nil {
		t.Fatal("ping carries no health report")
	}
	if got := testutil.ToFloat64(client.Metrics.Connected); got != 1 {
		t.Fatalf("ape_agent_connected = %v, want 1", got)
	}
	if g.connections.Load() != 1 {
		t.Fatalf("connections = %d, want 1", g.connections.Load())
	}
}

func TestReconnectsAfterTheGatewayDropsTheConnection(t *testing.T) {
	g := &gateway{t: t}
	g.script = func(n int32, conn *websocket.Conn, _ protocol.HelloPayload) bool {
		if n <= 2 { // simulate a gateway restart: the first two sessions die right away
			_ = conn.Close(websocket.StatusGoingAway, "restarting")
			return true
		}
		return false
	}
	client, stop := startClient(t, g)
	defer stop()

	eventually(t, "a healthy third session", func() bool { return g.connections.Load() == 3 && g.pings.Load() >= 1 })
	if got := testutil.ToFloat64(client.Metrics.Reconnects); got != 2 {
		t.Fatalf("ape_agent_reconnects_total = %v, want 2", got)
	}
	if got := testutil.ToFloat64(client.Metrics.Connected); got != 1 {
		t.Fatalf("ape_agent_connected = %v, want 1", got)
	}
}

func TestReconnectsWhenPongsStop(t *testing.T) {
	g := &gateway{t: t}
	g.script = func(n int32, conn *websocket.Conn, _ protocol.HelloPayload) bool {
		if n != 1 {
			return false
		}
		// First session: acknowledge, then go silent without closing.
		g.send(context.Background(), conn, protocol.MessageTypeHelloAck, protocol.HelloAckPayload{
			SessionId: "silent", HeartbeatIntervalSeconds: 30,
			Limits: protocol.CollectLimits{MaxPods: 1, MaxCompressedBytes: 1, MaxFrameBytes: 1},
		})
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		for {
			if _, _, err := conn.Read(ctx); err != nil {
				return true
			}
		}
	}
	_, stop := startClient(t, g)
	defer stop()
	eventually(t, "reconnect after silence", func() bool { return g.connections.Load() >= 2 })
}

func TestRejectedTokenBacksOffToTheMaximum(t *testing.T) {
	g := &gateway{t: t}
	g.script = func(_ int32, conn *websocket.Conn, _ protocol.HelloPayload) bool {
		_ = conn.Close(4401, "unauthorized")
		return true
	}
	server := httptest.NewServer(g)
	defer server.Close()

	var delays []time.Duration
	ctx, cancel := context.WithCancel(context.Background())
	client := &Client{
		Endpoint: "ws" + strings.TrimPrefix(server.URL, "http"),
		Token:    "ape_revoked",
		Hello:    func(context.Context) protocol.HelloPayload { return protocol.HelloPayload{Capabilities: []string{}} },
		Metrics:  telemetry.NewMetrics(),
		Logger:   slog.New(slog.NewJSONHandler(io.Discard, nil)),
		sleep: func(_ context.Context, d time.Duration) error {
			delays = append(delays, d)
			if len(delays) == 2 {
				cancel()
				return context.Canceled
			}
			return nil
		},
	}
	_ = client.Run(ctx)
	if len(delays) != 2 {
		t.Fatalf("expected 2 sleeps, got %d", len(delays))
	}
	for _, d := range delays {
		if d < 30*time.Second {
			t.Fatalf("retry after a rejected token in %s, want >= 30s", d)
		}
	}
}

func TestCollectRequestsRunOneAtATime(t *testing.T) {
	g := &gateway{t: t}
	release := make(chan struct{})
	var started atomic.Int32
	g.script = func(_ int32, conn *websocket.Conn, _ protocol.HelloPayload) bool {
		ctx := context.Background()
		g.send(ctx, conn, protocol.MessageTypeHelloAck, protocol.HelloAckPayload{
			SessionId: "s", HeartbeatIntervalSeconds: 30,
			Limits: protocol.CollectLimits{MaxPods: 7, MaxCompressedBytes: 1, MaxFrameBytes: 1},
		})
		req := protocol.CollectRequestPayload{RunId: "run-1", Resources: []string{"Pod"}, ExcludeNamespaces: []string{}}
		g.send(ctx, conn, protocol.MessageTypeCollectRequest, req)
		req.RunId = "run-2"
		g.send(ctx, conn, protocol.MessageTypeCollectRequest, req)
		for {
			_, data, err := conn.Read(ctx)
			if err != nil {
				return true
			}
			frame, _ := protocol.Decode(data)
			if frame.Type == protocol.MessageTypeError {
				var e protocol.ErrorPayload
				_ = frame.DecodePayload(&e)
				if e.RunId != nil && *e.RunId == "run-2" {
					close(release)
				}
			}
		}
	}
	server := httptest.NewServer(g)
	defer server.Close()
	var limits atomic.Int32
	client := &Client{
		Endpoint: "ws" + strings.TrimPrefix(server.URL, "http"),
		Token:    "ape_t",
		Hello:    func(context.Context) protocol.HelloPayload { return protocol.HelloPayload{Capabilities: []string{}} },
		Metrics:  telemetry.NewMetrics(),
		Logger:   slog.New(slog.NewJSONHandler(io.Discard, nil)),
		OnCollect: func(_ context.Context, s Sender, _ protocol.CollectRequestPayload) {
			started.Add(1)
			limits.Store(int32(s.Limits().MaxPods)) //nolint:gosec // test value
			<-release
		},
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); _ = client.Run(ctx) }()
	defer func() { cancel(); <-done }()

	select {
	case <-release:
	case <-time.After(5 * time.Second):
		t.Fatal("second collect_request was not rejected")
	}
	if started.Load() != 1 || limits.Load() != 7 {
		t.Fatalf("started=%d limits=%d, want 1 and 7", started.Load(), limits.Load())
	}
}
