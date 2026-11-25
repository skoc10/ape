package collect

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/dynamic"

	"github.com/skoc10/ape/agent/internal/protocol"
	"github.com/skoc10/ape/agent/internal/telemetry"
	"github.com/skoc10/ape/agent/internal/transport"
)

const (
	listPageSize   = 500
	collectTimeout = 5 * time.Minute
)

// Collector answers collect_request messages with read-only list calls.
type Collector struct {
	Dynamic   dynamic.Interface
	Discovery discovery.DiscoveryInterface
	Metrics   *telemetry.Metrics
	Logger    *slog.Logger
	Version   string
}

// Hello describes this agent and cluster for the hello message.
func (c *Collector) Hello(ctx context.Context) protocol.HelloPayload {
	hello := protocol.HelloPayload{AgentVersion: c.Version, K8SVersion: "unknown", Capabilities: []string{}}
	if info, err := c.Discovery.ServerVersion(); err == nil {
		hello.K8SVersion = info.GitVersion
	} else {
		c.Logger.WarnContext(ctx, "cannot read the cluster version", "error", err.Error())
	}
	nodes, err := c.Dynamic.Resource(gvr("", "v1", "nodes")).List(ctx, metav1.ListOptions{})
	if err == nil {
		hello.NodeCount = len(nodes.Items)
	}
	if c.hasMetricsAPI() {
		hello.Capabilities = append(hello.Capabilities, "metrics")
	}
	return hello
}

func (c *Collector) hasMetricsAPI() bool {
	_, err := c.Discovery.ServerResourcesForGroupVersion(MetricsGroupVersion)
	return err == nil
}

// Handle runs one collection and streams the result. It implements transport.Handler.
func (c *Collector) Handle(ctx context.Context, s transport.Sender, req protocol.CollectRequestPayload) {
	ctx, cancel := context.WithTimeout(ctx, collectTimeout)
	defer cancel()
	start := time.Now()
	log := c.Logger.With("run_id", req.RunId)

	done, err := c.collect(ctx, s, req)
	if err != nil {
		c.Metrics.RecordError()
		code := errorCode(err)
		// Only the kind and the error class are reported, never object content.
		log.ErrorContext(ctx, "collection failed", "code", string(code), "error", err.Error())
		runID := req.RunId
		_ = s.Send(context.WithoutCancel(ctx), protocol.MessageTypeError, protocol.ErrorPayload{
			RunId: &runID, Code: code, Message: truncate(err.Error(), 500),
		})
		return
	}
	if err := s.Send(ctx, protocol.MessageTypeCollectDone, done); err != nil {
		c.Metrics.RecordError()
		log.ErrorContext(ctx, "could not send collect_done", "error", err.Error())
		return
	}
	c.Metrics.RecordCollect(time.Since(start))
	log.InfoContext(ctx, "collection finished",
		"chunks", done.TotalChunks, "truncated", done.Truncated, "duration_ms", time.Since(start).Milliseconds())
}

func (c *Collector) collect(ctx context.Context, s transport.Sender, req protocol.CollectRequestPayload) (protocol.CollectDonePayload, error) {
	limits := s.Limits()
	done := protocol.CollectDonePayload{RunId: req.RunId, Counts: map[string]int{}}
	digest := sha256.New()
	sent := 0
	metricsAPI := c.hasMetricsAPI()

	for _, res := range Resources {
		if len(req.Resources) > 0 && !slices.Contains(req.Resources, res.Kind) {
			continue
		}
		if res.Optional && !metricsAPI {
			continue
		}
		started := time.Now()
		objects, truncated, err := c.list(ctx, res, req.ExcludeNamespaces, limits)
		c.Metrics.CollectSeconds.WithLabelValues(res.Kind).Observe(time.Since(started).Seconds())
		if err != nil {
			if res.Optional {
				continue // usage data is a bonus, never a reason to fail the audit
			}
			return done, fmt.Errorf("list %s: %w", res.Kind, err)
		}
		done.Truncated = done.Truncated || truncated

		chunks, skipped, err := pack(objects, maxChunkGzipBytes)
		if err != nil {
			return done, fmt.Errorf("pack %s: %w", res.Kind, err)
		}
		if skipped > 0 {
			done.Truncated = true
		}
		done.Counts[res.Kind] = 0
		for _, ch := range chunks {
			if limits.MaxCompressedBytes > 0 && sent+len(ch.gzip) > limits.MaxCompressedBytes {
				done.Truncated = true
				return finish(done, digest.Sum(nil)), nil
			}
			payload := protocol.CollectChunkPayload{
				RunId: req.RunId, Seq: done.TotalChunks, Kind: res.Kind, Count: ch.count, Data: ch.base64(),
			}
			if err := s.Send(ctx, protocol.MessageTypeCollectChunk, payload); err != nil {
				return done, err
			}
			digest.Write(ch.gzip)
			sent += len(ch.gzip)
			done.TotalChunks++
			done.Counts[res.Kind] += ch.count
			c.Metrics.Objects.WithLabelValues(res.Kind).Add(float64(ch.count))
		}
	}
	return finish(done, digest.Sum(nil)), nil
}

func finish(done protocol.CollectDonePayload, sum []byte) protocol.CollectDonePayload {
	done.Sha256 = hex.EncodeToString(sum)
	return done
}

// list returns the sanitized, JSON-encoded objects of one kind.
func (c *Collector) list(ctx context.Context, res Resource, excludeNamespaces []string, limits protocol.CollectLimits) (objects [][]byte, truncated bool, err error) {
	opts := metav1.ListOptions{Limit: listPageSize}
	for {
		page, err := c.Dynamic.Resource(res.GVR).List(ctx, opts)
		if err != nil {
			return nil, false, err
		}
		for i := range page.Items {
			obj := page.Items[i].Object
			namespace := page.Items[i].GetNamespace()
			if res.Kind == "Namespace" {
				namespace = page.Items[i].GetName()
			}
			if namespace != "" && slices.Contains(excludeNamespaces, namespace) {
				continue
			}
			if res.Kind == "Pod" && limits.MaxPods > 0 && len(objects) >= limits.MaxPods {
				return objects, true, nil
			}
			Sanitize(obj)
			// List items come without kind/apiVersion; the Control Plane needs them.
			obj["kind"] = res.Kind
			obj["apiVersion"] = res.GVR.GroupVersion().String()
			encoded, err := json.Marshal(obj)
			if err != nil {
				return nil, false, fmt.Errorf("encode %s: %w", res.Kind, err)
			}
			objects = append(objects, encoded)
		}
		if page.GetContinue() == "" {
			return objects, false, nil
		}
		opts.Continue = page.GetContinue()
	}
}

func errorCode(err error) protocol.ErrorCode {
	switch {
	case apierrors.IsForbidden(err), apierrors.IsUnauthorized(err):
		return protocol.ErrorCodeForbidden
	case errors.Is(err, context.DeadlineExceeded), apierrors.IsTimeout(err), apierrors.IsServerTimeout(err):
		return protocol.ErrorCodeTimeout
	default:
		return protocol.ErrorCodeInternal
	}
}

func truncate(s string, limit int) string {
	if len(s) <= limit {
		return s
	}
	return s[:limit]
}
