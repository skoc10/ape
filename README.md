# ape - Autonomous Platform Engineering

Multi-tenant platform for freelance DevOps engineers: a read-only agent in the
customer's Kubernetes cluster sends masked inventory to a self-hosted Control
Plane, which produces a white-label audit report and a GitHub pull request with
fixes. The locked architecture and the 4-week sprint plan live in
[`mvp-plan.md`](mvp-plan.md).

## Layout

| Path | What |
| --- | --- |
| `apps/web` | Next.js panel (Better Auth, shadcn/ui) |
| `apps/api` | Python package `ape`: FastAPI `api` and queue `worker` |
| `agent` | Go Data Plane agent |
| `db` | dbmate migrations, pgTAP tests, seed data |
| `deploy` | Docker Compose (local), Helm charts, k3s notes |
| `packages/protocol` | JSON Schema for the agent protocol |

## Local development

Requirements: Docker (Colima works) with Compose, Node 24 + pnpm, uv, Go, [Task](https://taskfile.dev), dbmate, golangci-lint.

```bash
cp .env.example .env
task setup      # install dependencies
task infra:up   # PostgreSQL 16 (pgTAP) on :54320 and S3 storage on :8333
task lint
task test
task dev        # infra + web, api and worker with live reload
```
