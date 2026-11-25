package transport

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/coder/websocket"
	"go.opentelemetry.io/otel/trace"
	"go.opentelemetry.io/otel/trace/noop"

	"github.com/skoc10/ape/agent/internal/protocol"
	"github.com/skoc10/ape/agent/internal/telemetry"
)

// Close codes used by the gateway (see apps/api/ape/gateway/sessions.py).
const (
	closeUnauthorized websocket.StatusCode = 4401
	closeReplaced     websocket.StatusCode = 4409
)

const (
	helloTimeout     = 10 * time.Second
	writeTimeout     = 10 * time.Second
	missedHeartbeats = 3
)

// Sender sends protocol messages on the current session.
type Sender interface {
	Send(ctx context.Context, t protocol.MessageType, payload any) error
	// Limits are the collection limits announced by the Control Plane in hello_ack.
	Limits() protocol.CollectLimits
}

// Handler reacts to a collect_request. It runs on its own goroutine, one at a time.
type Handler func(ctx context.Context, s Sender, req protocol.CollectRequestPayload)

// Client maintains the session with the Control Plane.
type Client struct {
	Endpoint string
	Token    string
	// Hello builds the hello payload for each new session.
	Hello func(ctx context.Context) protocol.HelloPayload
	// OnCollect handles collect_request; nil ignores them.
	OnCollect Handler
	Metrics   *telemetry.Metrics
	Logger    *slog.Logger
	Tracer    trace.Tracer
	Backoff   Backoff

	// heartbeatOverride replaces the interval from hello_ack (tests only).
	heartbeatOverride time.Duration
	// sleep is replaceable in tests.
	sleep func(ctx context.Context, d time.Duration) error
}

// Run connects and reconnects until ctx is cancelled.
func (c *Client) Run(ctx context.Context) error {
	if c.Tracer == nil {
		c.Tracer = noop.NewTracerProvider().Tracer("transport")
	}
	if c.sleep == nil {
		c.sleep = sleepCtx
	}
	if c.Backoff.Base == 0 {
		c.Backoff = DefaultBackoff()
	}
	first := true
	for {
		if !first {
			c.Metrics.Reconnects.Inc()
		}
		first = false

		established, err := c.session(ctx)
		c.Metrics.Connected.Set(0)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		c.Metrics.RecordError()

		switch status := websocket.CloseStatus(err); {
		case status == closeUnauthorized:
			// A rejected token will not start working by itself: retry slowly.
			c.Logger.ErrorContext(ctx, "token rejected by the control plane; check that it was not revoked")
			c.Backoff.Saturate()
		case status == closeReplaced:
			c.Logger.WarnContext(ctx, "another agent connected for this cluster with the same token")
			c.Backoff.Saturate()
		case established:
			c.Backoff.Reset()
		}
		delay := c.Backoff.Next()
		c.Logger.WarnContext(ctx, "session ended, reconnecting",
			"error", err.Error(), "retry_in_ms", delay.Milliseconds())
		if err := c.sleep(ctx, delay); err != nil {
			return err
		}
	}
}

// session runs one connection. established reports whether hello_ack was received.
func (c *Client) session(ctx context.Context) (established bool, err error) {
	ctx, span := c.Tracer.Start(ctx, "agent.session")
	defer span.End()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	dialCtx, dialCancel := context.WithTimeout(ctx, helloTimeout)
	defer dialCancel()
	conn, resp, err := websocket.Dial(dialCtx, c.Endpoint, &websocket.DialOptions{
		HTTPHeader: http.Header{"Authorization": {"Bearer " + c.Token}},
		HTTPClient: &http.Client{Transport: &http.Transport{
			Proxy:           http.ProxyFromEnvironment,
			TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS13},
		}},
	})
	if resp != nil && resp.Body != nil {
		_ = resp.Body.Close()
	}
	if err != nil {
		return false, fmt.Errorf("dial: %w", err)
	}
	defer func() { _ = conn.CloseNow() }()
	conn.SetReadLimit(protocol.MaxFrameBytes)

	s := &session{conn: conn}
	if err := s.Send(ctx, protocol.MessageTypeHello, c.Hello(ctx)); err != nil {
		return false, err
	}
	helloCtx, helloCancel := context.WithTimeout(ctx, helloTimeout)
	frame, err := s.read(helloCtx)
	helloCancel()
	if err != nil {
		return false, fmt.Errorf("waiting for hello_ack: %w", err)
	}
	var ack protocol.HelloAckPayload
	if frame.Type != protocol.MessageTypeHelloAck || frame.DecodePayload(&ack) != nil {
		return false, errors.New("expected hello_ack")
	}
	s.limits = ack.Limits
	interval := time.Duration(ack.HeartbeatIntervalSeconds) * time.Second
	if c.heartbeatOverride > 0 {
		interval = c.heartbeatOverride
	}
	c.Metrics.Connected.Set(1)
	c.Logger.InfoContext(ctx, "connected to control plane",
		"session_id", ack.SessionId, "heartbeat_ms", interval.Milliseconds())

	s.touch()
	errs := make(chan error, 2)
	go func() { errs <- c.heartbeat(ctx, s, interval) }()
	go func() { errs <- c.readLoop(ctx, s) }()
	err = <-errs
	cancel()
	s.collects.Wait()
	return true, err
}

func (c *Client) heartbeat(ctx context.Context, s *session, interval time.Duration) error {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if time.Since(s.lastSeen()) > missedHeartbeats*interval {
				return fmt.Errorf("no message from the control plane for %s", missedHeartbeats*interval)
			}
			var ping protocol.PingPayload
			lastCollect, errorCount := c.Metrics.Health()
			ping.Health = &protocol.AgentHealth{LastCollectSeconds: lastCollect, ErrorCount: &errorCount}
			if err := s.Send(ctx, protocol.MessageTypePing, ping); err != nil {
				return err
			}
		}
	}
}

func (c *Client) readLoop(ctx context.Context, s *session) error {
	for {
		frame, err := s.read(ctx)
		if err != nil {
			return err
		}
		s.touch()
		switch frame.Type {
		case protocol.MessageTypePong:
		case protocol.MessageTypePing:
			if err := s.Send(ctx, protocol.MessageTypePong, protocol.PongPayload{}); err != nil {
				return err
			}
		case protocol.MessageTypeCollectRequest:
			var req protocol.CollectRequestPayload
			if err := frame.DecodePayload(&req); err != nil {
				return err
			}
			c.startCollect(ctx, s, req)
		default:
			// The Control Plane has no other commands. Anything else is ignored, never executed.
			c.Logger.WarnContext(ctx, "ignoring unexpected message", "type", string(frame.Type))
		}
	}
}

func (c *Client) startCollect(ctx context.Context, s *session, req protocol.CollectRequestPayload) {
	if c.OnCollect == nil {
		return
	}
	if !s.collecting.TryLock() {
		c.Logger.WarnContext(ctx, "collection already running, rejecting request", "run_id", req.RunId)
		busy := protocol.ErrorPayload{RunId: &req.RunId, Code: protocol.ErrorCodeBadRequest,
			Message: "a collection is already running"}
		_ = s.Send(ctx, protocol.MessageTypeError, busy)
		return
	}
	s.collects.Add(1)
	go func() {
		defer s.collects.Done()
		defer s.collecting.Unlock()
		c.OnCollect(ctx, s, req)
	}()
}

type session struct {
	conn   *websocket.Conn
	limits protocol.CollectLimits

	writeMu    sync.Mutex
	collecting sync.Mutex
	collects   sync.WaitGroup

	seenMu sync.Mutex
	seen   time.Time
}

func (s *session) Limits() protocol.CollectLimits { return s.limits }

func (s *session) Send(ctx context.Context, t protocol.MessageType, payload any) error {
	data, err := protocol.Encode(t, payload)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, writeTimeout)
	defer cancel()
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	if err := s.conn.Write(ctx, websocket.MessageText, data); err != nil {
		return fmt.Errorf("send %s: %w", t, err)
	}
	return nil
}

func (s *session) read(ctx context.Context) (protocol.Frame, error) {
	kind, data, err := s.conn.Read(ctx)
	if err != nil {
		return protocol.Frame{}, fmt.Errorf("read: %w", err)
	}
	if kind != websocket.MessageText {
		return protocol.Frame{}, errors.New("read: unexpected binary frame")
	}
	return protocol.Decode(data)
}

func (s *session) touch() {
	s.seenMu.Lock()
	s.seen = time.Now()
	s.seenMu.Unlock()
}

func (s *session) lastSeen() time.Time {
	s.seenMu.Lock()
	defer s.seenMu.Unlock()
	return s.seen
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
