package collect

import (
	"encoding/json"
	"strings"
	"testing"
)

// Every value below must be gone after Sanitize, wherever it sits.
var planted = []string{
	"hunter2-env-literal",
	"s3cr3t-in-args",
	"tok3n-next-arg",
	"pgpassw0rd",
	"managed-fields-leak",
	"last-applied-leak",
	"init-env-leak",
	"cron-env-leak",
	"sidecar-flag-leak",
}

func podSpec(envValue string) map[string]any {
	return map[string]any{
		"containers": []any{
			map[string]any{
				"name":  "app",
				"image": "ghcr.io/acme/app:1.2.3",
				"env": []any{
					map[string]any{"name": "DB_PASSWORD", "value": envValue},
					map[string]any{"name": "LOG_LEVEL", "value": "debug"},
					map[string]any{"name": "API_KEY", "valueFrom": map[string]any{
						"secretKeyRef": map[string]any{"name": "app-secrets", "key": "api-key"},
					}},
				},
				"command": []any{"/app", "--db-password=s3cr3t-in-args", "--port=8080"},
				"args": []any{
					"--token", "tok3n-next-arg",
					"--dsn=postgres://app:pgpassw0rd@db.internal:5432/app",
					"--verbose",
				},
			},
			map[string]any{
				"name":    "sidecar",
				"command": []any{"proxy", "-api-key=sidecar-flag-leak"},
			},
		},
		"initContainers": []any{
			map[string]any{"name": "init", "env": []any{map[string]any{"name": "X", "value": "init-env-leak"}}},
		},
	}
}

func metadata(name string) map[string]any {
	return map[string]any{
		"name":      name,
		"namespace": "prod",
		"annotations": map[string]any{
			lastAppliedAnnotation: `{"spec":{"env":"last-applied-leak"}}`,
			"team":                "payments",
		},
		"managedFields": []any{map[string]any{"manager": "kubectl", "fieldsV1": "managed-fields-leak"}},
	}
}

func fixtures() map[string]map[string]any {
	return map[string]map[string]any{
		"Pod": {"metadata": metadata("p"), "spec": podSpec("hunter2-env-literal")},
		"Deployment": {"metadata": metadata("d"), "spec": map[string]any{
			"replicas": int64(1),
			"template": map[string]any{"spec": podSpec("hunter2-env-literal")},
		}},
		"CronJob": {"metadata": metadata("c"), "spec": map[string]any{
			"schedule": "* * * * *",
			"jobTemplate": map[string]any{"spec": map[string]any{
				"template": map[string]any{"spec": podSpec("cron-env-leak")},
			}},
		}},
	}
}

func TestSanitizeRemovesEveryPlantedSecret(t *testing.T) {
	for kind, obj := range fixtures() {
		Sanitize(obj)
		encoded, err := json.Marshal(obj)
		if err != nil {
			t.Fatal(err)
		}
		for _, secret := range planted {
			if strings.Contains(string(encoded), secret) {
				t.Errorf("%s: %q survived sanitizing", kind, secret)
			}
		}
		if strings.Contains(string(encoded), "managedFields") {
			t.Errorf("%s: managedFields survived", kind)
		}
		if strings.Contains(string(encoded), lastAppliedAnnotation) {
			t.Errorf("%s: last-applied-configuration survived", kind)
		}
	}
}

func TestSanitizeKeepsWhatTheAnalysisNeeds(t *testing.T) {
	pod := fixtures()["Pod"]
	Sanitize(pod)
	container := pod["spec"].(map[string]any)["containers"].([]any)[0].(map[string]any)
	env := container["env"].([]any)

	if got := env[0].(map[string]any)["value"]; got != "<redacted:len=19>" {
		t.Errorf("env value = %v, want a length-preserving placeholder", got)
	}
	// Even harmless looking literals are dropped: the agent cannot tell them apart.
	if got := env[1].(map[string]any)["value"]; got != "<redacted:len=5>" {
		t.Errorf("LOG_LEVEL value = %v", got)
	}
	ref := env[2].(map[string]any)["valueFrom"].(map[string]any)["secretKeyRef"].(map[string]any)
	if ref["name"] != "app-secrets" || ref["key"] != "api-key" {
		t.Errorf("valueFrom reference was changed: %v", ref)
	}
	if env[0].(map[string]any)["name"] != "DB_PASSWORD" {
		t.Error("env names must stay")
	}
	command := container["command"].([]any)
	if command[0] != "/app" || command[1] != "--db-password=<redacted:len=14>" || command[2] != "--port=8080" {
		t.Errorf("command = %v", command)
	}
	args := container["args"].([]any)
	want := []any{"--token", "<redacted:len=14>", "--dsn=postgres://app:<redacted:len=10>@db.internal:5432/app", "--verbose"}
	for i := range want {
		if args[i] != want[i] {
			t.Errorf("args[%d] = %v, want %v", i, args[i], want[i])
		}
	}
	meta := pod["metadata"].(map[string]any)
	if meta["annotations"].(map[string]any)["team"] != "payments" || container["image"] != "ghcr.io/acme/app:1.2.3" {
		t.Error("unrelated metadata was changed")
	}
}

func TestSanitizeHandlesObjectsWithoutPodSpecs(t *testing.T) {
	for _, obj := range []map[string]any{
		{},
		{"metadata": map[string]any{"name": "ns"}},
		{"metadata": map[string]any{"annotations": map[string]any{lastAppliedAnnotation: "x"}}, "spec": "not-a-map"},
		{"spec": map[string]any{"containers": "not-a-list"}},
		{"spec": map[string]any{"containers": []any{"not-a-map", map[string]any{"env": []any{1, map[string]any{"value": 5}}}}}},
	} {
		Sanitize(obj) // must not panic
	}
	obj := map[string]any{"metadata": map[string]any{"annotations": map[string]any{lastAppliedAnnotation: "x"}}}
	Sanitize(obj)
	if _, ok := obj["metadata"].(map[string]any)["annotations"]; ok {
		t.Error("an annotations map emptied by sanitizing should be removed")
	}
}
