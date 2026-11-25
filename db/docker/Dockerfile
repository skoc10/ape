# Migration job image: creates the three roles (when superuser access is given) and
# applies the dbmate migrations as app_migrator.
#   docker build -f db/docker/Dockerfile -t ape-migrate db
FROM ghcr.io/amacneil/dbmate:2 AS dbmate

FROM postgres:16-alpine
COPY --from=dbmate /usr/local/bin/dbmate /usr/local/bin/dbmate
COPY bootstrap /db/bootstrap
COPY migrations /db/migrations
COPY docker/migrate.sh /usr/local/bin/migrate.sh
USER 70:70
ENTRYPOINT ["/bin/sh", "/usr/local/bin/migrate.sh"]
