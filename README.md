# ape - Autonomous Platform Engineering

Multi-tenant platform for freelance DevOps engineers: a read-only agent in the customer's
Kubernetes cluster sends masked inventory to a self-hosted Control Plane, which produces a
white-label audit report and a GitHub pull request with fixes. The locked architecture and the
sprint plan live in [`mvp-plan.md`](mvp-plan.md); decisions taken on the way are in
[`MEMORY.md`](MEMORY.md).

```
customer cluster                         control plane (k3s on a VPS)
┌──────────────────┐   outbound WSS     ┌─────────────────────────────────────────────┐
│ agent (Go)       │ ─────────────────▶ │ api: gateway, masking on arrival            │
│ read-only, list  │                    │ worker: rules ─▶ LLM (optional) ─▶ report   │
└──────────────────┘                    │ web: panel (Next.js, Better Auth)           │
                                        │ PostgreSQL (RLS per tenant), S3 storage     │
                                        └─────────────────────────────────────────────┘
```

## Layout

| Path | What |
| --- | --- |
| `apps/web` | Next.js panel (Better Auth, shadcn/ui) |
| `apps/api` | Python package `ape`: FastAPI `api` and the queue `worker` |
| `agent` | Go Data Plane agent |
| `db` | dbmate migrations, role bootstrap, pgTAP tests, seed data |
| `deploy/compose` | local PostgreSQL (pgTAP), S3 storage, optional Ollama |
| `deploy/helm` | `ape-agent` (customer side) and `ape-control-plane` charts |
| `deploy/k3s` | [VPS installation guide](deploy/k3s/README.md) |
| `packages/protocol` | JSON Schema of the agent protocol, source of the generated types |
| `testdata/vulnerable` | deliberately misconfigured manifests with planted fake secrets |
| `docs` | [security checklist](docs/SECURITY.md), [what the agent sees](docs/what-the-agent-sees.md) |

## Local development

Requirements: Docker with Compose (Colima works), Node 24 with pnpm, [uv](https://docs.astral.sh/uv/),
Go, [Task](https://taskfile.dev), dbmate, golangci-lint, and for the agent tests kind, kubectl
and helm. PDF reports need Pango (`brew install pango`, or `libpango-1.0-0 libpangoft2-1.0-0`
on Debian/Ubuntu).

```bash
cp .env.example .env
task setup        # install dependencies for web, api and agent
task infra:up     # PostgreSQL 16 on :54320 and S3 storage on :8333
task db:reset     # roles, migrations and two demo tenants
task dev          # web on :3000, api on :8000 and the worker, with live reload
```

Open http://localhost:3000, sign up and create a workspace.

### Checks

```bash
task lint         # eslint + tsc, ruff + mypy, golangci-lint
task test         # web unit tests, API tests (real PostgreSQL and S3), Go tests, pgTAP
task gen:check    # generated protocol code is up to date
```

### An audit against a local cluster

```bash
task kind-up && task kind-vuln      # kind cluster with the vulnerable test set
task images                          # build the agent image
```

In the panel: **Clusters → Add cluster**, copy the token, then install the agent. From inside
kind the API on your machine is reachable through the Docker host gateway
(`docker exec ape-dev-control-plane ip route | awk '/default/ {print $3}'`), and the API has to
listen on all interfaces for that
(`cd apps/api && uv run uvicorn ape.server:app --factory --host 0.0.0.0 --port 8000` instead of `task dev:api`):

```bash
task kind-agent TOKEN=ape_... ENDPOINT=ws://<gateway-ip>:8000/v1/agent
```

The cluster turns "connected"; **Start audit** collects, masks, analyses and builds the report.
The same flow runs unattended as `apps/web/scripts/e2e-collect.mjs` (see `.github/workflows/e2e.yml`).

### LLM explanations

Off by default: findings then carry the rule's template text. To try a local model:

```bash
docker-compose -f deploy/compose/docker-compose.yml --profile llm up -d ollama
docker exec ape-ollama-1 ollama pull qwen2.5:3b
# in .env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:3b
```

For Claude set `LLM_PROVIDER=anthropic` and `LLM_API_KEY`, after the Zero Data Retention check
in [`docs/SECURITY.md`](docs/SECURITY.md).

## Deployment

- Control Plane: [`deploy/k3s/README.md`](deploy/k3s/README.md)
- Agent image and chart: tag `agent-v<version>` (workflow `agent-release`)
- Control Plane images and chart: tag `cp-v<version>` (workflow `control-plane-release`)
