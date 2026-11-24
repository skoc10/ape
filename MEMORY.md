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

## Kullanıcı Aksiyonu Gerekenler

- **GitHub push yetkisi (1.1'den beri bekliyor).** Bu Mac'in keychain'indeki GitHub kimliği
  `voloagent` ve `skoc10/ape` reposuna yazamıyor (403). `origin` adresi
  `https://skoc10@github.com/skoc10/ape.git` olarak ayarlandı. Terminalde bir kez
  `git push -u origin claude/project-thread-2s1349` çalıştırıp `skoc10` için bir Personal Access
  Token (repo + workflow yetkili) girilmesi yeterli; token keychain'e kaydolur ve sonraki
  push'lar otomatik çalışır. Alternatif: `voloagent` hesabını repoya collaborator eklemek.
  O zamana kadar commit'ler yalnızca yerelde birikiyor ve CI iş akışları GitHub'da doğrulanamadı.

## Haftalık Review Notları

Henüz yok.
