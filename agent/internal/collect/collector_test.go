package collect

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"sync"
	"testing"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/version"
	fakediscovery "k8s.io/client-go/discovery/fake"
	dynamicfake "k8s.io/client-go/dynamic/fake"
	clienttesting "k8s.io/client-go/testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"github.com/skoc10/ape/agent/internal/protocol"
	"github.com/skoc10/ape/agent/internal/telemetry"
)

type recorder struct {
	mu     sync.Mutex
	limits protocol.CollectLimits
	chunks []protocol.CollectChunkPayload
	done   *protocol.CollectDonePayload
	errs   []protocol.ErrorPayload
}

func (r *recorder) Limits() protocol.CollectLimits { return r.limits }

func (r *recorder) Send(_ context.Context, t protocol.MessageType, payload any) error {
	if _, err := protocol.Encode(t, payload); err != nil {
		return err // every message must fit a frame
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	switch p := payload.(type) {
	case protocol.CollectChunkPayload:
		r.chunks = append(r.chunks, p)
	case protocol.CollectDonePayload:
		r.done = &p
	case protocol.ErrorPayload:
		r.errs = append(r.errs, p)
	}
	return nil
}

func (r *recorder) objects(t *testing.T) map[string][]map[string]any {
	t.Helper()
	out := map[string][]map[string]any{}
	for _, ch := range r.chunks {
		raw, err := base64.StdEncoding.DecodeString(ch.Data)
		if err != nil {
			t.Fatal(err)
		}
		zr, err := gzip.NewReader(bytes.NewReader(raw))
		if err != nil {
			t.Fatal(err)
		}
		plain, _ := io.ReadAll(zr)
		var items []map[string]any
		if err := json.Unmarshal(plain, &items); err != nil {
			t.Fatal(err)
		}
		if len(items) != ch.Count {
			t.Fatalf("chunk %d: count %d but %d objects", ch.Seq, ch.Count, len(items))
		}
		out[ch.Kind] = append(out[ch.Kind], items...)
	}
	return out
}

func object(apiVersion, kind, namespace, name string, spec map[string]any) *unstructured.Unstructured {
	meta := map[string]any{
		"name":          name,
		"managedFields": []any{map[string]any{"manager": "kubectl", "fieldsV1": "managed-fields-leak"}},
	}
	if namespace != "" {
		meta["namespace"] = namespace
	}
	obj := map[string]any{"apiVersion": apiVersion, "kind": kind, "metadata": meta}
	if spec != nil {
		obj["spec"] = spec
	}
	return &unstructured.Unstructured{Object: obj}
}

func newCollector(t *testing.T, withMetrics bool, objects ...runtime.Object) (*Collector, *dynamicfake.FakeDynamicClient) {
	t.Helper()
	listKinds := map[schema.GroupVersionResource]string{}
	for _, res := range Resources {
		listKinds[res.GVR] = res.Kind + "List"
	}
	client := dynamicfake.NewSimpleDynamicClientWithCustomListKinds(runtime.NewScheme(), listKinds, objects...)
	disco := &fakediscovery.FakeDiscovery{Fake: &clienttesting.Fake{}}
	disco.FakedServerVersion = &version.Info{GitVersion: "v1.31.2"}
	if withMetrics {
		disco.Resources = []*metav1.APIResourceList{{GroupVersion: MetricsGroupVersion}}
	}
	return &Collector{
		Dynamic:   client,
		Discovery: disco,
		Metrics:   telemetry.NewMetrics(),
		Logger:    slog.New(slog.NewJSONHandler(io.Discard, nil)),
		Version:   "test",
	}, client
}

func cluster() []runtime.Object {
	podSpec := map[string]any{"containers": []any{map[string]any{
		"name": "app", "image": "nginx:latest",
		"env": []any{map[string]any{"name": "PASSWORD", "value": "hunter2-env-literal"}},
	}}}
	return []runtime.Object{
		object("v1", "Pod", "prod", "web-1", podSpec),
		object("v1", "Pod", "prod", "web-2", podSpec),
		object("v1", "Pod", "kube-system", "coredns", podSpec),
		object("apps/v1", "Deployment", "prod", "web", map[string]any{"template": map[string]any{"spec": podSpec}}),
		object("v1", "Namespace", "", "prod", nil),
		object("v1", "Namespace", "", "kube-system", nil),
		object("v1", "Node", "", "node-1", nil),
		object("rbac.authorization.k8s.io/v1", "ClusterRole", "", "admin", nil),
		object("v1", "Service", "prod", "web", nil),
	}
}

const runID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

func TestCollectStreamsSanitizedWholeObjects(t *testing.T) {
	c, _ := newCollector(t, false, cluster()...)
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 50 << 20, MaxFrameBytes: 512 << 10}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID, ExcludeNamespaces: []string{"kube-system"}})

	if len(rec.errs) != 0 || rec.done == nil {
		t.Fatalf("errors=%v done=%v", rec.errs, rec.done)
	}
	got := rec.objects(t)
	if len(got["Pod"]) != 2 || len(got["Deployment"]) != 1 || len(got["Namespace"]) != 1 || len(got["Node"]) != 1 {
		t.Fatalf("unexpected objects: pods=%d deployments=%d namespaces=%d nodes=%d",
			len(got["Pod"]), len(got["Deployment"]), len(got["Namespace"]), len(got["Node"]))
	}
	for kind, items := range got {
		for _, item := range items {
			encoded, _ := json.Marshal(item)
			if strings.Contains(string(encoded), "hunter2-env-literal") || strings.Contains(string(encoded), "managedFields") {
				t.Errorf("%s left the cluster unsanitized: %s", kind, encoded)
			}
			if item["kind"] != kind || item["apiVersion"] == nil {
				t.Errorf("%s object lacks kind/apiVersion", kind)
			}
			if meta := item["metadata"].(map[string]any); meta["namespace"] == "kube-system" || meta["name"] == "kube-system" {
				t.Errorf("excluded namespace leaked through %s", kind)
			}
		}
	}

	// collect_done matches what was sent.
	digest := sha256.New()
	for i, ch := range rec.chunks {
		if ch.Seq != i || ch.RunId != runID {
			t.Fatalf("chunk %d has seq %d run %s", i, ch.Seq, ch.RunId)
		}
		raw, _ := base64.StdEncoding.DecodeString(ch.Data)
		digest.Write(raw)
	}
	if rec.done.Sha256 != hex.EncodeToString(digest.Sum(nil)) {
		t.Error("sha256 does not cover the gzip bytes of all chunks in order")
	}
	if rec.done.TotalChunks != len(rec.chunks) || rec.done.Counts["Pod"] != 2 || rec.done.Truncated {
		t.Errorf("collect_done = %+v", *rec.done)
	}
	if _, ok := rec.done.Counts["PodMetrics"]; ok {
		t.Error("metrics kinds must be skipped without metrics-server")
	}
	if lastCollect, _ := c.Metrics.Health(); lastCollect == nil {
		t.Error("last collection duration was not recorded")
	}
}

func TestCollectNeverTouchesSecretsOrConfigMapsAndOnlyReads(t *testing.T) {
	c, client := newCollector(t, true, cluster()...)
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 50 << 20}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID})
	c.Hello(context.Background())

	for _, action := range client.Actions() {
		if verb := action.GetVerb(); verb != "list" {
			t.Errorf("agent used verb %q on %s", verb, action.GetResource().Resource)
		}
		if res := action.GetResource().Resource; res == "secrets" || res == "configmaps" {
			t.Errorf("agent listed %s", res)
		}
	}
	for _, res := range Resources {
		if res.GVR.Resource == "secrets" || res.GVR.Resource == "configmaps" {
			t.Fatalf("%s must not be in the resource table", res.GVR.Resource)
		}
	}
}

func TestCollectHonoursRequestedKindsAndIgnoresUnknownOnes(t *testing.T) {
	c, client := newCollector(t, false, cluster()...)
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 50 << 20}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID, Resources: []string{"Service", "Secret", "exec"}})

	if rec.done == nil || len(rec.done.Counts) != 1 || rec.done.Counts["Service"] != 1 {
		t.Fatalf("collect_done = %+v", rec.done)
	}
	if actions := client.Actions(); len(actions) != 1 || actions[0].GetResource().Resource != "services" {
		t.Fatalf("unexpected API calls: %v", actions)
	}
}

func TestCollectTruncatesAtThePodLimit(t *testing.T) {
	c, _ := newCollector(t, false, cluster()...)
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 1, MaxCompressedBytes: 50 << 20}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID})
	if rec.done == nil || !rec.done.Truncated || rec.done.Counts["Pod"] != 1 {
		t.Fatalf("collect_done = %+v", rec.done)
	}
}

func TestCollectStopsAtTheByteLimit(t *testing.T) {
	c, _ := newCollector(t, false, cluster()...)
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 1}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID})
	if rec.done == nil || !rec.done.Truncated || len(rec.chunks) != 0 {
		t.Fatalf("done=%+v chunks=%d", rec.done, len(rec.chunks))
	}
}

func TestCollectWithMetricsServer(t *testing.T) {
	c, client := newCollector(t, true, cluster()...)
	// Created through the client: the fake tracker cannot guess that PodMetrics lives under "pods".
	usage := object("metrics.k8s.io/v1beta1", "PodMetrics", "prod", "web-1", nil)
	if _, err := client.Resource(gvr("metrics.k8s.io", "v1beta1", "pods")).Namespace("prod").
		Create(context.Background(), usage, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 50 << 20}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID})
	if rec.done == nil || rec.done.Counts["PodMetrics"] != 1 {
		t.Fatalf("collect_done = %+v", rec.done)
	}
	hello := c.Hello(context.Background())
	if len(hello.Capabilities) != 1 || hello.Capabilities[0] != "metrics" || hello.K8SVersion != "v1.31.2" || hello.NodeCount != 1 {
		t.Fatalf("hello = %+v", hello)
	}
}

func TestBrokenMetricsAPIDoesNotFailTheCollection(t *testing.T) {
	c, client := newCollector(t, true, cluster()...)
	client.PrependReactor("list", "pods", func(action clienttesting.Action) (bool, runtime.Object, error) {
		if action.GetResource().Group == "metrics.k8s.io" {
			return true, nil, apierrors.NewServiceUnavailable("metrics-server is down")
		}
		return false, nil, nil
	})
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 50 << 20}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID})
	if len(rec.errs) != 0 || rec.done == nil || rec.done.Counts["Pod"] != 3 {
		t.Fatalf("errs=%v done=%+v", rec.errs, rec.done)
	}
}

func TestForbiddenListReportsAnErrorInsteadOfDone(t *testing.T) {
	c, client := newCollector(t, false, cluster()...)
	client.PrependReactor("list", "deployments", func(clienttesting.Action) (bool, runtime.Object, error) {
		return true, nil, apierrors.NewForbidden(schema.GroupResource{Group: "apps", Resource: "deployments"}, "", nil)
	})
	rec := &recorder{limits: protocol.CollectLimits{MaxPods: 5000, MaxCompressedBytes: 50 << 20}}
	c.Handle(context.Background(), rec, protocol.CollectRequestPayload{RunId: runID})

	if rec.done != nil || len(rec.errs) != 1 {
		t.Fatalf("done=%v errs=%v", rec.done, rec.errs)
	}
	if e := rec.errs[0]; e.Code != protocol.ErrorCodeForbidden || e.RunId == nil || *e.RunId != runID {
		t.Fatalf("error payload = %+v", e)
	}
	if _, errorCount := c.Metrics.Health(); errorCount != 1 {
		t.Errorf("error count = %d, want 1", errorCount)
	}
}
