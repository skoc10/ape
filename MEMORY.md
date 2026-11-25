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

### 2.1 Protokol şeması ve kod üretimi (2026-11-25)

- **Tek dosya:** `packages/protocol/protocol.schema.json` içinde `$defs` olarak zarf ve sekiz mesaj
  tipi (`ping` ve `pong` ayrı sayıldı). Üreticiler sabit sürümlü: go-jsonschema v0.24.1 ve
  datamodel-code-generator 0.102.0; `task gen` üretir, `task gen:check` ve CI güncelliği denetler.
- **Zarf elle yazıldı:** payload tipi `type` alanına göre seçildiği için çözümleme
  `agent/internal/protocol/frame.go` ve `apps/api/ape/gateway/protocol.py` içinde. İki taraf da
  bilinmeyen alanı reddeder ve hata mesajında payload içeriğini yansıtmaz.
- **Ortak örnekler:** `packages/protocol/examples/*.json` hem Go hem Python testlerinde çözülüyor.
- **`collect_done.sha256`:** tüm parçaların gzip baytlarının `seq` sırasıyla birleşiminin özeti.
- Dizi öğelerinde `maxLength` kullanılmadı (üretilen Python tipleri sade kalsın diye); toplam
  çerçeve boyutu zaten 512 KiB ile sınırlı.

### 2.2 WebSocket gateway (2026-11-25)

- **Kimlik:** `Authorization: Bearer ape_...`; bağlantı önce kabul edilir, token geçersizse 4401
  ile kapatılır (ajan düz HTTP 403 yerine anlamlı kapanış kodu görsün diye). Oturumun
  `(org_id, cluster_id)` çifti token'dan gelir ve değişmez; mesajlarda tenant alanı yoktur.
- **Kapanış kodları:** 4400 protokol hatası, 4401 yetkisiz/iptal, 4408 hello veya heartbeat
  zaman aşımı, 4409 aynı cluster için yeni bağlantı eskisinin yerini aldı.
- **Heartbeat:** ajan 30 sn'de bir `ping` yollar, gateway `pong` döner; 90 sn sessizlikte 4408.
  Her ping'de token yeniden çözülür (iptal ve süre dolumu en geç 30 sn'de etkili); iptal ucu
  ayrıca açık oturumu anında kapatır.
- **Durum:** `connected` değeri yalnızca `last_seen_at` son 90 sn içindeyse geçerli sayılır
  (API süreci ölürse cluster kendiliğinden `offline` okunur; RLS yüzünden toplu güncelleme yok).
- **Metrikler:** `ape_agent_sessions`, `ape_agent_messages_total{type,direction}`.
- Oturum kaydı bellek içi; plan gereği tek `api` instance'ı varsayılıyor.

### 2.3 Go ajanı: bağlantı katmanı ve telemetri (2026-11-25)

- **Kütüphaneler:** `github.com/coder/websocket`, `prometheus/client_golang`, OpenTelemetry Go SDK
  (OTLP/HTTP yalnızca `OTEL_EXPORTER_OTLP_ENDPOINT` doluysa), `log/slog` JSON (`trace_id`'li).
- **Ayarlar (env):** `APE_ENDPOINT`, `APE_TOKEN` veya `APE_TOKEN_FILE`, `APE_METRICS_ADDR`
  (varsayılan `127.0.0.1:9090`, yani yalnızca pod içi), `APE_ALLOW_INSECURE=true` olmadan `ws://`
  reddedilir; `wss://` için en az TLS 1.3.
- **Yeniden bağlanma:** 1 sn'den 60 sn'ye üstel geri çekilme, yarı aralıkta jitter; sağlıklı
  oturumdan sonra sıfırlanır. 4401 (token reddi) ve 4409 (yerini başka ajan aldı) durumunda
  doğrudan azami beklemeye geçer. Yerelde doğrulandı: API yeniden başlatılınca 2 sn'de bağlandı.
- **Heartbeat:** aralık `hello_ack`'ten gelir; 3 aralık boyunca hiç mesaj gelmezse oturum düşer.
  `ping` içinde son toplama süresi ve hata sayısı taşınır.
- **Tek komut:** ajan yalnızca `collect_request` işler, aynı anda tek toplama; diğer her mesaj
  yok sayılır.

### 2.4 Go ajanı: toplayıcılar ve kaynakta temizlik (2026-11-25)

- **client-go dynamic client** ile sabit bir kaynak tablosu (`internal/collect/resources.go`, 21
  tür) sayfalı listelenir. `collect_request` listeyi daraltabilir ama genişletemez; Secret ve
  ConfigMap tabloda yok. Ajan yalnızca `list` fiilini kullanır (testli).
- **Temizlik:** `managedFields` ve `last-applied-configuration` silinir; tüm container türlerinde
  (init ve ephemeral dahil; Pod, şablonlu iş yükleri, CronJob) `env[].value` her zaman
  `<redacted:len=N>` olur, `valueFrom` kalır; `command`/`args` içinde hassas adlı bayrakların
  değerleri (`--x=deger` ve `--x deger`) ile URL içindeki parolalar maskelenir.
- **Parçalama:** tür başına, bütün nesnelerden oluşan gzip'li JSON dizileri; gzip boyutu 360 KiB'i
  aşarsa ikiye bölünür, tek başına sığmayan nesne atlanır ve `truncated: true` olur.
- **Hata davranışı:** zorunlu bir türün listelenememesi çalışmayı `error` mesajıyla bitirir
  (`forbidden`, `timeout`, `internal`); metrics API'si yoksa ya da bozuksa sessizce atlanır.
- **Namespace hariç tutma** Namespace nesnelerinin kendisine de uygulanır.

### 2.5 Ajan Helm chart'ı ve imajı (2026-11-25)

- **İmaj:** `gcr.io/distroless/static-debian12:nonroot`, uid 65532, yaklaşık 12 MB. Dockerfile hem
  BuildKit (çoklu mimari) hem klasik builder ile çalışır (bu Mac'te buildx yok).
- **Chart `deploy/helm/ape-agent`:** Deployment (`Recreate`, tek replika), ServiceAccount, plandaki
  salt-okur ClusterRole + Binding, Secret (ya da `existingSecret`), NetworkPolicy, isteğe bağlı
  metrics Service + ServiceMonitor. Token dosya olarak mount edilir (`APE_TOKEN_FILE`), env'de değil.
- **Chart kendini korur:** `token`/`existingSecret` yoksa ya da `ws://` uç noktası
  `allowInsecure=true` olmadan verilirse `helm install` anlaşılır bir hatayla durur.
- **NetworkPolicy:** ingress tamamen kapalı (metrics açıksa yalnızca `metrics.allowedNamespace`);
  egress yalnızca DNS ve `egressPorts` (443, 6443). Vanilla NetworkPolicy alan adına göre
  kısıtlayamadığı için isteğe bağlı `controlPlaneCIDRs` var.
- **Probe yok:** `/metrics` varsayılan olarak loopback'te dinlediği için kubelet erişemez; ajan
  ayar hatasında süreçten çıkar, bağlantı sorununda kendisi yeniden dener.
- **Doğrulama:** kind (v1.37) üzerinde `helm install` ile ajan yereldeki API'ye bağlandı.
  `ci/rbac-check.sh` yazma fiili ve `secrets`/`configmaps` erişimi olmadığını kanıtlıyor (kasıtlı
  yetki verilince kırıldığı görüldü); CI'da kind üzerinde çalışıyor.
- **Yayın:** `agent-release.yml`, `agent-v*` etiketi ya da elle tetiklemeyle imajı
  (amd64 + arm64) ve chart'ı `ghcr.io/skoc10` altına OCI olarak iter. Henüz çalıştırılmadı.

### 2.6 Toplama uçtan uca ve ilk maskeleme (2026-11-25)

- **Uç noktalar:** `POST/GET /v1/clusters/{id}/audits`, `GET /v1/audits/{run_id}`. Audit başlatma
  owner/admin'e açık; ajan bağlı değilse ya da bir toplama sürüyorsa 409. Satır doğrudan
  `collecting` durumunda açılır (ayrı bir `queued` adımı yok).
- **Parça işleme (`ape/gateway/collection.py`):** sıra numarası, tür (izinli liste), nesne sayısı,
  toplam boyut ve gzip açılım sınırı (parça başına 64 MiB) denetlenir; `collect_done` içindeki
  parça sayısı, tür sayımları ve SHA-256 gelenle karşılaştırılır. Tutmayan her şey çalışmayı
  `failed` yapar ve hiçbir şey saklanmaz. Ajan kopunca ya da `error` yollayınca da `failed`.
- **Maskeleme (`ape/sanitize`):** bu hafta iki dedektör: `key_name` (hassas adlı alanlar ve
  `{name, value}` çiftleri; `secretKeyRef`, `secretName` gibi yapısal alanlar korunur) ve `pem`.
  Yer tutucu `<secret:tip:hmac4>`; HMAC anahtarı `MASKING_KEY`'den tenant başına türetilir.
  Ajanın `<redacted:len=N>` değerlerine dokunulmaz; işlem deterministik ve idempotent.
- **Ham veri yalnızca bellekte:** her parça thread'de çözülüp maskelenir, diske (geçici dosya) ve
  S3'e yalnızca maskeli kopya yazılır. Anahtar `{org_id}/{run_id}/snapshot.json.gz` ve yalnızca
  `ape/storage` tarafından tenant bağlamından üretilir.
- **Yarım kalan çalışmalar:** 10 dakikadan eski `collecting` satırları yeni audit başlatılırken
  `failed` yapılır (API yeniden başlarsa bellekteki durum kaybolur).
- **Uçtan uca test:** `apps/web/scripts/e2e-collect.mjs` (panel, kind üzerinde helm ile ajan,
  audit, S3'teki snapshot'ta ekili sırların olmadığı kontrolü). Yerelde 248 nesne, 12 parça ile
  geçti; CI'da `e2e` iş akışı.

### 3.1 Kötü manifest seti (2026-11-25)

- `testdata/vulnerable/manifests/` 14 kuralı tetikler (`vuln-shop`, `vuln-batch`); `clean-app`
  negatif kontroldür. Beklenen bulgular `expected.yaml`, ekili sahte sırlar `planted-secrets.txt`.
- `COST-002` (istek gerçek kullanımın 3 katı) metrics-server gerektirdiği için bu sette yok;
  kural birim testleriyle kapsanacak.
- Kural kimlikleri: `SEC-001..005`, `RBAC-001..004`, `NET-001..002`, `COST-001..002`,
  `REL-001..002`.

### 3.2 Deterministik maskelemenin tamamlanması (2026-11-25)

- **Beş dedektör ailesi** (metrik etiketi): `known_format` (AWS, GCP servis hesabı, GitHub, Slack,
  Stripe, JWT, PEM), `connection_string` (URL içindeki parola), `key_name` (hassas adlı alan,
  env, bayrak, başlık ve metin içi `ad=değer`), `entropy`, `pii` (e-posta; dış IP isteğe bağlı,
  varsayılan kapalı). Yer tutucudaki tür daha ayrıntılı: `<secret:aws_key:7f3a>` gibi.
- **Entropi kuralı plandakinden bir adım dar:** 20 karakterden uzun, Shannon > 4,0 **ve** büyük
  harf + küçük harf + rakam içeren dizgiler. Neden: Kubernetes adları kural gereği küçük harf
  olduğu için ad, etiket ve imaj referansları yanlışlıkla maskelenmiyor (hex digest'ler zaten
  en fazla 4,0 bit). Ayrıca `name`, `uid`, `image` gibi kimlik alanlarında entropi çalışmaz.
- **Mevcut yer tutucular korunur:** dedektörler `<secret:...>` ve `<redacted:...>` içine bakmaz;
  ikinci geçiş hiçbir şeyi değiştirmez (korpusta testli).
- **`MaskedPayload`** yalnızca `Masker.mask_payload` ile oluşur; LLM fonksiyonu (3.5) başka tip
  kabul etmeyecek.
- **Korpus:** `tests/sanitize/corpus/` altında 30 YAML + 26 log şablonu. Sahte sırlar dosyalarda
  değil, test anında `fakes.py` içinde sabit tohumdan üretilir (repo secret-scanner'a takılmaz).
  Testler: hiçbir ekili sır çıktıda yok, aynı girdi aynı çıktı, beş ailenin hepsi tetikleniyor.

### 3.3 Kural motoru (2026-11-25)

- **`ape/rules`:** 15 kural saf fonksiyon olarak (`engine.py`), kural metinleri ve varsayılan
  önem dereceleri `model.py` içindeki `RULES` tablosunda (LLM kapalıyken kullanılan şablon
  açıklama ve düzeltme metinleri de burada).
- **Tek bulgu, sahibinde:** bir controller'a ait pod'lar ayrıca raporlanmaz; bulgu Deployment,
  StatefulSet, DaemonSet, Job, CronJob ya da sahipsiz Pod üzerinde ve (kural, kaynak) başına bir
  kez çıkar. `kube-system`, `kube-public`, `kube-node-lease` varsayılan olarak atlanır.
- **Kural yorumları (plan ayrıntı vermiyordu):** SEC-003 `runAsNonRoot` yoksa ve uid belirtilmemişse
  ya da 0 ise; SEC-005 etiketsiz ya da `:latest` (digest varsa geçerli); RBAC-001/004 yerleşik
  (`system:`, `cluster-admin`, `admin`, `edit`, `view`) rolleri atlar; RBAC-004 yalnızca açıkça
  `secrets` yazan kuralları sayar (`*` RBAC-001'in işi); NET-001 yalnızca iş yükü olan
  namespace'ler; COST-001 `requests.cpu`, `requests.memory`, `limits.memory`; COST-002 istek en az
  100m ve kullanımın 3 katından fazlaysa; REL-001 Job ve CronJob için aranmaz; REL-002 tek replika
  ve eşleşen PDB yoksa.
- **Fingerprint:** `sha256(rule_id, kind, namespace, name)`. "Tekrarlayan" işareti saklanmaz,
  okuma sırasında hesaplanır: aynı cluster'ın daha eski bir çalışmasında aynı fingerprint var mı.
- **Doğrulama:** her kural için pozitif ve negatif test; kötü manifest seti tam olarak
  `expected.yaml`'daki bulguları üretiyor, `clean-app` temiz; maskeli sette sonuç aynı.

### 3.4 Worker servisi (2026-11-25)

- **Kuyruk ikinci `SECURITY DEFINER` istisnası:** worker tenant'ı bilmeden RLS altında hiçbir satır
  göremediği için işi `app.claim_audit_run()` verir (`FOR UPDATE SKIP LOCKED`, FIFO). Fonksiyon
  tek satır ve onun `org_id`'sini döndürür; geri kalan her şey `db.tenant(org_id)` altında.
  Yalnızca `app_backend` çağırabilir (pgTAP'ta testli).
- **Kilit zaman aşımı:** `locked_at` 10 dakikadan eskiyse (`WORKER_STALE_AFTER_SECONDS`) çalışma
  yeniden alınabilir. Sağlıklı uzun işler için worker 60 sn'de bir `locked_at`'i yeniler.
- **Zehirli iş koruması (plana ek):** `audit_runs.attempts` sütunu; 3 denemeden sonra çalışma
  `failed` olur, sonsuza kadar yeniden denenmez.
- **Aşamalar:** `rules` (snapshot'ı S3'ten oku, kuralları çalıştır, bulguları yaz), `llm` (3.5),
  `report` (Hafta 4'te dolacak), sonra `completed`. Hata durumunda yalnızca istisna türü saklanır
  ve loglanır (mesaj veri içerebilir).
- **Süreç:** `python -m ape.worker` ayrı süreç, aynı anda tek iş, kendi `/metrics` portu (9091):
  `ape_audit_runs_total{status}`, `ape_audit_stage_seconds{stage}`.

## Kullanıcı Aksiyonu Gerekenler

- **`MASKING_KEY` (prod).** `.env.example` içindeki değer yalnızca yerel içindir; prod için
  `openssl rand -base64 32` ile üretilip Secret'a konmalı ve sonradan değiştirilmemeli
  (değişirse eski ve yeni çalışmaların yer tutucuları eşleşmez). 4.6'da.

- **Ajan imajı ve chart'ının ilk yayını (2.5).** `git tag agent-v0.1.0 && git push origin
  agent-v0.1.0` (ya da Actions sekmesinden `agent-release`) çalıştırılmalı; ardından GitHub'da
  `ape-agent` ve `charts/ape-agent` paketleri müşterilerin çekebilmesi için **public** yapılmalı.

- **Control Plane alan adı (1.5).** Sihirbazdaki helm komutu şu an `ws://localhost:8000/v1/agent`
  gösteriyor. Prod için `AGENT_ENDPOINT=wss://cp.<alan-adı>/v1/agent` gerekli (VPS IP ve DNS, 4.6).

- **SMTP bilgileri (1.3).** Davet ve e-posta doğrulama postaları için SMTP sunucusu, kullanıcı,
  parola ve gönderen adresi gerekli. Gelene kadar davet bağlantısı panelden kopyalanıyor.
- **`BETTER_AUTH_SECRET` (prod).** `.env.example` içindeki değer yalnızca yerel içindir; prod için
  `openssl rand -base64 32` ile üretilip Kubernetes Secret'ına konmalı (4.6).

- ~~GitHub push yetkisi~~ Çözüldü (2026-11-24): `origin` artık
  `https://skoc10@github.com/skoc10/ape.git` ve push çalışıyor.

## Haftalık Review Notları

### Hafta 2 Review: Temiz (2026-11-25)

Bağımsız reviewer `e8f8a32` üzerinde hükmü verdi: kritik veya yüksek bulgu yok, Hafta 3 onaylandı.
Dört orta bulgu review commit'inde testleriyle düzeltildi (ekili sırlar iki kilidi de geçiyordu):

- **O1:** probe ve lifecycle `exec.command` alanları `args` kurallarından geçiyor; `httpHeaders`
  değerleri her zaman redakte. Masker `Authorization`/`Cookie` başlıklarını maskeliyor.
- **O2:** pod `status` içindeki `terminated.message` (log kuyruğu) her zaman redakte.
- **O3:** `kapp`, `kopf`, Rancher manifest kopyası anotasyonları siliniyor; JSON içeren anotasyon
  değerleri redakte; kalan anotasyon ve argüman metinleri `sanitizeText`'ten geçiyor (URL parolası,
  dizgi içi `ad=değer`, `Bearer`/`Basic`).
- **O4:** masker'ın yapısal izin listesi artık düz dizgileri muaf tutmuyor (yalnızca `secretName`,
  `secretNamespace`).

Backlog (düşük, rapor: `reviews/hafta-2-review.md`):

- **D1:** takılan toplama bellekte kalıp yeni audit'i engelliyor; süre sınırı ve `mark_collected`
  dönüş değeri denetimi.
- **D2 (3.2 ile):** açılmış veri için toplam sınır, parça sınırını 16 MiB'e indirme, `max_pods`'u
  sunucuda denetleme.
- **D3:** ajanın hata mesajı loga yazılmasın, yalnızca `code`.
- **D4 (3.2 ile):** `-pPAROLA` ve tireyle başlayan değerler korpusa; `auth` kalıbını daralt
  (`--authorization-mode` gibi değerler şu an redakte oluyor).
- **D5:** `controlPlaneCIDRs` Kubernetes API çıkışını da kısıtlıyor; `checksum/token` düz SHA-256;
  ClusterRole'de kullanılmayan `get` ve `replicasets`.
- **D6 (4.6):** Actions'ı SHA ile sabitle, taban imajı digest ile sabitle, cosign imzası ve SBOM.
- **D7:** kimliği doğrulanmış ajan için çerçeve hız sınırı.

### Hafta 1 Review: Temiz (2026-11-25)

Bağımsız reviewer `6381df9` üzerinde nihai hükmü verdi: kritik veya orta bulgu yok, Hafta 2
onaylandı. Son düşük bulgu (ayrılmış slug'ın `organization/update` ile verilebilmesi)
`beforeUpdateOrganization` hook'u ve uçtan uca testle kapatıldı (D4b; kullanıcı bunu v1.1
backlog'una almak istemişti ama düzeltme o mesajdan önce push edilmişti, `2adaa3a`).

### Hafta 1 ara rapor (1.1-1.4)

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
