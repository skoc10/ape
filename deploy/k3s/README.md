# Control Plane on a single VPS with k3s

Everything here is plain Kubernetes: the same chart runs on any cluster.

Requirements: a VPS with 4 GB RAM or more (Ubuntu 24.04 or Debian 12), ports 80 and 443 open,
and two DNS A records pointing at it, for example `cp.example.com` (panel and agent endpoint)
and `files.example.com` (report downloads).

## 1. k3s

```bash
curl -sfL https://get.k3s.io | sudo sh -s - --write-kubeconfig-mode 600
# the node registers a few seconds after the installer returns
until sudo k3s kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 3; done
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | sudo bash
mkdir -p ~/.kube && sudo cat /etc/rancher/k3s/k3s.yaml > ~/.kube/config && chmod 600 ~/.kube/config
echo 'export KUBECONFIG=$HOME/.kube/config' >> ~/.bashrc && export KUBECONFIG=$HOME/.kube/config
```

k3s ships Traefik as the ingress controller; WebSocket traffic works without extra settings.
Copy `/etc/rancher/k3s/k3s.yaml` to your workstation (replace `127.0.0.1` with the VPS address)
to use `kubectl` and `helm` from there.

## 2. cert-manager (TLS)

```bash
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true --wait
# set your e-mail address first (or delete the line: it is optional)
kubectl apply -f deploy/k3s/cluster-issuer.yaml
kubectl get clusterissuer letsencrypt        # READY must be True
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

What is reachable from the internet, over HTTPS only: the panel (`/`) and the agent WebSocket
(`/v1/agent`) on `cp.example.com`, and signed download links on `files.example.com`. The REST API, `/metrics`,
`/docs` and the database are only reachable inside the cluster.

## 6. Metrics (optional)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
  --set grafana.enabled=false --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.retention=15d \
  --set 'prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.accessModes[0]=ReadWriteOnce' \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=10Gi
# then set observability.serviceMonitor.enabled=true and upgrade the release
```

`serviceMonitorSelectorNilUsesHelmValues=false` makes Prometheus pick up the ServiceMonitors of
other releases. Prometheus is not exposed; look at it with
`kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090`.

Logs are read with `kubectl -n ape logs deploy/ape-api` (JSON lines with `trace_id`).

## 7. Backup and a restore drill

With `postgres.backup.enabled: true`, CloudNativePG takes a daily base backup and archives WAL
continuously. There are two destinations.

### 7a. Off the node (recommended)

A backup that lives on the machine it protects is lost with the machine. Send backups to an
S3-compatible bucket somewhere else (Cloudflare R2, Backblaze B2, AWS S3, ...):

1. Create a bucket that is used for nothing else, and an access key that can only use that
   bucket (read, write, list, delete). Do not reuse a key that can reach other data.
2. Store the key in the cluster:

   ```bash
   kubectl -n ape create secret generic pg-backup-offsite \
     --from-literal=ACCESS_KEY_ID=... --from-literal=SECRET_ACCESS_KEY=...
   ```

3. Set in your values file and upgrade the release:

   ```yaml
   postgres:
     backup:
       enabled: true
       external:
         enabled: true
         endpointUrl: https://<account>.r2.cloudflarestorage.com   # leave out for AWS S3
         destinationPath: s3://<bucket>/ape
         existingSecret: pg-backup-offsite
   ```

4. Take a base backup right away (see below): WAL archived to the new destination is only
   useful together with a base backup that is there too.

Switching an existing installation from 7b to 7a: the new destination has to be empty. The
backups in the in-cluster bucket stay where they are and are no longer extended; delete them
only after a restore from the new destination has worked.

### 7b. In the cluster

Without `external.enabled`, backups go to the `backups` bucket of the in-cluster S3 storage.
This protects against a broken database, not against losing the machine.

The backup bucket has an S3 identity of its own (`S3_BACKUP_ACCESS_KEY` and
`S3_BACKUP_SECRET_KEY` in `ape-secrets`). The keys the api and the worker run with cannot
read it: a backup holds every tenant without row level security. From outside, the file host
routes only the report bucket.

### Checking, on demand, and the restore drill

```bash
kubectl -n ape get backups.postgresql.cnpg.io
kubectl cnpg status ape-pg -n ape            # needs the cnpg kubectl plugin
```

Take a backup on demand by creating a `Backup` object (`kind: Backup`, `spec.cluster.name: ape-pg`)
and wait until `kubectl -n ape get backups.postgresql.cnpg.io` shows it as completed.

Try a restore once, before you need it, and again after changing the destination: create a
second cluster from the backup, compare row counts with the live one, then delete it
(`kubectl -n ape delete cluster ape-pg-restore`).

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
        # 7b, in the cluster:
        destinationPath: s3://backups/
        endpointURL: http://ape-s3:8333
        s3Credentials:
          accessKeyId: {name: ape-secrets, key: S3_BACKUP_ACCESS_KEY}
          secretAccessKey: {name: ape-secrets, key: S3_BACKUP_SECRET_KEY}
        # 7a, off the node: the destinationPath and endpointURL from your values, and
        #   accessKeyId: {name: pg-backup-offsite, key: ACCESS_KEY_ID}
        #   secretAccessKey: {name: pg-backup-offsite, key: SECRET_ACCESS_KEY}
```

Not covered by these backups: the `snapshots` and `reports` buckets (collected inventories
and rendered reports) and the `ape-secrets` Secret. Keep a copy of the Secret outside the
cluster; without `MASKING_KEY` and the database passwords a restored database is of no use.

## Upgrading

```bash
helm upgrade ape oci://ghcr.io/skoc10/charts/ape-control-plane -n ape -f values-production.yaml --wait
```

Check first that no audit is running; restarting the worker interrupts it, and it is only
picked up again after ten minutes:

```bash
kubectl -n ape exec ape-pg-1 -c postgres -- psql -U postgres -d ape -Atc \
  "select count(*) from audit_runs where status not in ('completed','failed')"
```

A new migration job runs with every upgrade. Agents reconnect on their own within a minute
when the `api` pod restarts.

### Upgrading to a chart with separate storage identities (0.1.9 and later)

The first upgrade adds `S3_BACKUP_ACCESS_KEY` and `S3_BACKUP_SECRET_KEY` to `ape-secrets`,
restarts the S3 storage with two bucket-scoped identities and switches the database's WAL
archiving to the backup identity. Existing backups stay readable. Back up `ape-secrets` again
afterwards: it has two new keys. If you use `secrets.existingSecret`, add the two keys to
your Secret before upgrading.
