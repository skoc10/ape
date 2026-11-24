# PostgreSQL 16 with the pgTAP extension, used for local dev and CI tenant-isolation tests.
FROM postgres:16-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgtap \
 && apt-get clean
