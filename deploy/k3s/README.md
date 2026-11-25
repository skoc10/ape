# Control Plane on a single VPS with k3s

Everything here is plain Kubernetes: the same chart runs on any cluster.

Requirements: a VPS with 4 GB RAM or more (Ubuntu 24.04 or Debian 12), ports 80 and 443 open,
and two DNS A records pointing at it, for example `cp.example.com` (panel and agent endpoint)
and `files.example.com` (report downloads).

## 1. k3s

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes
```

k3s ships Traefik as the ingress controller; WebSocket traffic works without extra settings.
Copy `/etc/rancher/k3s/k3s.yaml` to your workstation (replace `127.0.0.1` with the VPS address)
to use `kubectl` and `helm` from there.

## 2. cert-manager (TLS)

```bash
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true --wait
# edit the e-mail address first
kubectl apply -f deploy/k3s/cluster-issuer.yaml
```

## 3. CloudNativePG operator (PostgreSQL)

```bash
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm upgrade --install cnpg cnpg/cloudnative-pg -n cnpg-system --create-namespace --wait
```

## 4. Optional secrets

```bash
kubectl create namespace ape

# LLM explanations (only after Zero Data Retention is confirmed, see docs/SECURITY.md)
kubectl -n ape create secret generic llm-api-key --from-literal=LLM_API_KEY=...

# GitHub App (pull requests with fixes)
kubectl -n ape create secret generic github-app \
  --from-literal=GITHUB_APP_ID=... \
  --from-literal=GITHUB_APP_SLUG=... \
  --from-literal=GITHUB_APP_CLIENT_ID=... \
  --from-literal=GITHUB_APP_CLIENT_SECRET=... \
  --from-file=GITHUB_APP_PRIVATE_KEY=./github-app.private-key.pem
```

GitHub App settings: permissions Contents (read and write), Pull requests (read and write),
Metadata (read); "Request user authorization (OAuth) during installation" enabled; callback URL
and setup URL `https://cp.example.com/github/callback`.

## 5. Install the Control Plane

```bash
cp deploy/k3s/values-production.example.yaml values-production.yaml   # then edit
helm upgrade --install ape oci://ghcr.io/skoc10/charts/ape-control-plane \
  -n ape --create-namespace -f values-production.yaml --wait --timeout 10m
kubectl -n ape get pods
```

(From a checkout, use `deploy/helm/ape-control-plane` instead of the OCI reference.)

The chart creates the PostgreSQL cluster, the S3 storage with its buckets, the three
deployments (`web`, `api`, `worker`) and a migration job that creates the database roles and
applies the schema. Passwords, `BETTER_AUTH_SECRET` and `MASKING_KEY` are generated on the first
install into the Secret `ape-secrets`.

**Back up that Secret.** It is kept when the release is uninstalled, and `MASKING_KEY` must never
change: placeholders in old and new audits would no longer match.

```bash
kubectl -n ape get secret ape-secrets -o yaml > ape-secrets.backup.yaml   # store it somewhere safe
```

What is reachable from the internet: the panel (`/`) and the agent WebSocket (`/v1/agent`) on
`cp.example.com`, and signed download links on `files.example.com`. The REST API, `/metrics`,
`/docs` and the database are only reachable inside the cluster.

## 6. Metrics (optional)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
# then set observability.serviceMonitor.enabled=true and upgrade the release
```

Logs are read with `kubectl -n ape logs deploy/ape-api` (JSON lines with `trace_id`).

## 7. Backup and a restore drill

With `postgres.backup.enabled: true`, CloudNativePG takes a daily base backup and archives WAL
into the `backups` bucket.

```bash
kubectl -n ape get backups.postgresql.cnpg.io
kubectl cnpg status ape-pg -n ape            # needs the cnpg kubectl plugin
```

Try a restore once, before you need it: create a second cluster from the backup and compare.

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: ape-pg-restore, namespace: ape}
spec:
  instances: 1
  storage: {size: 20Gi}
  bootstrap:
    recovery: {source: ape-pg}
  externalClusters:
    - name: ape-pg
      barmanObjectStore:
        destinationPath: s3://backups/
        endpointURL: http://ape-s3:8333
        s3Credentials:
          accessKeyId: {name: ape-secrets, key: S3_ACCESS_KEY}
          secretAccessKey: {name: ape-secrets, key: S3_SECRET_KEY}
```

Note that the S3 storage runs on the same VPS: a backup there protects against a broken
database, not against losing the machine. For that, point `s3.mode: external` at storage
outside the VPS, or copy the `backups` bucket off the machine regularly.

## Upgrading

```bash
helm upgrade ape oci://ghcr.io/skoc10/charts/ape-control-plane -n ape -f values-production.yaml --wait
```

The migration job runs after every upgrade. Agents reconnect on their own within a minute
when the `api` pod restarts.
