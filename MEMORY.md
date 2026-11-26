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

### 3.5 PydanticAI analiz ajanı ve model soyutlaması (2026-11-25)

- **pydantic-ai-slim 2.x** (`anthropic` ve `openai` ekleriyle). Model `ape/analysis/model.py`
  içinde yalnızca ortam değişkenlerinden kurulur: `LLM_PROVIDER` boşsa LLM kapalı, `anthropic`
  ise `AnthropicModel` (varsayılan `claude-sonnet-5-5`), `openai_compatible` ise `LLM_BASE_URL`
  ile `OpenAIChatModel` (vLLM, Ollama, LiteLLM).
- **Modele giden veri:** bulgu başına kural kanıtı ve ilgili kaynağın `status` ve gürültü metadata'sı
  atılmış, 6000 karakterle sınırlı manifest özeti. Snapshot'ın tamamı gitmez. Modeli çağıran tek
  fonksiyon `MaskedPayload` dışında tip kabul etmez; payload çıkışta bir kez daha maskelenir
  (maskelenmemiş girdiyle bile ekili sırların prompt'a ulaşmadığı testli).
- **Çıktı doğrulama:** bilinmeyen fingerprint atılır; yama yalnızca YAML mapping ise ve hedefi
  bulgunun kendi kaynağıysa tutulur, aksi halde yalnızca yama düşer. Model `severity` ve `title`
  değiştirebilir, bulgu ekleyemez.
- **Dayanıklılık:** 25'lik gruplar; grup hatası (API, şema, zaman aşımı) yalnızca o grubu şablon
  metne düşürür. Çalışma başına token tavanı `LLM_MAX_TOKENS_PER_RUN` (300k); aşılınca kalan
  gruplar şablon alır. LLM kapalı ya da erişilemezken audit yine `completed` olur.
- **Kayıt:** `stats.llm` (model, token, zenginleştirilen/şablon sayısı, atılanlar), `stats.summary`,
  `stats.top_risks`; metrik `ape_llm_tokens_total{model,direction}`. `business_impact`
  ayrı sütun olmadığı için `explanation` metnine eklenir.
- **Prompt injection:** manifest içeriği "veri" olarak işaretli; asıl koruma çıktı doğrulaması ve
  PR'ın insan onayından geçmesi.
- **Çıktı modu `LLM_OUTPUT_MODE=native` (varsayılan):** sağlayıcının JSON şemalı yapılandırılmış
  çıktısı kullanılır. Plandaki tool calling yolu (`tool`) duruyor ama varsayılan değil: yerel
  küçük modeller (qwen2.5 1.5b ve 3b) tool çağrısı üretmeyip boş döndü, `native` ile 3b modeli
  geçerli çıktı verdi; ayrıca güncel Claude modelleri zorlanmış tool seçimini reddediyor.
- **Yerel model denemesi (Ollama, Compose `--profile llm`):** `qwen2.5:3b` ile aynı kod, yalnızca
  ortam değişkenleri değiştirilerek 2 bulguyu 63 sn'de zenginleştirdi (CPU, Colima). 1.5b modeli
  şemayı tutturamadı ve beklenen şekilde şablon metne düşüldü.
- **Doğrulanamayan:** gerçek Claude çağrısı (bu ortamda API anahtarı yok); o yol yalnızca betikli
  modellerle sınandı. `task eval` (iki modelin altın çıktı karşılaştırması)
  henüz yok, backlog.

### 3.6 Bulgu ekranı (2026-11-25)

- `GET /v1/audits/{run_id}/findings?severity=&category=`; "recurring" okuma anında hesaplanır.
- Panel: `/[orgSlug]/audits/[runId]` durum rozeti ve 3 sn'lik yenileme (çalışma sürerken), özet,
  önem ve kategori filtresi (URL parametresi), her bulguda açıklama, düzeltme, yama ve
  "recurring" rozeti. Cluster sayfasındaki audit satırları bu sayfaya bağlanır.
- Uçtan uca test artık worker'ı da kapsıyor: kind üzerindeki kötü manifest seti için beklenen
  18 bulgu birebir çıkıyor, `clean-app` temiz, ekili sırlar ne snapshot'ta ne bulgularda.

### 4.1 Beyaz etiket ayarları (2026-11-25)

- `GET /v1/settings`, `PUT /v1/settings/branding`, `PUT/GET/DELETE /v1/settings/logo`; yazma
  owner/admin. `branding` jsonb: `company_name`, `primary_color` (`#rrggbb`), `logo_key`,
  `logo_content_type`.
- **Logo yalnızca PNG ya da JPEG, en fazla 512 KiB;** tür istemcinin beyanına değil dosyanın ilk
  baytlarına göre belirlenir. SVG bilerek reddedilir (script ve dış referans taşıyabilir ve her
  müşterinin raporuna girer). Nesne anahtarı `{org_id}/branding/logo`, `reports` bucket'ında.
- Panelde `/[orgSlug]/settings/branding`; logo önizlemesi API üzerinden data URI olarak gelir.

### 4.2 Rapor şablonu ve skor (2026-11-25)

- **Skor:** `ape/reports/scoring.toml` (critical 15, high 8, medium 3, low 1, kategori başına en
  fazla 40). `audit_runs.score` ve `stats.score` içine yazılır.
- **Şablon:** `ape/reports/templates/report.html.j2` (Jinja2, autoescape açık). Bölümler: yönetici
  özeti, skor halkası ve kategori çubukları (statik SVG), ilk 5 risk, bulgu tablosu, bulgu
  başına açıklama, düzeltme ve yama. Tek dosya: satır içi CSS, script yok, dış URL yok.
- **Beyaz etiket:** firma adı, renk ve logo `organization_settings.branding`'den; ayar yoksa
  nötr varsayılan ("Platform Engineering", mavi). Ürün adı raporda geçmez (testli). Ajanın kendi
  namespace'i (`ape-system`) bu yüzden bulgulardan hariç tutulur.
- **Sunum:** `GET /v1/audits/{id}/report` HTML'i `Content-Security-Policy: sandbox; default-src
  'none'` ile döner; panel `sandbox=""` iframe içinde `srcDoc` olarak gösterir (rapor metni
  cluster verisi ve LLM çıktısı içerdiği için etkisiz belge olarak ele alınır).

### 4.3 WeasyPrint ile PDF (2026-11-25)

- Aynı HTML, WeasyPrint 70 ile A4 PDF olur; URL fetcher yalnızca `data:` protokolüne izin verir
  (ağ ve dosya erişimi yok, testli). PDF `{org_id}/{run_id}/report.pdf` olarak `reports`
  bucket'ına gider, `report_key` set edilir.
- **İndirme:** `GET /v1/audits/{id}/report/pdf` tenant kontrolünden sonra 5 dakikalık presigned URL
  döner; panelde `/audits/{id}/report.pdf` rotası oturumu doğrulayıp oraya yönlendirir. S3 istemcisi
  SigV4'e sabitlendi (yanıt başlıklı presigned linkler eski imzayla SeaweedFS'te 403 veriyordu).
  Tarayıcının göreceği S3 adresi farklıysa `S3_PUBLIC_ENDPOINT_URL` (4.6).
- **100 bulgu testi:** kırılamayan 400 karakterlik dizgilerle 100 bulguluk rapor A4'e sığıyor,
  Python tarafı bellek tepe değeri 1 GiB'in çok altında; `ape_pdf_render_seconds` ölçüyor.
- **PDF hatası audit'i düşürmez:** render başarısız olursa çalışma yine `completed` olur, HTML
  rapor durur, `stats.report.pdf=false`.
- **Sistem kütüphaneleri:** worker imajına Pango ve DejaVu fontları gerekli (CI'da apt ile);
  yerelde `brew install pango`. macOS'ta Homebrew kütüphane yolu kod içinde eklenir.

### 4.4 GitHub App ve PR (2026-11-25)

- **Kimlik bilgisi yok, mock ile geçildi:** gerçek GitHub App oluşturulmadığı için akışın tamamı
  sahte bir GitHub'a (httpx MockTransport) karşı test edildi. `GITHUB_APP_*` boşken özellik
  kapalı: uçlar 503 döner, panel "Not available yet" gösterir.
- **Kurulum güvenliği (plana ek):** dönüş URL'sindeki `installation_id` saldırgan tarafından
  değiştirilebilir. Bu yüzden (1) imzalı, 15 dakikalık, kullanıcıya ve tenant'a bağlı `state`,
  (2) GitHub OAuth `code` ile `/user/installations` üzerinden "bu kurulum gerçekten bu GitHub
  kullanıcısının mı" doğrulaması yapılır. Aksi halde başkasının kurulumu kendi workspace'ine
  bağlanıp yabancı repoda PR açılabilirdi. Repo seçimi de yalnızca kurulumun kapsadığı repolardan.
- **PR'ı worker değil `api` açıyor (plandan sapma):** iş yalnızca ağ çağrısı ve küçük YAML
  ayrıştırma; bellek yükü yok, kullanıcı sonucu hemen görüyor ve ayrı bir kuyruk durumu
  gerekmiyor. Yalnızca owner/admin'in açık isteğiyle (`POST /v1/audits/{id}/pr`), audit başına bir kez.
- **Yama (`ape/github/patcher.py`):** ruamel.yaml round-trip; yorum, tırnak, sıra ve girinti korunur
  (bayt bayt testli), çok belgeli dosyalar desteklenir. Eşleşme sırası: tam eşleşme, aynı
  dizindeki `kustomization.yaml` namespace'i, tek adaylı `kind + name`; birden fazla aday
  belirsizdir. Başka namespace'i açıkça yazan manifest asla yamalanmaz. Yama `kind`, `name` ve
  `namespace` değiştiremez. Listeler `name` (ve benzeri) anahtarıyla birleştirilir.
- **PR:** tek commit, `audit/<zaman>` dalı (plandaki `ape/audit-<tarih>` yerine: müşteri reposunda
  ürün adı geçmesin), açıklamada özet, skor, rapor bağlantısı, her yamanın eşleşme kuralı ve
  "To apply manually" bölümü. Bulgu metinleri Markdown tablosunu bozamaz, HTML ya da @mention
  taşıyamaz. GitHub hata gövdeleri kullanıcıya ve loga yansıtılmaz.
- Yamalar yalnızca LLM açıkken üretildiği için PR butonu LLM'siz kurulumda görünmez.

### 4.5 Güvenlik kontrol listesi (2026-11-25)

- `docs/SECURITY.md`: 12 güvence, her biri hangi testle ve hangi CI işinde kanıtlandığıyla.
- Yeni testler: `apps/api/tests/test_security_checklist.py` (ajan maskelenmemiş nesne yollasa bile
  API ve worker loglarında nesne içeriği, token ve ekili sır yok; snapshot, LLM prompt'u,
  bulgular ve raporda ekili sır yok) ve ajan tarafında `TestAgentLogsNeverContainObjectContent`.
- **Bulunan ve kapatılan açık:** FastAPI'nin varsayılan 422 yanıtı reddedilen girdiyi geri
  yansıtıyordu; artık yalnızca alan konumu ve hata türü dönüyor.
- **ZDR:** otomatik doğrulanamaz; `docs/SECURITY.md` içindeki tablo kullanıcı tarafından
  doldurulana kadar harici LLM kapalı kalmalı.

### 1.6 ve 4.6 ön hazırlığı: imajlar ve Control Plane chart'ı (2026-11-25)

- **İmajlar:** `apps/api/Dockerfile` tek dosyada iki hedef (`api`, `worker`; Pango ve fontlar
  yalnızca worker'da), `apps/web/Dockerfile` (Next.js standalone), `db/docker/Dockerfile`
  (`ape-migrate`: psql + dbmate + migration'lar). Plandaki `Dockerfile.api`/`Dockerfile.worker`
  yerine tek dosya, çünkü Python paketi ortak.
- **Chart `deploy/helm/ape-control-plane`:** web, api, worker Deployment'ları (non-root, salt-okur
  kök dosya sistemi), CloudNativePG `Cluster` (isteğe bağlı günlük yedek), tek düğümlü SeaweedFS
  StatefulSet'i, migration Job'ı (install/upgrade sonrası hook: süper kullanıcıyla üç rolü
  oluşturur, sonra `app_migrator` ile dbmate), Ingress, isteğe bağlı ServiceMonitor.
- **Dışarıya açık olanlar yalnızca** panel (`/`) ve ajan WebSocket'i (`/v1/agent`), bir de
  `s3.publicHost` verilirse imzalı indirme adresi. REST API, `/metrics`, `/docs` ve veritabanı
  cluster içinde kalır (review D5).
- **Sırlar ilk kurulumda üretilir** (`ape-secrets`: rol parolaları, `BETTER_AUTH_SECRET`,
  `MASKING_KEY`, S3 anahtarları), upgrade'de korunur ve release silinse de kalır.
- **Doğrulama CI'da:** `charts` iş akışındaki `control-plane-install` işi imajları build edip kind
  üzerinde CNPG + chart kurar. Yerelde denenemedi: Mac'in diski doldu (aşağıya bak).
- **Yayın:** `control-plane-release.yml` (`cp-v*` etiketi). Henüz çalıştırılmadı.

### Yerel ortam notu: disk (2026-11-25)

Mac'in diski bu çalışma sırasında tamamen doldu (460 GB'ın 129 MB'ı boş kaldı) ve Colima I/O
hatası verdi. Colima yeniden başlatıldı; bu oturumun eklediği büyük şeyler silindi: Ollama imajı
ve model volume'u (yaklaşık 9 GB), dört Control Plane imajı, Go/uv/lint önbellekleri ve
`apps/web/.next`. Şu an yaklaşık 12 GB boş. Bu makinede büyük imaj build'i ve yerel LLM denemesi
yapılmamalı; ağır doğrulamalar CI'da.

### 4.6 VPS'te k3s dağıtımı (2026-11-25, tamamlandı)

- **Sunucu:** GCP `e2-standard-2` `ape-cp-01` (2 vCPU, 8 GB, 80 GB, Ubuntu 24.04), SSH
  `office-access@35.207.186.10`, kubeconfig `~/.kube/config`. DNS: `cp.skoc.site` ve
  `files.skoc.site` (Cloudflare DNS-only).
- **Yayın:** `agent-v0.1.0` ve `cp-v0.1.0` etiketleri; beş imaj ve iki chart `ghcr.io/skoc10`
  altında, depo public olduğu için paketler de doğrudan public çıktı.
- **Kurulu olanlar:** k3s v1.37.2 (Traefik), cert-manager (ClusterIssuer `letsencrypt`, e-postasız),
  CloudNativePG operatörü, kube-prometheus-stack (Grafana ve Alertmanager kapalı, 15 gün saklama),
  `ape` release'i (chart 0.1.0, değerler sunucuda `~/values-production.yaml`).
- **Doğrulananlar:** Let's Encrypt sertifikası iki alan adı için geçerli; yalnızca HTTPS (HTTP 404);
  dışarıdan `/v1/me`, `/metrics`, `/docs` erişilemiyor; Prometheus `ape-api` ve `ape-worker`
  hedeflerini topluyor (12 hedefin tamamı sağlıklı); WAL arşivleme çalışıyor.
- **Canlı uçtan uca kontrol:** yayınlanan ajan chart'ı VPS'in kendi k3s'ine kuruldu, WSS üzerinden
  bağlandı, audit 376 nesne ve 45 bulguyla tamamlandı, PDF `files.skoc.site` imzalı bağlantısıyla
  indi. `helm install`'dan rapora 18 saniye. Kontrol için açılan kullanıcı, workspace ve ajan
  sonradan silindi (S3'te o çalışmanın snapshot ve rapor nesneleri kaldı).
- **Yedekten geri yükleme denendi:** isteğe bağlı yedek 34 sn'de tamamlandı; ayrı bir cluster'a
  (`ape-pg-restore`) geri yüklendi, satır sayıları ve en yeni kayıt kaynakla birebir, üç rol ve
  5 tabloda zorunlu RLS yerinde; deneme cluster'ı silindi.
- **Kurulumda çıkanlar ve chart 0.1.1'e alınanlar:** Traefik HTTP'yi de sunuyordu; Ingress artık
  `websecure` giriş noktasına sabit (sunucuda değer dosyasıyla, chart'ta varsayılan olarak).
  k3s rehberine kubeconfig, node bekleme döngüsü ve Prometheus bayrakları eklendi.
- **Backlog:** CNPG'nin yerleşik `barmanObjectStore` yöntemi 1.32'de kalkacak, Barman Cloud
  Plugin'e geçilmeli. Yedekler aynı VPS'teki depolamada (makine kaybına karşı korumaz).
  `api` ve `worker` aynı metrik adlarını dışa veriyor (kullanılmayanlar 0). Chart 0.1.1 henüz
  yayınlanmadı (bir sonraki `cp-v*` etiketiyle).

### 4.7 İlk gerçek audit ve skor kalibrasyonu (2026-11-26)

İlk gerçek cluster (AKS, 1050 nesne, 178 bulgu) 0/100 aldı; ilk model (100'den düş, kategori
başına 40 tavan) hacimle tabana vuruyordu. Kararlar:

- **Skor modeli değişti** (`apps/api/ape/reports/scoring.toml`, tek kaynak): her kategori
  0-100 kendi skorunu alır (`100 * half / (half + ceza)`, `half = 20`), toplam ağırlıklı
  ortalamadır (security 30, rbac 25, reliability 20, network 15, cost 10). Aynı kuralın
  tekrarları azalan ağırlıkla sayılır (`factor = 0.5`: bir kural en kötü bulgusunun en fazla
  2 katı ceza üretir). Ceza puanları: critical 25, high 10, medium 3, low 1, info 0.
  Kalibrasyon noktaları: temiz cluster 100, tek critical 83, bu AKS cluster'ı 64, kasıtlı
  bozuk test seti 49. `stats.score` artık `{total, categories}` (eski `deductions` yok).
- **Bilinen platform bileşenleri** (`apps/api/ape/rules/platform.py`): Argo CD, cert-manager,
  ingress-nginx, Prometheus stack, AKS/EKS/GKE bileşenleri vb. için tasarım gereği geniş
  erişim bulguları (yalnızca RBAC-001, RBAC-002, RBAC-004, SEC-002) silinmez; `low` olur ve
  `evidence.platformComponent` ile etiketlenir. Eşleşme yalnızca nesne adının önekiyle
  yapılır (namespace adı yetmez). Bu bir kanıt değil gelenektir: aynı adı taşıyan sahte bir
  nesne de düşük görünür; bu yüzden bulgu hiçbir zaman gizlenmiyor. Bileşenlerin diğer
  bulguları (probe, kaynak, NetworkPolicy) normal önemde kalır.
- **Top risks** (şablon yolu): (önem, kural) başına tek satır, "N resources, for example X".
- Rapor: kategori çubukları artık kategori skorunu ve bulgu sayısını gösterir.
- Eski audit'lerin kayıtlı skoru değişmez; yeni skor için audit yeniden çalıştırılır.
- Backlog: platform listesi ve muafiyetler workspace bazında ayarlanabilir olmalı (v1.1);
  LLM yolu açıkken LLM'in bir platform bulgusunun önemini yükseltmesi mümkün (yalnızca
  düşürme engelli).

### Panel: dark mode (2026-11-26)

- `next-themes` (zaten bağımlılıktı, sonner kullanıyordu): `class` stratejisi, varsayılan
  `system`, seçim tarayıcıda `localStorage`'da (`theme`). Sunucu tarafında kullanıcı ayarı
  yok; cihaz başına tercih yeterli görüldü.
- Seçici (Light / Dark / System) üst çubukta, giriş/kayıt ve onboarding ekranlarında.
- Rapor (iframe ve PDF) bilerek açık temada kalır: beyaz etiketli, yazdırılabilir çıktı.
  Logo önizlemesi de beyaz zeminde kalır.

### GitHub entegrasyonu: gerçek kullanım değerlendirmesi ve "Download fixes" (2026-11-26)

4.7'de görülen engel: GitHub bir App'i organizasyona kurmayı yalnızca owner'a izin verir;
freelancer müşteri reposuna yazabilse de genelde owner değildir. Kararlar:

- **Varsayılan yol artık indirme:** `GET /v1/audits/{id}/fixes` (panelde "Download fixes",
  `/[orgSlug]/audits/[runId]/fixes.zip`). Zip: kaynak başına tek strategic merge patch
  (`patches/<namespace|cluster-scoped>/<kind>-<name>.yaml`, tam kimlikli: apiVersion, kind,
  metadata), `kustomization.example.yaml`, `README.md`. Kurulum, onay ve saklanan kimlik
  bilgisi yok; her git sağlayıcısında çalışır. Kod: `apps/api/ape/reports/fixes.py`.
- Güvenlik: maskeli değer içeren yama zip'e girmez (README'de "Not included"); yamanın
  iddia ettiği kind/ad yok sayılır, denetlenen kaynağın kimliği yazılır; dosya adları
  temizlenir; YAML tırnakları korunur (`yes`/`on` YAML 1.1'de boolean olur). Her üye
  indirebilir (aynı yamalar bulgularda zaten okunabiliyor).
- **Reddedilenler:** App secret'ını panelden yönetmek (platform yöneticisi rolü ve DB'de
  private key gerektirir, asıl engeli çözmez); müşteri başına App (manifest akışı; owner
  engeli aynen kalır, her müşterinin private key'i bizde durur); PAT saklamak.
- **Sıradaki (onaylanan yön, henüz yapılmadı):** kurulumu bağlamadan ayırmak: owner'a
  gönderilebilir kurulum linki, freelancer'ın kendi GitHub yetkisiyle "Bağla" (push
  edebildiği repolar). Sonra: cluster başına repo/klasör eşlemesi, Helm/Kustomize
  farkındalığı, GitLab ve Azure Repos.
- Öneri (kullanıcıya iletildi): App kişisel hesaptan bir GitHub organizasyonuna taşınmalı.

### LLM bütçesi ve şablon yamalar (2026-11-26)

İlk LLM'li gerçek audit (178 bulgu) 300 bin token bütçesini doldurdu: 150 bulgu LLM'den,
28'i şablondan; yalnızca 10 yama çıktı. Kararlar:

- **Seçim** (`select_for_model`, `apps/api/ape/analysis/analyst.py`): model en önemliden
  başlayarak sorulur; platform bileşeni bulguları hiç gönderilmez (düzeltilecek bir şey yok,
  ayrıca LLM'in onların önemini yükseltmesi de böylece kapanır); `low`/`info` bulgulardan
  kural başına en fazla `LLM_LOW_SAMPLES_PER_RULE` (varsayılan 3) gönderilir, kalanı kuralın
  şablon metnini alır. 0 örneklemeyi kapatır. `stats.llm.skipped_by_policy` sayıyı tutar.
- **Manifest tekrarı kalktı:** prompt'ta her kaynağın manifesti `resources` altında bir kez
  gider, bulgular kind/namespace/ad ile başvurur.
- **Şablon yama** (`apps/api/ape/analysis/template_patches.py`): yalnızca değeri her
  workload için aynı olan düzeltme: SEC-004 (`allowPrivilegeEscalation: false`; privileged
  ya da SYS_ADMIN'li container atlanır). LLM kapalıyken ya da yama yazmadığında kullanılır,
  aynı placeholder kontrolünden geçer. Probe (path/port), PDB (yeni kaynak, mevcut yama
  modeli yalnızca bulgunun kendi kaynağını yamalar), kaynak boyutları ve kullanıcı id'si
  bilerek kapsam dışı: değer tahmini gerekir. `stats.llm.template_patches` sayıyı tutar.
- Backlog: PDB ve NetworkPolicy gibi **yeni kaynak** öneren düzeltmeler için zip'e
  "eklenecek manifestler" bölümü; örneklenen düşük bulgular için grup düzeyinde LLM metni.

## Kullanıcı Aksiyonu Gerekenler

- **`ape-secrets` yedeği (4.6).** `kubectl -n ape get secret ape-secrets -o yaml` çıktısı güvenli bir
  yere alınmalı; `MASKING_KEY` ve veritabanı parolaları yeniden üretilemez.
- **Firewall (4.6).** 22 ve 6443 herkese açık; yalnızca kendi IP adres(ler)ine kısıtlanmalı.

- **VPS ve DNS (4.6).** `deploy/k3s/README.md` adımları hazır; gereken: 4 GB+ RAM'li bir VPS ve iki
  A kaydı (ör. `cp.<alan>` ve `files.<alan>`). Ardından `cp-v0.1.0` etiketiyle imajlar ve chart
  yayınlanmalı. Yedekten geri yükleme denemesi ve Prometheus kurulumu da bu adımda.
- **Dogfooding (4.7).** Gerçek bir müşteri cluster'ında uçtan uca röntgen, süre ölçümü.
- **Mac'te disk alanı.** Yaklaşık 12 GB boş; yerel geliştirme için yer açılması iyi olur.

- **ZDR yazılı onayı (4.5).** Harici LLM açılmadan önce `docs/SECURITY.md` içindeki "Zero Data
  Retention" tablosu doldurulmalı (kim, ne zaman, kanıt).

- **GitHub App oluşturma (4.4).** GitHub'da bir App: izinler Contents (read/write), Pull requests
  (read/write), Metadata (read); "Request user authorization (OAuth) during installation" açık;
  Callback URL ve Setup URL `https://<panel-adresi>/github/callback`. Ardından `GITHUB_APP_ID`,
  `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET` ve
  `GITHUB_APP_PRIVATE_KEY` ortam değişkenleri. Sonra gerçek bir test reposunda bir PR denenmeli;
  özellikle bağlantı adımındaki `/user/installations/{id}/repositories` yanıtının `permissions.push`
  alanı gerçek GitHub'da doğrulanmalı (Hafta 4 review O1 yalnızca sahte GitHub'a karşı testli).

- **Claude API anahtarı ve ZDR onayı (3.5).** LLM zenginleştirmesini açmak için worker'a
  `LLM_PROVIDER=anthropic` ve `LLM_API_KEY` verilmeli; müşteri verisi gideceği için hesapta Zero
  Data Retention'ın açık olduğu yazılı olarak doğrulanmalı (4.5 DoD). O zamana kadar raporlar
  şablon açıklamalarla üretiliyor. Alternatif: yerel model (`openai_compatible`).

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

### Hafta 4 Review: Temiz (2026-11-25)

Bağımsız reviewer `3907dc6` üzerinde hükmü verdi: kritik veya yüksek bulgu yok. Üç orta bulgu
review commit'inde testleriyle düzeltildi (rapor: `reviews/hafta-4-review.md`):

- **O1 (GitHub kurulum kapsamı):** `/user/installations` bir kurulumu, kapsadığı tek bir repoda
  ortak çalışan herkese listeler; kurulum token'ı ise bütün repolara yazabilir. Eski kontrolle
  bir repoda okuma yetkisi olan kişi kurulumu kendi workspace'ine bağlayıp bütün (özel) repo
  adlarını görebilir ve yazamadığı repolarda dal ile PR açtırabilirdi. Artık bağlanırken
  `/user/installations/{id}/repositories` ile kişinin **push** yetkisi olan repolar alınıp
  `organization_settings.github_allowed_repos` sütununa yazılıyor (yeni migration
  `20261125000003`); repo listesi, repo seçimi ve PR açma bu listeyle sınırlı. Push yetkisi
  olmayan kişi bağlayamaz. Liste bağlantı anının görüntüsüdür; yetki değişirse yeniden bağlanmalı.
- **O2 (logo boyutu):** 20 KB'lık 13000 x 13000 bir PNG, worker'da PDF üretirken 1,36 GB bellek
  harcatıyordu (ölçüldü); paylaşılan worker'ı her audit'te düşürebilirdi. Logo artık başlıktan
  okunan boyuta göre en fazla 2048 x 2048 (`ape/reports/logo.py`); kontrol hem yüklemede (422)
  hem rapor üretiminde yapılıyor.
- **O3 (patcher):** `metadata: null` ya da eşleme olmayan `metadata` kaynağın adını ve
  namespace'ini silebiliyor/ezebiliyordu; artık "manual" listesine gidiyor. Birleştirme anahtarı
  liste/eşleme olan liste öğesi 500 hatası veriyordu. Yer tutucu (`<secret:..>`) içeren yama PR
  planlanırken de reddediliyor (Hafta 3 D14 kapandı). PR metninde ters eğik çizgi ve köşeli
  parantez kaçışı eklendi (tablo bozma ve bağlantı ekleme).

Backlog (düşük):

- **D15 (kapatıldı, 2026-11-25):** rapor bağlantısı PR açıklamasından ve `PUBLIC_WEB_URL` ayarı
  koddan kaldırıldı. Önceki durum: PR açıklamasındaki rapor bağlantısı `PUBLIC_WEB_URL/audits/<id>`; gerçek yol
  `/<orgSlug>/audits/<id>`, yani bağlantı 404 verir. Ayrıca müşterinin reposuna panel alan adını
  yazar (beyaz etiket). Öneri: bağlantıyı kaldır ya da kiracıya özel bir adres ayarı ekle.
- **D16:** eşzamanlı iki `POST /audits/{id}/pr` iki PR açabilir (`set_pr_url` sonucu yok
  sayılıyor); PR açıldıktan sonra DB yazımı başarısız olursa PR sahipsiz kalır.
- **D17:** `read_manifests` dosyaları istek içinde sırayla okuyor (en çok 300 çağrı) ve ağacın
  `truncated` alanına bakmıyor; büyük repolarda istek zaman aşımına uğrayabilir.
- **D18:** `state` tek kullanımlık değil (15 dakika, aynı kullanıcı ve tenant ile sınırlı).
- **D19:** `github_allowed_repos` yenilenmiyor; "Disconnect" App'i GitHub'dan kaldırmıyor.
- **D20:** `weasyprint>=62` alt sınırı test edilen 70.0'a çekilmeli.
- **D21:** `test_security_checklist.py` son satırdaki `[:0]` dilimi iddiayı boşa çıkarıyor.
- **D22:** 422 yanıtındaki `msg`, UUID hatalarında girdiden tek karakter taşıyabiliyor.
- Hafta 3'ten açık kalanlar: D8, D9, D10, D11, D12, D13.

### Hafta 3 Review: Temiz (2026-11-25)

Bağımsız reviewer `62fe64f` üzerinde hükmü verdi: kritik veya yüksek bulgu yok, Hafta 4 onaylandı.
Beş orta bulgu review commit'inde testleriyle düzeltildi (rapor: `reviews/hafta-3-review.md`):

- **O1 (entropi kuralı):** "büyük + küçük + rakam" şartı tek harf düzenli anahtarları (base32,
  base36), rakamsız rastgele harfleri ve hex anahtarları masum adlar altında kaçırıyordu; 21-25
  karakterlik dizgilerde 4,0 eşiği de rastgele token'ların önemli bir kısmını geçiriyordu.
  Yeni kural: eşik kısa dizgilerde `0,85 x log2(uzunluk)`'a iner; ayraçsız harf-rakam parçaları
  sınıf geçişlerine göre değerlendirilir; 32+ karakterlik hex ayrı dedektör (`<secret:hex:..>`).
  Muafiyet: `sha256:` önekli değerler ve `checksum`, `hash`, `digest`, `commit`, `revision`,
  `fingerprint`, `uid`, `...id` adlı alanlar. Kalan bilinen boşluk: yalnızca küçük harften oluşan
  rastgele dizgiler (sözcükten ayırt edilemiyor).
- **O2 (metin içi değerler):** URL parolasında `@` varsa kuyruğu, tırnaklı değerde boşluktan
  sonrası sızıyordu; ikisi de bütün olarak maskeleniyor. Ek olarak: `["--password", "x"]` gibi
  ayrı liste öğeleri, `auth` (registry girişi) ve `pwd` adları, ön imzalı URL imzaları (`sig=`),
  `mysql -pPAROLA`.
- **O3 (LLM önem derecesi):** model önem derecesini yükseltebilir ama kuralın altına indiremez
  (manifest içindeki metin bulguyu "info"ya çektiremez). Sayaç: `stats.llm.ignored_severity_downgrades`.
- **O4 (yer tutuculu yama):** içinde `<secret:..>` ya da `<redacted:..>` geçen yama atılır; PR'a
  girseydi müşterinin gerçek değerini yer tutucuyla ezerdi. Plandaki "yama yer tutucu içerebilir"
  notu bu yönde daraltıldı.
- **O5 (heartbeat):** tek bir heartbeat hatası başarılı bir analizi ve worker sürecini düşürüyordu;
  artık yalnızca loglanıyor.

Backlog (düşük):

- **D8 (4.6):** `ape-worker` imajı yok (API için Dockerfile da yok); 3.4 DoD'unun "imaj" kısmı açık.
- **D9:** `app.claim_audit_run` parametreleri çağırandan geliyor; fonksiyon içinde alt sınır koy
  (`p_stale_after >= 1 dakika`, `p_max_attempts >= 1`). Geri alınan işte eski worker'ı durduran
  bir sahiplik belirteci (`locked_by`) yok.
- **D10:** `stats.findings` sayıları kural önem derecesinden, liste LLM'in yükselttiği dereceden;
  rapor (4.1) tek kaynaktan saymalı.
- **D11:** AWS ARN'leri ve rol adları entropi kuralına takılıyor (güvenli yönde hata, LLM
  bağlamını zayıflatır). Tırnaksız boşluklu değerler (`password: correct horse`) ve `/` içeren
  URL parolaları hâlâ kısmen açık.
- **D12:** başarısız ve bütçeyi aşan LLM çağrılarının token'ları sayılmıyor; `explanation`,
  `remediation` ve `patch.content` için uzunluk sınırı yok.
- **D13:** sahibi toplanmayan bir tür olan pod'lar (Argo Rollout vb.) hiçbir kuralda görünmüyor.
- **D14 (4.3'ten önce):** PR açılırken yama hedefi ve içeriği yeniden doğrulanmalı (yalnızca
  bulgunun kaynağı, yer tutucu yok); `task eval` ve gerçek Claude çağrısı hâlâ sınanmadı.

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
