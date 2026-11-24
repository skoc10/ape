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

## Kullanıcı Aksiyonu Gerekenler

Şu an için yok.

## Haftalık Review Notları

Henüz yok.
