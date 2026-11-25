#!/bin/sh
# Runs once, when the data directory is first initialised. `task db:bootstrap`
# applies the same SQL to an existing database.
set -eu
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -v dbname="$POSTGRES_DB" \
  -v migrator_password="$APP_MIGRATOR_PASSWORD" \
  -v auth_password="$APP_AUTH_PASSWORD" \
  -v backend_password="$APP_BACKEND_PASSWORD" \
  -f /db/bootstrap/roles.sql
