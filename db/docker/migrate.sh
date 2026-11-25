#!/bin/sh
# 1. With superuser access (PGUSER/PGPASSWORD/PGHOST of a superuser): create or refresh
#    the three application roles. Skipped when BOOTSTRAP_ROLES is not "true".
# 2. Apply pending migrations as app_migrator.
set -eu

# The database may still be starting (first install): wait for it instead of failing.
tries=0
until pg_isready -q -h "$PGHOST" -p "${PGPORT:-5432}"; do
  tries=$((tries + 1))
  if [ "$tries" -ge 120 ]; then
    echo "database did not become reachable" >&2
    exit 1
  fi
  sleep 5
done

if [ "${BOOTSTRAP_ROLES:-false}" = "true" ]; then
  echo "bootstrapping roles"
  psql -v ON_ERROR_STOP=1 -q -d postgres \
    -v dbname="$APP_DATABASE" \
    -v migrator_password="$APP_MIGRATOR_PASSWORD" \
    -v auth_password="$APP_AUTH_PASSWORD" \
    -v backend_password="$APP_BACKEND_PASSWORD" \
    -f /db/bootstrap/roles.sql
fi

echo "applying migrations"
exec dbmate --url "postgres://app_migrator:${APP_MIGRATOR_PASSWORD}@${PGHOST}:${PGPORT:-5432}/${APP_DATABASE}?sslmode=${PGSSLMODE:-require}" \
  --migrations-dir /db/migrations --no-dump-schema up
