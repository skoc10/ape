# MEMORY

Otonom geliştirme sırasında alınan kararlar ve kullanıcıdan beklenen aksiyonlar.
Ana plan: [`mvp-plan.md`](mvp-plan.md).

## Kararlar

### 1.1 Repo ve yerel ortam (2026-11-24)

- **MinIO yerine yerelde SeaweedFS.** MinIO resmi container imajlarını Docker Hub ve quay.io'dan
  kaldırmış (`minio/minio` ve `minio/mc` 404 dönüyor), bu yüzden plandaki karar 6 aynen
  uygulanamadı. Plan zaten "kod yalnızca S3 API'sini kullanır, başka S3 servisine yalnızca
  config ile geçilir" dediği için Compose'da `chrislusf/seaweedfs:4.55` (Apache-2.0, `weed mini`)
  kullanıldı. Bucket'lar (`snapshots`, `reports`) açılışta otomatik oluşuyor, anonim erişim 403.
  Ortam değişkenleri sağlayıcıdan bağımsız: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`.
  Prod (k3s) için depolama seçimi (SeaweedFS veya Garage) görev 4.6'da kesinleşecek.
- **Yerel portlar:** PostgreSQL host portu `54320` (makinede 5432'de başka bir Postgres çalışıyor),
  S3 `8333`. İkisi de yalnızca `127.0.0.1`'e bağlı.
- **Compose komutu:** Makinede `docker compose` eklentisi yok, `docker-compose` var. Taskfile
  hangisi varsa onu kullanıyor.
- **Sürümler:** Next.js 16 (App Router, Tailwind 4, pnpm), Python 3.12 + uv, Go 1.27,
  golangci-lint v2, dbmate. Kod içi yorumlar İngilizce.
- **Lint:** ruff (E,F,I,B,UP,S,ASYNC,SIM,RUF) + mypy strict, golangci-lint (standard + gosec,
  revive, errorlint), eslint-config-next.
- **Geliştirme branch'i:** `claude/project-thread-2s1349`.

### 1.2 PostgreSQL şeması ve RLS (2026-11-24)

- **Better Auth şeması:** CLI artık `auth` paketi (`pnpm dlx auth@latest generate`); eski
  `@better-auth/cli` 1.4'te kaldı. Çıktı `db/migrations/20261124000001_better_auth.sql` içinde.
  Tablo adları tekil ve sütunlar camelCase: `"user"`, `"member"."organizationId"`, `"userId"`.
- **Roller migration'da değil, bootstrap'ta:** `db/bootstrap/roles.sql` süper kullanıcıyla bir kez
  çalışır (Compose'da initdb betiği, `task db:bootstrap`; k3s'te CNPG managed roles olacak).
  Migration'lar `app_migrator` ile çalışır ve rol oluşturma yetkisi gerektirmez.
- **`organization_settings` satırı trigger ile:** `app_auth` uygulama tablolarına yazamadığı için
  boş ayar satırını `organization` üzerindeki `SECURITY DEFINER` trigger ekliyor (Better Auth
  hook'u yerine). Böylece `app_auth` izolasyonu delinmiyor.
- **Ek sıkılaştırmalar:** `app` şeması ve fonksiyonları PUBLIC'e kapalı; `app_backend` kimlik
  tablolarından yalnızca `organization` ve `member` okuyabiliyor (`user`, `session`, `account`,
  `jwks` kapalı).
- **pgTAP:** `db/tests/tenant_isolation.sql` 64 test; `pg_prove` Postgres imajına kuruldu,
  `task db:test` ile çalışır. RLS kapatılarak testlerin gerçekten kırıldığı doğrulandı.
- **dbmate `--no-dump-schema`:** host'ta `pg_dump` olmadığı için `schema.sql` üretilmiyor.

### 1.3 Better Auth ve organizasyonlar (2026-11-24)

- **Next.js 16 Cache Components kapalı:** Paneldeki her sayfa kullanıcıya ve tenant'a özel
  olduğu için `cacheComponents` ve `partialPrefetching` kapatıldı. Route tipleri
  `next typegen` ile üretiliyor (lint adımına eklendi).
- **API token'ı aktif organizasyona sabitleniyor:** `lib/api.ts` içindeki `getApiToken(orgId)`,
  oturumun aktif organizasyonu URL'deki organizasyondan farklıysa önce onu değiştirir, sonra
  üretilen JWT'nin `org_id` claim'ini doğrular. Böylece iki sekmede iki farklı müşteri açıkken
  yanlış tenant'a istek gitmez. JWT: `iss` = `BETTER_AUTH_URL`, `aud` = `ape-api`, 15 dk, EdDSA.
- **Davet e-postası stub:** SMTP olmadığı için davet bağlantısı panelde (kopyalanabilir) gösterilir
  ve sunucu loguna yalnızca bağlantı yazılır. E-posta doğrulaması MVP'de kapalı.
- **UI dili İngilizce**, shadcn/ui `base-nova` stili (Base UI tabanlı). Parola en az 10 karakter.
- **Uçtan uca test:** `apps/web/scripts/auth-smoke.mjs` (19 kontrol) `task test:web:smoke` ile ve
  CI'da `auth-smoke` işinde gerçek veritabanına karşı çalışır.

### 1.4 FastAPI iskeleti, tenant bağlamı ve gözlemlenebilirlik (2026-11-24)

- **Sürücü:** psycopg 3 (async) + psycopg_pool. Bağlantı almanın yalnızca iki yolu var:
  `Database.tenant(org_id)` (işlem başında `set_config('app.org_id', ..., true)`) ve tenant
  bağlamı olmayan `Database.system()` (üyelik sorgusu, token çözümleme, readiness).
- **JWT:** PyJWT ile yalnızca `EdDSA` kabul edilir (algoritma başlıktan okunmaz); `exp`, `iat`,
  `sub`, `aud`, `iss` zorunlu. JWKS `kid` bazında önbelleklenir, bilinmeyen `kid` için en fazla
  10 sn'de bir yeniden çekilir. JWKS'e ulaşılamazsa 401 değil 503 döner.
- **Üyelik önbelleği:** yalnızca olumlu sonuçlar 60 sn tutulur; üyelikten çıkarılan kullanıcının
  geçerli token'ı en geç 60 sn sonra 403 alır (uçtan uca testle doğrulandı).
- **Çift kilit:** SQL yalnızca `ape/repos` altında yazılır ve her fonksiyon `TenantConnection`
  veya `SystemConnection` alır; `tests/test_architecture.py` bunu AST ile zorunlu kılıyor
  (plandaki "ham SQL lint kuralı").
- **Gözlemlenebilirlik:** structlog JSON (`trace_id`, `span_id`), OpenTelemetry FastAPI
  enstrümantasyonu (OTLP yalnızca `OTEL_EXPORTER_OTLP_ENDPOINT` doluysa), Prometheus
  `ape_http_request_duration_seconds{method,route,status}`. Etikette ham yol değil route şablonu
  var. `/metrics` API portunda; 4.6'da Ingress'ten dışarı açılmamalı.
- **Testler gerçek Postgres'e karşı çalışır** (RLS'in anlamlı olması için); CI'da Compose ile.

### 1.5 Cluster ve token API'si ile UI (2026-11-25)

- **Uç noktalar:** `GET/POST /v1/clusters`, `GET /v1/clusters/{id}`,
  `GET/POST /v1/clusters/{id}/tokens`, `DELETE /v1/clusters/{id}/tokens/{token_id}`.
- **Rol kuralı (plan belirtmiyordu):** cluster ve token oluşturma/iptal yalnızca `owner` ve
  `admin`; `member` yalnızca okur. Başka tenant'ın kaynağı her zaman 404 döner.
- **Token:** `ape_` + 43 karakter base62 (32 bayt rastgele). Veritabanında yalnızca SHA-256 özeti
  ve gösterim için `ape_` + ilk 8 karakter var; token yanıtta bir kez döner, loglanmaz.
  İsteğe bağlı `expires_in_days` (1-365).
- **Panel:** yazma işlemleri Next.js Server Action'ları üzerinden; her action oturumu ve üyeliği
  yeniden doğrular, id'ler UUID değilse API'ye gitmez. Token iptali iki tıklamayla onaylanır.
- **Helm komutu:** `AGENT_ENDPOINT` ve `AGENT_CHART_REF` ortam değişkenlerinden üretilir.

## Kullanıcı Aksiyonu Gerekenler

- **Control Plane alan adı (1.5).** Sihirbazdaki helm komutu şu an `ws://localhost:8000/v1/agent`
  gösteriyor. Prod için `AGENT_ENDPOINT=wss://cp.<alan-adı>/v1/agent` gerekli (VPS IP ve DNS, 4.6).

- **SMTP bilgileri (1.3).** Davet ve e-posta doğrulama postaları için SMTP sunucusu, kullanıcı,
  parola ve gönderen adresi gerekli. Gelene kadar davet bağlantısı panelden kopyalanıyor.
- **`BETTER_AUTH_SECRET` (prod).** `.env.example` içindeki değer yalnızca yerel içindir; prod için
  `openssl rand -base64 32` ile üretilip Kubernetes Secret'ına konmalı (4.6).

- ~~GitHub push yetkisi~~ Çözüldü (2026-11-24): `origin` artık
  `https://skoc10@github.com/skoc10/ape.git` ve push çalışıyor.

## Haftalık Review Notları

### Hafta 1 (ara rapor, 1.1-1.4; nihai hüküm 1.5 incelenince)

Bağımsız reviewer kritik açık bulmadı. Düzeltilenler (2026-11-25):

- **O1:** `TenantConn` artık `scope="function"`; transaction yanıttan önce commit ediliyor (testli).
- **O2:** `safeNextPath` kontrol karakterlerini ve origin dışı hedefleri reddediyor (open redirect).
- **D1:** JWKS hiç yüklenemediyse her istek 503; anahtarlar en geç 1 saatte yeniden çekiliyor.
- **D2:** `app_backend`'in `member` ve `organization` tablo yetkisi kaldırıldı; üyelik yalnızca
  `app.member_role(user, org)` fonksiyonuyla sorgulanıyor (toplu okuma yok).
- **D4:** ayrılmış slug'lar sunucuda `beforeCreateOrganization` hook'unda reddediliyor.

Hafta 4 / v1.1 backlog (kullanıcı kararı, 2026-11-25):

- **D3:** SMTP gelince `requireEmailVerification` ve `requireEmailVerificationOnInvitation` açılacak.
- **D5 (4.6):** web güvenlik başlıkları (CSP, frame-ancestors, nosniff, Referrer-Policy),
  `X-Powered-By` kapatma, prod'da `/docs`, `/openapi.json`, `/metrics` Ingress dışında,
  veritabanı için `sslmode=verify-full`.
- **D6:** kullanıcı silme açılmadan önce `created_by` / `triggered_by` için `ON DELETE SET NULL`.
