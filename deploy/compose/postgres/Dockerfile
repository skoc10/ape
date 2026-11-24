# PostgreSQL 16 with pgTAP and pg_prove, used for local dev and CI tenant-isolation tests.
FROM postgres:16-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgtap libtap-parser-sourcehandler-pgtap-perl \
 && apt-get clean
# Creates the three application roles on first start (see db/bootstrap/roles.sql).
COPY init-roles.sh /docker-entrypoint-initdb.d/10-init-roles.sh
