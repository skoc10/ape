# Autonomous Platform Engineering — MVP Teknik Mimari ve Geliştirme Planı

Nov 24, 2026 · @skoc

## Özet ve kilitlenen kararlar

MVP tek bir akıştır: müşteri cluster'ına Helm ile kurulan salt-okur Go ajanı dışa doğru WSS ile bağlanır, toplanan veri maskelenir, deterministik kurallardan geçer, tek bir PydanticAI ajanıyla zenginleştirilir ve beyaz etiketli rapora ve GitHub PR'ına dönüşür. Control Plane'in tamamı açık kaynak bileşenlerle kendi VPS'indeki k3s'e Helm ile kurulur, yerelde Docker Compose ve kind ile çalışır. Hiçbir yönetilen bulut servisine bağımlı değildir. Cluster'a yazma, OPA, çoklu ajan ve embedding kapsam dışıdır.

&#91;embedded content: MVP mimarisi · Control Plane ve müşteri tarafı Data Plane\]

Müşterinin kubeconfig'i ve bulut anahtarları Control Plane'e hiç gelmez. Ajandan gelen veri FastAPI'de alınır alınmaz maskelenir; diske, MinIO'ya ve LLM'e yalnızca maskeli kopya gider. Ağır işler (LLM, PDF) ayrı bir worker pod'unda çalışır, böylece bir bellek taşması açık WebSocket bağlantılarını düşürmez.

### Kilitlenen kararlar (revizyon 2)

| # | Konu | Karar | Neden |
| --- | --- | --- | --- |
| 1 | AI çatısı | PydanticAI | Tek ajan ve yapılandırılmış çıktı için yeterli. LangGraph v2'deki çoklu ajana kalır. |
| 2 | LLM | Varsayılan Anthropic Claude Sonnet 5.5 (ZDR). `LLM_BASE_URL` ile OpenAI uyumlu vLLM veya Ollama'ya tek ortam değişkeniyle geçilir. | Sağlayıcı bağımlılığı yok; yerel modelde veri hiç dışarı çıkmaz. |
| 3 | Analiz yöntemi | Önce kural motoru, sonra LLM | LLM bulgu uydurmaz, yalnızca önceliklendirir, açıklar ve yama yazar. |
| 4 | Kimlik ve tenant | Better Auth + Organization ve JWT eklentileri. Kullanıcı ve organizasyon tabloları kendi PostgreSQL'imizde. Tenant anahtarı `organization.id`, her tabloda `org_id text`. | Açık kaynak, self-hosted; RLS `org_id` üzerinden çalışmaya devam eder. |
| 5 | Veritabanı | PostgreSQL 16. k3s'te CloudNativePG operatörü (CNCF), yerelde Docker Compose. | Supabase Cloud bağımlılığı kalkar; yedekleme MinIO'ya yapılır. |
| 6 | Dosya depolama | MinIO (S3 API). Kod yalnızca S3 API'sini kullanır. | Gerekirse Garage, SeaweedFS veya herhangi bir S3 servisine yalnızca config değişikliğiyle geçilir. |
| 7 | İş kuyruğu | Postgres `FOR UPDATE SKIP LOCKED`, Redis yok | Ek altyapı gerektirmez. |
| 8 | Barındırma | Tek VPS'te k3s + Helm. Ingress için k3s ile gelen Traefik, TLS için cert-manager (CNCF). Yerelde Docker Compose + kind. | CNCF uyumlu ve taşınabilir: aynı chart herhangi bir Kubernetes'te çalışır. |
| 9 | Servis ayırımı | `web`, `api` (WebSocket ve maskeleme) ve `worker` (kurallar, LLM, PDF) ayrı container ve Deployment | Worker'ın bellek limiti ayrıdır; OOM olursa yalnızca worker yeniden başlar. |
| 10 | Gözlemlenebilirlik | OpenTelemetry (trace ve `trace_id` içeren JSON loglar) ve Prometheus `/metrics`. Sentry yok. | CNCF standardı; istenen backend'e (Grafana stack, Jaeger vb.) bağlanabilir. |
| 11 | PDF | Jinja2 HTML şablonu + WeasyPrint. Web raporu da aynı HTML'i gösterir. | Tarayıcı motoru gerektirmez, hafif ve açık kaynak. |
| 12 | GitHub | GitHub App; `ruamel.yaml` ile yorumları koruyan düzenleme; `kind + namespace + name` eşleşmesi, olmazsa `kind + name` | Namespace'siz manifestler de yamalanabilir. |
| 13 | Embedding | v2'ye ertelendi. Tekrarlayan bulgular için `fingerprint` sütunu yeterli. | Kapsam ve altyapı sadeleşir; pgvector gerekmez. |
| 14 | Başlangıç ve kapasite | Sıfırdan yeni repo, haftada yaklaşık 40 saat | **Henüz onaylanmadı.** Daha az zamanın varsa "esnek" görevler v1.1'e kayar. |

## 1. Monorepo ve klasör yapısı

Tek bir `ape` reposu; dört uygulama (web, api, worker, agent), iki Helm chart (Control Plane ve müşteri ajanı) ve dilden bağımsız tek bir protokol şeması içerir. `api` ve `worker` aynı Python paketini paylaşır ama ayrı imaj ve Deployment olarak çalışır. Turborepo veya Nx yok: üç dil için tek bir `Taskfile.yml` ve yol filtreli CI yeterli.

```text
ape/
├─ apps/
│  ├─ web/                         # Next.js (App Router) + Tailwind + shadcn/ui + Better Auth
│  │  ├─ app/(auth)/                # giriş, kayıt, davet kabul
│  │  ├─ app/(app)/[orgSlug]/
│  │  │  ├─ clusters/               # liste, "cluster ekle" sihirbazı, helm komutu
│  │  │  ├─ audits/[runId]/         # bulgu listesi, rapor önizleme, PR butonu
│  │  │  └─ settings/               # beyaz etiket (logo, renk), GitHub App bağlantısı
│  │  ├─ app/api/auth/[...all]/     # Better Auth handler (session, organization, jwt, jwks)
│  │  ├─ lib/auth.ts                # betterAuth({ plugins: [organization(), jwt()] })
│  │  ├─ lib/api.ts                 # FastAPI client: Better Auth JWT ile
│  │  └─ components/ui/             # shadcn bileşenleri
│  └─ api/                         # Python 3.12 paketi (uv); iki giriş noktası
│     ├─ ape/server.py              # FastAPI: REST + ajan WebSocket gateway  → imaj: ape-api
│     ├─ ape/worker.py              # kuyruk işleyicisi: kurallar, LLM, PDF   → imaj: ape-worker
│     ├─ ape/core/                  # config, db havuzu, JWT (JWKS) doğrulama, tenant bağlamı
│     ├─ ape/observability/         # OpenTelemetry kurulumu, JSON log, Prometheus metrikleri
│     ├─ ape/routers/               # clusters, tokens, audits, reports, github
│     ├─ ape/gateway/               # WebSocket uç noktası, oturum kaydı, parça birleştirme
│     ├─ ape/sanitize/              # deterministik secret masking
│     ├─ ape/storage/               # S3 istemcisi (boto3), MinIO'ya bağlı değil
│     ├─ ape/rules/                 # security, rbac, network, cost, reliability kuralları
│     ├─ ape/analysis/              # PydanticAI ajanı, model fabrikası, çıktı şemaları, prompt'lar
│     ├─ ape/reports/               # Jinja2 şablonları + WeasyPrint
│     ├─ ape/github/                # GitHub App kimliği, ruamel.yaml eşleme, PR açma
│     ├─ Dockerfile.api / Dockerfile.worker
│     └─ tests/                     # unit, masking korpusu, tenant izolasyon testleri
├─ agent/                          # Go Data Plane ajanı (tek binary)
│  ├─ cmd/ape-agent/main.go
│  ├─ internal/collect/             # kaynak toplayıcılar + sanitize.go (kaynakta temizlik)
│  ├─ internal/transport/           # WSS istemcisi, yeniden bağlanma, parçalama
│  ├─ internal/protocol/            # şemadan üretilen mesaj tipleri
│  ├─ internal/telemetry/           # slog JSON + OpenTelemetry, Prometheus /metrics
│  └─ Dockerfile                    # distroless, non-root
├─ db/
│  ├─ migrations/                   # dbmate SQL migration'ları (Better Auth tabloları dahil)
│  ├─ tests/                        # pgTAP: tenant izolasyon testleri
│  └─ seed.sql                      # iki test tenant'ı (A ve B)
├─ deploy/
│  ├─ compose/docker-compose.yml    # yerel: postgres (pgTAP'lı), minio, web, api, worker
│  ├─ helm/ape-control-plane/       # web, api, worker Deployment'ları, Ingress, CNPG Cluster,
│  │                               #   MinIO, ServiceMonitor, migration Job'ı
│  ├─ helm/ape-agent/               # müşteri ajanı: Deployment, SA, ClusterRole, Binding,
│  │                               #   Secret, NetworkPolicy
│  └─ k3s/                          # VPS kurulum notları, cert-manager ve CNPG operatörü değerleri
├─ packages/protocol/              # JSON Schema: Go, Python ve TS tiplerinin tek kaynağı
├─ .github/workflows/              # web.yml, api.yml, agent.yml, db.yml, charts.yml (yol filtreli)
├─ Taskfile.yml                    # dev, test, lint, gen, kind-up, images, helm-package
└─ README.md
```

- **Protokol tek kaynaktan:** `packages/protocol/*.schema.json` dosyalarından Go tipleri `go-jsonschema`, Python modelleri `datamodel-code-generator` ile üretilir.
- **Migration tek kaynaktan:** Better Auth'un tabloları `@better-auth/cli generate` ile SQL olarak üretilir ve dbmate migration'ı olarak commit'lenir. Böylece şemayı tek bir araç yönetir.
- **Yerel geliştirme:** `task dev`, Docker Compose ile Postgres ve MinIO'yu, ardından web, api ve worker'ı başlatır. Ajan testleri için `task kind-up` bir kind cluster'ı kurar.
- **Yayınlama:** İmajlar (`ape-web`, `ape-api`, `ape-worker`, `ape-agent`) ve iki chart OCI olarak `ghcr.io`'ya gider. VPS'e dağıtım `helm upgrade --install ape-control-plane` ile yapılır.

## 2. Veritabanı şeması ve RLS politikaları

Tüm veri kendi PostgreSQL'imizde durur: Better Auth'un kullanıcı ve organizasyon tabloları ile uygulama tabloları aynı veritabanındadır. Uygulama tablolarının her birinde `org_id` var ve RLS `FORCE` ile açık. Alt tablolar üst tabloya `(id, org_id)` bileşik yabancı anahtarıyla bağlanır; böylece A tenant'ının bulgusu B tenant'ının audit'ine bağlanamaz.

### Tablolar ve ilişkiler

| Tablo | Sahibi | Amaç | Temel sütunlar | İlişki |
| --- | --- | --- | --- | --- |
| `user`, `session`, `account`, `verification`, `jwks` | Better Auth | Kimlik, oturum, JWT imza anahtarları | Better Auth CLI üretir | — |
| `organization` | Better Auth | Tenant kökü (müşteri workspace'i) | `id` (text), `name`, `slug`, `logo` | Kök |
| `member`, `invitation` | Better Auth | Üyelik, rol (`owner`, `admin`, `member`) ve davetler | `organizationId`, `userId`, `role` | organization 1–N |
| `organization_settings` | Uygulama | Beyaz etiket ve GitHub ayarları | `org_id` (PK), `branding` jsonb, `github_installation_id`, `github_repo`, `manifest_path` | organization 1–1 |
| `clusters` | Uygulama | Müşteri cluster'ı | `id`, `org_id`, `name`, `status`, `k8s_version`, `agent_version`, `last_seen_at` | organization 1–N |
| `agent_tokens` | Uygulama | Ajan kimlik token'ı | `id`, `org_id`, `cluster_id`, `prefix`, `token_hash`, `expires_at`, `revoked_at` | clusters 1–N |
| `audit_runs` | Uygulama | Bir "Müşteri Röntgeni" çalışması | `id`, `org_id`, `cluster_id`, `status`, `score`, `stats`, `snapshot_key`, `report_key`, `pr_url` | clusters 1–N |
| `findings` | Uygulama | Tek bir bulgu | `id`, `org_id`, `audit_run_id`, `rule_id`, `severity`, `resource_*`, `evidence` (maskeli), `patch`, `fingerprint` | audit\_runs 1–N |

`embeddings` tablosu ve pgvector v2'ye ertelendi. Çalışmalar arası tekrar eden bulgular `fingerprint` ile bulunur.

### Veritabanı rolleri ve tenant bağlamı

Tarayıcı veritabanına hiç bağlanmaz. Üç ayrı Postgres rolü var ve hiçbiri uygulama kodunda RLS'i atlayamaz.

| Rol | Kim kullanır | Erişim |
| --- | --- | --- |
| `app_auth` | Next.js içindeki Better Auth | Yalnızca Better Auth tabloları. Uygulama tablolarına hiç erişemez. |
| `app_backend` | `api` ve `worker` | Uygulama tablolarında RLS'e tabi okuma ve yazma; `member` ve `organization` tablolarında yalnızca okuma. BYPASSRLS yok. |
| `app_migrator` | dbmate migration Job'ı | Tablo sahibi, BYPASSRLS. Yalnızca migration sırasında kullanılır. |

1. Kullanıcı Next.js'te Better Auth ile giriş yapar ve aktif organizasyonu seçer. Organization eklentisi, kullanıcının o organizasyona üye olduğunu kontrol eder.
2. Next.js sunucusu, Better Auth JWT eklentisinden kısa ömürlü (15 dk) bir token alır. Token içinde `sub` (kullanıcı) ve `org_id` (aktif organizasyon) bulunur. FastAPI'ye bu token ile istek atar.
3. FastAPI token'ı Better Auth'un `/api/auth/jwks` uç noktasından alınan public key ile doğrular, ardından `member` tablosunda kullanıcının o organizasyona hâlâ üye olduğunu kontrol eder (60 sn önbellek).
4. Her veritabanı işleminin başında `select set_config('app.org_id', $1, true)` çağrılır. `true` parametresi ayarı işlem sınırında tutar, bu yüzden bağlantı havuzunda da güvenlidir. Worker da her işe başlarken aynı şeyi o işin `org_id`'si ile yapar.
5. Ajan bağlantısında token henüz bir tenant'a bağlı değildir. Tek istisna, yalnızca `app_backend`'in çağırabildiği `app.resolve_agent_token(hash)` adlı `SECURITY DEFINER` fonksiyondur.

### Uygulama migration'ı (özet)

Better Auth tabloları bundan önceki migration'da CLI çıktısı olarak yer alır.

```sql
create schema if not exists app;

-- Tenant bağlamı: yalnızca işlem başında set_config ile verilen org_id
create or replace function app.current_org_id() returns text
language sql stable as $$
  select nullif(current_setting('app.org_id', true), '')
$$;

create table organization_settings (
  org_id                 text primary key references organization(id) on delete cascade,
  branding               jsonb not null default '{}',   -- logo_key, primary_color, company_name
  github_installation_id bigint,
  github_repo            text,                          -- owner/name
  manifest_path          text default '/',
  updated_at             timestamptz not null default now()
);

create table clusters (
  id            uuid primary key default gen_random_uuid(),
  org_id        text not null references organization(id) on delete cascade,
  name          text not null,
  status        text not null default 'pending' check (status in ('pending','connected','offline')),
  k8s_version   text,
  agent_version text,
  last_seen_at  timestamptz,
  created_at    timestamptz not null default now(),
  unique (id, org_id),
  unique (org_id, name)
);

create table agent_tokens (
  id           uuid primary key default gen_random_uuid(),
  org_id       text not null,
  cluster_id   uuid not null,
  prefix       text not null,                   -- 'ape_' + ilk 8 karakter, UI'da gösterim için
  token_hash   bytea not null unique,           -- sha256(token); token'ın kendisi saklanmaz
  created_by   text not null references "user"(id),
  expires_at   timestamptz,
  revoked_at   timestamptz,
  last_used_at timestamptz,
  created_at   timestamptz not null default now(),
  foreign key (cluster_id, org_id) references clusters(id, org_id) on delete cascade
);

create table audit_runs (
  id            uuid primary key default gen_random_uuid(),
  org_id        text not null,
  cluster_id    uuid not null,
  status        text not null default 'queued' check (status in
                ('queued','collecting','collected','analyzing','reporting','completed','failed')),
  triggered_by  text not null references "user"(id),
  score         int check (score between 0 and 100),
  stats         jsonb not null default '{}',     -- kaynak sayıları, token ve süre bilgisi
  snapshot_key  text,                            -- S3: {org_id}/{run_id}/snapshot.json.gz (maskeli)
  report_key    text,                            -- S3: {org_id}/{run_id}/report.pdf
  pr_url        text,
  error         text,
  locked_at     timestamptz,                     -- worker kuyruğu
  created_at    timestamptz not null default now(),
  finished_at   timestamptz,
  unique (id, org_id),
  foreign key (cluster_id, org_id) references clusters(id, org_id) on delete cascade
);
create index on audit_runs (created_at) where status = 'collected';  -- worker'ın iş kuyruğu

create table findings (
  id             uuid primary key default gen_random_uuid(),
  org_id         text not null,
  audit_run_id   uuid not null,
  rule_id        text not null,                  -- örn. SEC-001
  category       text not null check (category in ('security','rbac','network','cost','reliability')),
  severity       text not null check (severity in ('critical','high','medium','low','info')),
  title          text not null,
  resource_kind  text not null,
  resource_ns    text,
  resource_name  text not null,
  evidence       jsonb not null,                 -- yalnızca maskelenmiş veri
  explanation    text,                           -- LLM
  remediation    text,                           -- LLM
  patch          text,                           -- LLM, YAML yaması
  fingerprint    text not null,                  -- sha256(rule_id, kind, ns, name): çalışmalar arası tekrarı bulur
  status         text not null default 'open' check (status in ('open','accepted','ignored')),
  created_at     timestamptz not null default now(),
  unique (audit_run_id, fingerprint),
  foreign key (audit_run_id, org_id) references audit_runs(id, org_id) on delete cascade
);
create index on findings (org_id, fingerprint);   -- "önceki çalışmada da vardı" sorgusu
```

### RLS politikaları ve izinler

```sql
-- Her uygulama tablosu için aynı kalıp
alter table clusters enable row level security;
alter table clusters force row level security;
create policy tenant_rw on clusters for all to app_backend
  using (org_id = app.current_org_id())
  with check (org_id = app.current_org_id());
-- (organization_settings, agent_tokens, audit_runs, findings için tekrarlanır)

-- Rol izinleri
revoke all on all tables in schema public from public;
grant select, insert, update, delete on clusters, agent_tokens, audit_runs, findings,
      organization_settings to app_backend;
grant select on organization, member to app_backend;          -- üyelik kontrolü için
grant select, insert, update, delete on "user", session, account, verification, jwks,
      organization, member, invitation to app_auth;            -- uygulama tabloları yok

-- Ajan token çözümlemesi: tek RLS istisnası, dar ve denetlenebilir.
-- Sahibi app_migrator (BYPASSRLS) olduğu için tablo RLS'ini aşabilir.
create or replace function app.resolve_agent_token(p_hash bytea)
returns table (org_id text, cluster_id uuid)
language sql security definer set search_path = public as $$
  update agent_tokens set last_used_at = now()
  where token_hash = p_hash and revoked_at is null
    and (expires_at is null or expires_at > now())
  returning org_id, cluster_id
$$;
revoke all on function app.resolve_agent_token(bytea) from public;
grant execute on function app.resolve_agent_token(bytea) to app_backend;
```

- **Nesne depolama (MinIO):** S3'te satır düzeyi güvenlik olmadığı için izolasyon uygulama katmanındadır. Bucket'lar private; tüm anahtarları `ape/storage` modülü `{org_id}/...` önekiyle ve tenant bağlamındaki `org_id`'den üretir; dışarıdan anahtar kabul etmez. Rapor indirme, tenant kontrolünden sonra üretilen 5 dakikalık presigned URL ile yapılır. MinIO erişim anahtarları yalnızca `api` ve `worker`'da bulunur.
- **Repository katmanı (çift kilit):** `ape/core/tenancy.py` içindeki repository fonksiyonları `org_id` olmadan sorgu çalıştırmaz; ham SQL yazımı lint kuralıyla yasaklanır.
- **Test:** `db/tests/tenant_isolation.sql` (pgTAP), A tenant'ı bağlamındayken B'nin her tablodaki satırlarının 0 döndüğünü, B'ye insert denemesinin hata verdiğini ve `app_auth` rolünün uygulama tablolarına erişemediğini doğrular. CI'da her migration'da çalışır.
- **Doğrulanacak:** Better Auth'un Postgres'te kullandığı tablo ve sütun adları (örneğin `"organizationId"` gibi camelCase adlar) CLI çıktısıyla Hafta 1'de kesinleşecek; SQL'deki referanslar ona göre güncellenecek.

## 3. Control Plane ve Data Plane (Go ajanı) protokolü

Bağlantıyı her zaman ajan başlatır: müşteri ortamında inbound port açılmaz, yalnızca 443'e giden trafik gerekir. Control Plane yalnızca önceden tanımlı bir "topla" komutu gönderebilir; ajan uzaktan rastgele komut veya sorgu çalıştırmaz.

### Kurulum ve kimlik doğrulama

1. Freelancer panelde "Cluster ekle" der. API, `clusters` satırı ve 32 bayt rastgele bir token üretir (`ape_` + base62). Token yalnızca bir kez gösterilir, veritabanında SHA-256 özeti tutulur.
2. Panel kopyalanabilir bir komut gösterir: `helm install ape-agent oci://ghcr.io/<kullanıcı>/charts/ape-agent -n ape-system --create-namespace --set token=<token> --set endpoint=wss://cp.<alan-adı>/v1/agent`. Token bir K8s Secret olarak saklanır.
3. Ajan `wss://.../v1/agent` adresine `Authorization: Bearer <token>` başlığı ve TLS 1.3 ile bağlanır. Gateway özeti `app.resolve_agent_token` ile çözer, bağlantıyı `(org_id, cluster_id)` çiftine bağlar ve bu bilgi oturum boyunca değişmez. Ajanın gönderdiği hiçbir mesaj tenant kimliğini belirleyemez.
4. Token iptal edilirse gateway açık soketi 4401 koduyla kapatır. Token rotasyonu v1.1'e bırakıldı; MVP'de iptal edip yeniden kurmak yeterli.

### Mesaj zarfı

Tüm mesajlar JSON metin çerçevesidir: `{"v":1, "type":"...", "id":"<ulid>", "ts":"<RFC3339>", "payload":{...}}`. Büyük veri gzip + base64 ile parçalara bölünür ve her çerçeve en fazla 512 KB olur. Bir parça her zaman bütün K8s nesnelerinden oluşan bir dizidir, bir nesne iki parçaya bölünmez. Bu sayede gateway her parçayı geldiği anda maskeleyebilir; `collect_done` sonrasında maskeli snapshot MinIO'ya yazılır ve çalışma `collected` durumuna geçerek worker'ın kuyruğuna düşer.

| Yön | `type` | Payload | Ne zaman |
| --- | --- | --- | --- |
| Ajan → CP | `hello` | ajan sürümü, K8s sürümü, node sayısı, desteklenen yetenekler | Bağlantı açılınca |
| CP → Ajan | `hello_ack` | oturum id, heartbeat aralığı (30 sn), toplama limitleri | `hello` sonrası |
| İki yön | `ping` / `pong` | boş | Her 30 sn; 90 sn yanıt yoksa bağlantı düşer |
| CP → Ajan | `collect_request` | `run_id`, kaynak listesi, namespace hariç tutma listesi | Audit başlatılınca |
| Ajan → CP | `collect_chunk` | `run_id`, `seq`, `kind`, gzip+base64 parça | Toplama sırasında |
| Ajan → CP | `collect_done` | `run_id`, toplam parça sayısı, kaynak başına sayım, SHA-256 | Toplama bitince |
| Ajan → CP | `error` | `run_id`, kod, mesaj (örn. `forbidden`, `timeout`) | Hata olunca |

Ajan kopan bağlantıyı üstel geri çekilmeyle (1 sn'den 60 sn'ye, jitter'lı) yeniden kurar. Yarım kalan bir toplama yeniden başlamaz; gateway o çalışmayı `failed` olarak işaretler ve kullanıcı yeniden başlatır.

### Toplanan kaynaklar

| Grup | Kaynaklar | Hangi analizde kullanılır |
| --- | --- | --- |
| İş yükleri | Pod, Deployment, StatefulSet, DaemonSet, Job, CronJob | securityContext, imaj etiketi, probe, kaynak istek ve limitleri |
| RBAC | ServiceAccount, Role, ClusterRole, RoleBinding, ClusterRoleBinding | wildcard yetkiler, cluster-admin bağları, varsayılan SA kullanımı |
| Ağ | Namespace, Service, Ingress, NetworkPolicy | NetworkPolicy olmayan namespace'ler, dışa açık servisler |
| Kapasite | Node, ResourceQuota, LimitRange, PodDisruptionBudget | kaynak israfı ve dayanıklılık |
| Kullanım (varsa) | `metrics.k8s.io` PodMetrics ve NodeMetrics | istek ile gerçek kullanım farkı (israf) |

Secret ve ConfigMap **içerikleri hiç toplanmaz**. Secret'tan yalnızca ad, namespace, tip ve anahtar adları alınır.

### Ajanın RBAC yetkisi (Helm şablonu)

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ape-agent-readonly
rules:
  - apiGroups: [""]
    resources: [pods, services, namespaces, nodes, serviceaccounts, resourcequotas, limitranges]
    verbs: [get, list]
  - apiGroups: [apps]
    resources: [deployments, statefulsets, daemonsets, replicasets]
    verbs: [get, list]
  - apiGroups: [batch]
    resources: [jobs, cronjobs]
    verbs: [get, list]
  - apiGroups: [networking.k8s.io]
    resources: [networkpolicies, ingresses]
    verbs: [get, list]
  - apiGroups: [rbac.authorization.k8s.io]
    resources: [roles, rolebindings, clusterroles, clusterrolebindings]
    verbs: [get, list]
  - apiGroups: [policy]
    resources: [poddisruptionbudgets]
    verbs: [get, list]
  - apiGroups: [metrics.k8s.io]
    resources: [pods, nodes]
    verbs: [get, list]
# secrets ve configmaps bilinçli olarak yok. Secret metadata'sı için ayrı bir
# metadata-only yol yok; bu yüzden MVP'de Secret'lar sadece Pod spec'teki
# referanslardan (secretKeyRef, volume adı) çıkarılır.
```

Ajan pod'u `runAsNonRoot`, salt-okur root dosya sistemi ve tüm capability'leri düşürülmüş olarak çalışır. Chart, yalnızca Control Plane'e çıkışa izin veren bir NetworkPolicy de içerir.

### Kaynakta temizlik (ajan tarafı, birinci kilit)

Ajan veriyi göndermeden önce `internal/collect/sanitize.go` ile şunları yapar:

- `metadata.managedFields` ve `kubectl.kubernetes.io/last-applied-configuration` anotasyonunu siler, çünkü bu ikisi tam manifest kopyası taşıyabilir.
- Container `env[].value` değerlerini `<redacted:len=N>` ile değiştirir. `valueFrom` referansları korunur, çünkü analiz için değerli ve gizli bilgi taşımazlar.
- `command` ve `args` içindeki `--password=`, `--token=` gibi bayrak değerlerini maskeler.
- Her çalışmada en fazla 5.000 pod ve toplam 50 MB sıkıştırılmış veri gönderir; aşılırsa `stats` içinde `truncated: true` döner.

Bu ilk temizlik, ikinci kilit olan Control Plane tarafındaki deterministik maskelemenin (Bölüm 4) yerine geçmez.

### Gözlemlenebilirlik (OpenTelemetry ve Prometheus)

Tüm servisler stdout'a `trace_id` ve `span_id` içeren yapılandırılmış JSON log yazar ve Prometheus formatında `/metrics` sunar. Trace'ler OTLP ile dışa aktarılır; `OTEL_EXPORTER_OTLP_ENDPOINT` boşsa trace kapalıdır, yani bir gözlemlenebilirlik backend'i zorunlu değildir.

| Bileşen | Kütüphaneler | Önemli metrikler |
| --- | --- | --- |
| `api` | `opentelemetry-instrumentation-fastapi`, `structlog`, `prometheus-client` | `ape_agent_sessions` (açık ajan bağlantısı), `ape_agent_messages_total{type}`, `ape_masking_redactions_total{detector}`, HTTP gecikme histogramı |
| `worker` | aynı Python paketi | `ape_audit_runs_total{status}`, `ape_audit_stage_seconds{stage}`, `ape_llm_tokens_total{model,direction}`, `ape_pdf_render_seconds` |
| `agent` | `log/slog` (JSON), OpenTelemetry Go SDK, `prometheus/client_golang` | `ape_agent_connected`, `ape_agent_reconnects_total`, `ape_agent_collect_seconds{kind}`, `ape_agent_objects_total{kind}` |

- Ajanın `/metrics` portu varsayılan olarak yalnızca pod içinde dinler. Müşteri isterse Helm değeriyle (`metrics.enabled=true`) bir `ServiceMonitor` ve yalnızca kendi monitoring namespace'inden gelen trafiğe izin veren NetworkPolicy kuralı açılır. İnternete açık port yine yoktur.
- Ajan, sağlık bilgisini (son toplama süresi, hata sayısı) `ping` mesajına da ekler; böylece Control Plane müşterinin Prometheus'una erişmeden ajan durumunu görür.
- VPS'te Control Plane için tek bir Prometheus (ve isteğe bağlı Grafana) yeterlidir. Loglar MVP'de `kubectl logs` ile okunur; Loki gibi bir log toplayıcı v1.1'e kalır.
- Loglar ve trace attribute'ları da maskelemeden geçer: K8s nesnesi içeriği asla loglanmaz, yalnızca `kind`, sayı ve süre loglanır.

## 4. AI analiz ve secret masking akışı

Bir audit çalışması altı aşamadan geçer. İlk ikisi `api` pod'unda, kalanı ayrı `worker` pod'unda çalışır. LLM yalnızca 4. aşamada devreye girer ve yalnızca maskelenmiş, kural motorunun seçtiği kanıt parçalarını görür; ham snapshot'ın tamamı LLM'e gitmez.

1. **Toplama** (`api`, `collecting`): Gateway parçaları alır ve SHA-256'yı doğrular.
2. **Maskeleme** (`api`, ikinci kilit): Her parça geldiği anda `ape/sanitize` ile maskelenir. MinIO'ya yazılan tek kopya maskeli kopyadır; ham veri yalnızca bellekte, parça boyutunda kalır. Bitince durum `collected` olur.
3. **Kural motoru** (`worker`, `analyzing`): Worker işi `SKIP LOCKED` ile alır, snapshot'ı MinIO'dan okur ve deterministik kuralları çalıştırır.
4. **LLM zenginleştirme** (`worker`): PydanticAI ajanı bulguları gruplar, önceliklendirir, açıklar ve YAML yaması yazar.
5. **Rapor** (`worker`, `reporting`): Skor hesaplanır, bulgular yazılır, HTML ve PDF üretilip MinIO'ya konur.
6. **PR (isteğe bağlı):** Kullanıcı panelde onayladığında worker GitHub PR'ını açar. PR otomatik açılmaz.

### Deterministik maskeleme

Maskeleme regex ve parser tabanlıdır, içinde LLM yoktur, aynı girdi her zaman aynı çıktıyı verir. Değerler silinmez, `<secret:aws_key:7f3a>` gibi tipli ve kararlı bir yer tutucuyla değiştirilir. `7f3a`, tenant'a özel anahtarla alınmış HMAC'in ilk karakterleridir. Böylece LLM "aynı sır iki yerde kullanılmış" diyebilir ama değeri göremez.

| Dedektör | Ne yakalar |
| --- | --- |
| Bilinen formatlar | AWS erişim anahtarı, GCP servis hesabı JSON'u, GitHub, Slack, Stripe token'ları, JWT, PEM bloğu |
| Bağlantı dizgileri | `postgres://user:pass@...` gibi URL'lerdeki parola kısmı |
| Anahtar adı kuralı | Adında `password`, `secret`, `token`, `apikey`, `credential` geçen alanların değerleri |
| Entropi | 20 karakterden uzun ve Shannon entropisi 4,0'ın üzerindeki dizgiler (izin listesi: imaj digest'leri, UID'ler) |
| Kişisel ve altyapı verisi | E-posta adresleri; isteğe bağlı olarak dış IP adresleri |

Maskeleme modülü kendi test korpusuyla gelir: `tests/sanitize/corpus/` içinde gerçekçi ama sahte sırlar içeren en az 50 YAML ve log örneği. CI'da "maskeleme sonrası çıktıda bilinen sırlardan hiçbiri geçmiyor" testi zorunludur. LLM çağrısı yapan tek fonksiyon `MaskedPayload` tipini kabul eder; bu tip yalnızca maskeleyici tarafından oluşturulabilir, böylece maskelenmemiş veriyi LLM'e göndermek tip düzeyinde zorlaşır.

### MVP kural seti (ilk 15 kural)

| Kategori | Kurallar |
| --- | --- |
| security | privileged container, `hostPath` / `hostNetwork` / `hostPID`, root olarak çalışma, `allowPrivilegeEscalation`, `:latest` veya etiketsiz imaj |
| rbac | `*` verb veya resource, `cluster-admin` bağları, varsayılan ServiceAccount kullanımı ve otomatik token mount'u, `secrets` üzerinde `list` yetkisi |
| network | NetworkPolicy olmayan namespace, `LoadBalancer` / `NodePort` ile dışa açık servis |
| cost | limit veya istek tanımsız container, isteğin gerçek kullanımın 3 katından fazla olması (metrics varsa) |
| reliability | liveness/readiness probe yok, tek replika ve PDB yok |

### LLM çıktı şeması (PydanticAI)

```python
class ResourceRef(BaseModel):
    kind: str
    namespace: str | None
    name: str

class Patch(BaseModel):
    format: Literal["strategic_merge", "json6902"]
    target: ResourceRef
    content: str                    # YAML; yalnızca maskeli yer tutucular içerebilir

class EnrichedFinding(BaseModel):
    fingerprint: str                # kural motorundan gelir, LLM yeni bulgu ekleyemez
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str = Field(max_length=120)
    explanation: str                # müşteriye yönelik, sade dil
    business_impact: str
    remediation: str                # adım adım
    patch: Patch | None             # güvenle yamalanamıyorsa None
    confidence: float = Field(ge=0, le=1)

class AuditAnalysis(BaseModel):
    executive_summary: str = Field(max_length=1200)
    top_risks: list[str] = Field(max_length=5)
    findings: list[EnrichedFinding]

analyst = Agent(
    build_model(),                   # aşağıdaki model fabrikası
    output_type=AuditAnalysis,
    system_prompt=SYSTEM_PROMPT,     # app/analysis/prompts/system.md
    retries=2,
)
```

### Model soyutlaması (sağlayıcıdan bağımsız LLM)

Analiz kodu hiçbir sağlayıcıyı doğrudan çağırmaz; modeli `ape/analysis/model.py` içindeki tek fonksiyon ortam değişkenlerinden kurar. Claude'dan yerel bir vLLM veya Ollama sunucusuna geçmek için yalnızca `LLM_PROVIDER`, `LLM_BASE_URL` ve `LLM_MODEL` değişir.

```python
# ape/analysis/model.py
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

def build_model(s: Settings = settings):
    match s.llm_provider:                        # LLM_PROVIDER
        case "anthropic":
            return AnthropicModel(s.llm_model,   # LLM_MODEL=claude-sonnet-5-5
                                  provider=AnthropicProvider(api_key=s.llm_api_key))
        case "openai_compatible":                # vLLM, Ollama, LiteLLM, OpenAI
            return OpenAIModel(s.llm_model,      # LLM_MODEL=qwen2.5-coder:32b vb.
                               provider=OpenAIProvider(base_url=s.llm_base_url,  # LLM_BASE_URL=http://ollama:11434/v1
                                                       api_key=s.llm_api_key or "none"))
        case _:
            raise ValueError(f"unknown LLM_PROVIDER: {s.llm_provider}")
```

- **Yerel modellerde çıktı güvenilirliği:** Yapılandırılmış çıktı tool calling ile alınır. vLLM'de tool calling açık olmalı, Ollama'da da tool destekli bir model seçilmeli. Şema doğrulaması başarısız olursa PydanticAI 2 kez yeniden dener, sonra şablon açıklamalara düşülür.
- **Kalite karşılaştırması:** `tests/analysis/golden/` altında 3.1'deki kötü manifest setinin beklenen çıktıları tutulur. `task eval LLM_PROVIDER=...` iki modeli aynı girdide karşılaştırır (geçerli yama oranı, şema hatası, süre).
- **Gizlilik:** Harici API kullanılırken ZDR şarttır. Yerel modelde veri VPS'ten hiç çıkmaz; maskeleme yine de kapatılmaz.

* **Prompt ilkeleri:** Rol tanımı ("kıdemli Kubernetes güvenlik denetçisi"), yalnızca verilen kanıtlara dayanma, yer tutucuları asla çözmeye çalışmama, yamayı en küçük değişiklikle yazma ve emin değilse `patch: null` döndürme.
* **Doğrulama:** Çıktıdaki her `fingerprint` kural motorunun listesinde olmalıdır, olmayan atılır. Her `patch.content` `yaml.safe_load` ile parse edilmeli ve hedef kaynak snapshot'ta bulunmalıdır.
* **Maliyet kontrolü:** Bulgular 25'lik gruplar halinde gönderilir. Toplam token kullanımı `audit_runs.stats` alanına yazılır ve çalışma başına üst sınır konur.
* **LLM erişilemezse:** Rapor yine kural motoru çıktısıyla ve hazır şablon açıklamalarla üretilir. Ürün LLM olmadan da çalışır.

### Skor ve beyaz etiketli rapor (WeasyPrint)

- **Skor:** 100'den başlar; critical için 15, high için 8, medium için 3, low için 1 puan düşülür, kategori başına en fazla 40 puan kesilir. Ağırlıklar tek bir config dosyasındadır.
- **Tek şablon, iki çıktı:** Worker, `ape/reports/templates/report.html.j2` Jinja2 şablonunu `organization_settings.branding` (logo, renk, firma adı) ile doldurur. Ürünün adı raporda görünmez. Bölümler: yönetici özeti, skor ve kategori dağılımı, ilk 5 risk, bulgu tablosu, her bulgu için açıklama ve düzeltme.
- **PDF:** Aynı HTML WeasyPrint ile A4 PDF'e dönüştürülür. WeasyPrint JavaScript çalıştırmaz, bu yüzden grafikler (skor halkası, kategori çubukları) Python'da statik SVG olarak üretilir. Fontlar imaja gömülür, dış ağ isteği yapılmaz.
- **Web görünümü:** HTML ve PDF MinIO'ya `{org_id}/{run_id}/` altına konur. Panel HTML'i sandbox'lı bir iframe'de gösterir, PDF presigned URL ile indirilir.
- **Bellek izolasyonu:** WeasyPrint ve LLM yalnızca `worker` pod'unda çalışır (örnek limit 1 GiB). Worker aynı anda tek iş alır; OOM olursa Kubernetes pod'u yeniden başlatır, iş `locked_at` zaman aşımıyla tekrar kuyruğa döner ve `api` ile açık WebSocket bağlantıları etkilenmez.

### GitHub PR akışı

1. Freelancer, müşterinin GitHub organizasyonuna ürünün GitHub App'ini yükler. İzinler: Contents ve Pull requests için read/write, Metadata için read. Workspace ayarlarında repo ve manifest dizini seçilir.
2. "PR aç" denildiğinde worker, seçili dizindeki `.yaml` ve `.yml` dosyalarını GitHub API ile okur ve `ruamel.yaml` round-trip modunda (`typ="rt"`) parse eder. Çok belgeli dosyalar (`---`) desteklenir; yorumlar, anahtar sırası ve girinti korunur.
3. Her yama aşağıdaki sırayla bir belgeye eşleştirilir:
   1. `kind + namespace + name` tam eşleşmesi.
   2. Manifestte `namespace` yoksa: aynı dizindeki `kustomization.yaml` dosyasının `namespace` alanı varsa o kullanılır.
   3. Hâlâ yoksa `kind + name` fallback'i: tek aday varsa eşleşir; birden fazla aday varsa belirsiz sayılır ve otomatik uygulanmaz.
4. Eşleşen yamalar ruamel ile belgeye uygulanır (strategic merge mantığıyla yalnızca ilgili alanlar), `ape/audit-<tarih>` dalına tek commit atılır ve PR açılır. PR açıklamasında özet, bulgu tablosu, rapor bağlantısı ve her yamanın hangi kuralla (tam eşleşme veya fallback) eşleştiği yazar.
5. Eşleşmeyen veya belirsiz yamalar (örneğin Helm `values.yaml` içinden üretilen kaynaklar) PR açıklamasına "elle uygulanacak" başlığı altında kod bloğu olarak eklenir. Helm değer dosyalarının otomatik yamalanması v2'ye kalır.
6. PR'ın URL'si `audit_runs.pr_url` alanına yazılır. Cluster'a hiçbir şey uygulanmaz; birleştirme ve dağıtım müşterinin kendi sürecindedir.

## 5. 4 haftalık sprint planı

Her haftanın sonunda çalışan ve gösterilebilir bir dilim var. 4. haftanın sonunda kendi test cluster'ında ve ilk dogfooding müşterinde uçtan uca bir röntgen, PDF ve PR üretebiliyor olacaksın. "Esnek" işaretli görevler zaman daralırsa v1.1'e kayar.

### Hafta 1: İskelet, kimlik ve tenant izolasyonu

Hafta sonu demosu: `task dev` ile tüm sistem yerelde Docker Compose üzerinde ayağa kalkar. Kullanıcı Better Auth ile kayıt olur, bir müşteri organizasyonu açar, "Cluster ekle" ile token ve helm komutunu alır. A tenant'ı B'nin hiçbir verisini göremez; bunu pgTAP testleri kanıtlar.

- [ ] **1.1 Repo ve yerel ortam**

  DoD: Bölüm 1'deki klasör yapısı, `Taskfile.yml` ve lint ayarları (ruff, mypy, golangci-lint, eslint) hazır. `deploy/compose/docker-compose.yml` PostgreSQL 16 (pgTAP eklentili) ve MinIO'yu sağlık kontrolleriyle başlatıyor; MinIO'da `snapshots` ve `reports` bucket'ları otomatik oluşuyor. Yol filtreli CI iş akışları boş iskeletle yeşil.
- [ ] **1.2 PostgreSQL şeması ve RLS**

  DoD: Better Auth CLI'ın ürettiği tablolar ve Bölüm 2'deki uygulama migration'ı dbmate ile sıfırdan kuruluyor (`task db:reset`). Üç rol (`app_auth`, `app_backend`, `app_migrator`) oluşuyor. pgTAP testleri CI'da geçiyor: her tablo için çapraz tenant okuma 0 satır, çapraz yazma hata, `app_auth` uygulama tablolarına erişemiyor.
- [ ] **1.3 Better Auth ve organizasyonlar**

  DoD: Next.js'te e-posta ve parola ile kayıt ve giriş, organizasyon oluşturma, aktif organizasyon değiştirme ve üye davet etme çalışıyor. Better Auth `app_auth` rolüyle bağlanıyor. JWT eklentisi `sub` ve `org_id` içeren 15 dakikalık token üretiyor, `/api/auth/jwks` yanıt veriyor. Organizasyon oluşturulunca boş bir `organization_settings` satırı ekleniyor.
- [ ] **1.4 FastAPI iskeleti, tenant bağlamı ve gözlemlenebilirlik**

  DoD: JWKS ile JWT doğrulama ve `member` tablosundan üyelik kontrolü yapan middleware hazır. Her işlemde `set_config('app.org_id', ...)` çağrılıyor. Token'sız, süresi geçmiş veya üye olunmayan organizasyon için istek 401 ya da 403 alıyor ve bunların testleri var. `/healthz`, `/readyz` ve `/metrics` (Prometheus) çalışıyor. Loglar `trace_id` içeren JSON formatında; OpenTelemetry FastAPI enstrümantasyonu açık.
- [ ] **1.5 Cluster ve token API'si ile UI**

  DoD: `POST /clusters` ve `POST /clusters/{id}/tokens` çalışıyor. Token bir kez gösteriliyor, veritabanında yalnızca hash var. Panelde shadcn/ui ile cluster listesi, "Cluster ekle" sihirbazı, kopyalanabilir `helm install` komutu ve token iptali var.
- [ ] **1.6 Container imajları ve Control Plane Helm chart iskeleti (esnek)**

  DoD: `ape-web`, `ape-api` ve `ape-worker` imajları CI'da build ediliyor (worker şimdilik boş bir döngü). `deploy/helm/ape-control-plane` chart'ı web, api ve worker'ı ayrı Deployment olarak, migration'ı bir Job olarak tanımlıyor ve kind üzerinde `helm install` ile ayağa kalkıyor. CloudNativePG ve MinIO'nun gerçek kurulumu Hafta 4'e kalıyor.

### Hafta 2: Data Plane (Go ajanı ve Helm)

Hafta sonu demosu: kind cluster'ına `helm install` yapılır, panelde cluster "connected" görünür, "Topla" denince maskeli snapshot MinIO'ya düşer.

- [ ] **2.1 Protokol şeması ve kod üretimi**

  DoD: `packages/protocol` içinde Bölüm 3'teki yedi mesaj tipinin JSON Schema'ları var. Go ve Python tipleri `task gen` ile üretiliyor; CI üretilen kodun güncel olduğunu kontrol ediyor.
- [ ] **2.2 WebSocket gateway**

  DoD: `/v1/agent` token ile kimlik doğruluyor, `hello` / `hello_ack` ve ping/pong çalışıyor, `clusters.status` ve `last_seen_at` güncelleniyor. İptal edilen token bağlantıyı 4401 ile kapatıyor. `ape_agent_sessions` ve `ape_agent_messages_total` metrikleri `/metrics`'te görünüyor. Geçersiz token testi var.
- [ ] **2.3 Go ajanı: bağlantı katmanı ve telemetri**

  DoD: WSS istemcisi, jitter'lı üstel geri çekilme ve heartbeat çalışıyor. Gateway yeniden başlatıldığında ajan 60 saniye içinde kendiliğinden yeniden bağlanıyor. `slog` JSON logları ve pod içi `/metrics` uç noktası hazır.
- [ ] **2.4 Go ajanı: toplayıcılar ve kaynakta temizlik**

  DoD: Bölüm 3'teki tüm kaynaklar `client-go` ile listeleniyor ve bütün nesnelerden oluşan parçalar halinde gönderiliyor. `sanitize.go` birim testleri `env` değerlerinin ve `managedFields` alanının gitmediğini kanıtlıyor. metrics-server yoksa hata vermeden atlanıyor.
- [ ] **2.5 Ajan Helm chart'ı ve imajı**

  DoD: Distroless, non-root imaj ve chart OCI olarak ghcr'a yayınlanıyor. `helm install` kind'da çalışıyor. `kubectl auth can-i --list --as=system:serviceaccount:ape-system:ape-agent` çıktısında hiç yazma yetkisi ve `secrets` erişimi yok. `metrics.enabled=true` iken ServiceMonitor oluşuyor.
- [ ] **2.6 Toplama uçtan uca ve ilk maskeleme**

  DoD: Panelden "Audit başlat" ile `audit_runs` satırı oluşuyor, `collect_request` gidiyor, parçalar SHA-256 ile doğrulanıyor. Her parça gateway'de `ape/sanitize` arayüzünden geçiyor (bu hafta ilk iki dedektör: anahtar adı kuralı ve PEM bloğu) ve maskeli snapshot MinIO'da `{org_id}/{run_id}/` altına yazılıyor. Çalışma `collected` durumuna geçiyor.

### Hafta 3: AI beyni (maskeleme, kurallar, LLM)

Hafta sonu demosu: Kasıtlı olarak hatalı yapılandırılmış bir test cluster'ında audit çalışır, panelde açıklamalı ve yamalı bulgular listelenir.

- [ ] **3.1 Test cluster'ı için kötü manifest seti**

  DoD: `testdata/vulnerable/` altında her kuralı en az bir kez tetikleyen manifestler ve sahte sırlar var. `task kind-vuln` bunları kuruyor.
- [ ] **3.2 Deterministik maskelemenin tamamlanması**

  DoD: Bölüm 4'teki beş dedektörün hepsi, HMAC'li yer tutucular ve `MaskedPayload` tipi hazır. 50 örnekli korpus testi geçiyor: çıktıda bilinen sırlardan hiçbiri yok. Aynı girdi iki kez aynı çıktıyı veriyor. `ape_masking_redactions_total{detector}` metriği doluyor.
- [ ] **3.3 Kural motoru**

  DoD: 15 kuralın her biri için pozitif ve negatif birim testi var. Kural motoru 3.1'deki setin tüm beklenen bulgularını buluyor ve fingerprint üretiyor. Önceki çalışmada da bulunan bulgular `fingerprint` ile "tekrarlayan" olarak işaretleniyor.
- [ ] **3.4 Worker servisi**

  DoD: `ape-worker` ayrı bir süreç ve imaj olarak `collected` durumundaki çalışmaları `SKIP LOCKED` ile alıyor, durum geçişlerini yazıyor ve her işte `app.org_id` bağlamını kuruyor. Worker öldürülünce 10 dakikadan eski `locked_at` kayıtları kuyruğa dönüyor; bu sırada `api` ve ajan bağlantıları etkilenmiyor.
- [ ] **3.5 PydanticAI analiz ajanı ve model soyutlaması**

  DoD: `AuditAnalysis` şemasıyla yapılandırılmış çıktı alınıyor; bilinmeyen fingerprint'ler atılıyor, yamalar parse ediliyor. Aynı kod yalnızca ortam değişkenleri değiştirilerek hem Claude hem de Compose'a eklenen bir Ollama servisiyle çalışıyor. LLM kapalıyken şablon açıklamalarla devam ediliyor. Token kullanımı `stats` alanına ve `ape_llm_tokens_total` metriğine yazılıyor.
- [ ] **3.6 Bulgu ekranı**

  DoD: `audits/[runId]` sayfasında çalışma durumu canlı güncelleniyor (polling yeterli). Bulgular önem ve kategoriye göre filtreleniyor; her bulgunun açıklaması, yaması ve "tekrarlayan" işareti görülüyor.

### Hafta 4: Çıktı ve değer (rapor, PR, dogfooding)

Hafta sonu demosu: Control Plane kendi VPS'indeki k3s'te çalışır. Gerçek bir müşteri cluster'ında, kurulumdan beyaz etiketli PDF'e kadar 10 dakikanın altında bir röntgen ve bir düzeltme PR'ı alınır.

- [ ] **4.1 Beyaz etiket ayarları**

  DoD: Ayarlar sayfasından logo (MinIO'ya), ana renk ve firma adı `organization_settings.branding` alanına kaydediliyor.
- [ ] **4.2 Rapor şablonu ve skor**

  DoD: Jinja2 şablonu Bölüm 4'teki bölümleri içeriyor ve ürün adı hiçbir yerde görünmüyor. Grafikler statik SVG. Skor config'teki ağırlıklarla hesaplanıyor ve birim testi var. Panel raporu sandbox'lı iframe'de gösteriyor.
- [ ] **4.3 WeasyPrint ile PDF**

  DoD: Worker aynı HTML'den A4 PDF üretip MinIO'ya koyuyor, panelden presigned URL ile indiriliyor. 100 bulguluk raporda sayfa taşması yok ve worker bellek kullanımı 1 GiB limitinin altında kalıyor (`ape_pdf_render_seconds` ile ölçülüyor).
- [ ] **4.4 GitHub App ve PR**

  DoD: App kurulumu ve repo seçimi `organization_settings`'e kaydediliyor. Test reposunda ruamel.yaml ile yorumlar korunarak yamalar uygulanıyor ve PR açılıyor. Testler üç durumu kapsıyor: namespace'li tam eşleşme, namespace'siz manifest için `kind + name` fallback'i ve birden fazla adaylı belirsiz durum ("elle uygulanacak" bölümüne düşer). PR URL'si panelde görünüyor.
- [ ] **4.5 Güvenlik kontrol listesi**

  DoD: Tenant izolasyon testleri, "LLM'e giden payload'da sır yok" testi, "loglarda K8s nesne içeriği yok" testi ve ajanın yazma yetkisi olmadığı testi CI'da yeşil. Harici LLM kullanılıyorsa ZDR'nin açık olduğu yazılı olarak doğrulandı.
- [ ] **4.6 VPS'te k3s dağıtımı**

  DoD: VPS'te k3s, cert-manager (Let's Encrypt) ve CloudNativePG operatörü kurulu. `helm upgrade --install ape-control-plane` ile web, api, worker, PostgreSQL (CNPG, MinIO'ya günlük yedekli) ve MinIO çalışıyor. Traefik WebSocket trafiğini `api`'ye yönlendiriyor. Prometheus tüm `/metrics` uç noktalarını topluyor. Bir yedekten geri yükleme bir kez denendi.
- [ ] **4.7 Dogfooding**

  DoD: Kendi müşterilerinden birinde uçtan uca bir röntgen alındı, süre ölçüldü ve sorunlar backlog'a yazıldı.
- [ ] **4.8 Kurulum dökümanı (esnek)**

  DoD: Müşteriye gönderilebilecek tek sayfalık "ajan ne görür, ne görmez" belgesi, README'de yerel geliştirme rehberi ve `deploy/k3s/` altında VPS kurulum adımları var.

### Bilerek dışarıda bırakılanlar

Cluster'a yazma veya yama uygulama, OPA, çoklu ajan ve LangGraph, embedding ve pgvector (kurumsal hafıza), token rotasyonu, birden fazla `api` instance'ı, Loki gibi merkezi log toplama, Helm `values.yaml` yamalama, faturalandırma ve abonelik, Handoff akışı. Bunların hepsi v1.1 veya v2'ye kalıyor.
