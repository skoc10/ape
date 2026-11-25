package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestValidateEndpoint(t *testing.T) {
	cases := []struct {
		endpoint string
		insecure bool
		ok       bool
	}{
		{"wss://cp.example.com/v1/agent", false, true},
		{"ws://localhost:8000/v1/agent", true, true},
		{"ws://localhost:8000/v1/agent", false, false},
		{"https://cp.example.com/v1/agent", false, false},
		{"", false, false},
		{"not a url", false, false},
	}
	for _, c := range cases {
		err := ValidateEndpoint(c.endpoint, c.insecure)
		if (err == nil) != c.ok {
			t.Errorf("ValidateEndpoint(%q, %v) = %v, want ok=%v", c.endpoint, c.insecure, err, c.ok)
		}
	}
}

func TestLoadReadsTokenFromFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(path, []byte("ape_fromfile\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("APE_ENDPOINT", "wss://cp.example.com/v1/agent")
	t.Setenv("APE_TOKEN", "")
	t.Setenv("APE_TOKEN_FILE", path)
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Token != "ape_fromfile" || cfg.MetricsAddr != "127.0.0.1:9090" {
		t.Fatalf("unexpected config: token set=%v addr=%s", cfg.Token != "", cfg.MetricsAddr)
	}
}

func TestLoadRequiresToken(t *testing.T) {
	t.Setenv("APE_ENDPOINT", "wss://cp.example.com/v1/agent")
	t.Setenv("APE_TOKEN", "")
	t.Setenv("APE_TOKEN_FILE", "")
	if _, err := Load(); err == nil {
		t.Fatal("expected an error without a token")
	}
}
