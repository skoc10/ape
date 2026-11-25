-- Cluster-level bootstrap, run once by a superuser before any migration:
--   psql -v ON_ERROR_STOP=1 -v dbname=ape \
--        -v migrator_password=... -v auth_password=... -v backend_password=... -f roles.sql
-- Idempotent: re-running only refreshes passwords and grants.
--
--   app_migrator  owns the schema, BYPASSRLS, used only by the dbmate migration job
--   app_auth      Better Auth inside Next.js: identity tables only
--   app_backend   api and worker: application tables, always subject to RLS

select 'create role app_migrator' where not exists (select from pg_roles where rolname = 'app_migrator') \gexec
select 'create role app_auth'     where not exists (select from pg_roles where rolname = 'app_auth') \gexec
select 'create role app_backend'  where not exists (select from pg_roles where rolname = 'app_backend') \gexec

alter role app_migrator login nosuperuser nocreatedb nocreaterole noreplication bypassrls   password :'migrator_password';
alter role app_auth     login nosuperuser nocreatedb nocreaterole noreplication nobypassrls password :'auth_password';
alter role app_backend  login nosuperuser nocreatedb nocreaterole noreplication nobypassrls password :'backend_password';

alter database :"dbname" owner to app_migrator;
revoke all on database :"dbname" from public;
grant connect on database :"dbname" to app_auth, app_backend;

\connect :"dbname"
alter schema public owner to app_migrator;
revoke create on schema public from public;
grant usage on schema public to app_auth, app_backend;
