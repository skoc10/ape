-- migrate:up

create schema app;
revoke all on schema app from public;
grant usage on schema app to app_backend;

-- Tenant context: only the org_id given with set_config at the start of a transaction.
create function app.current_org_id() returns text
language sql stable as $$
  select nullif(current_setting('app.org_id', true), '')
$$;
revoke all on function app.current_org_id() from public;
grant execute on function app.current_org_id() to app_backend;

create table organization_settings (
  org_id                 text primary key references "organization"(id) on delete cascade,
  branding               jsonb not null default '{}',   -- logo_key, primary_color, company_name
  github_installation_id bigint,
  github_repo            text,                          -- owner/name
  manifest_path          text default '/',
  updated_at             timestamptz not null default now()
);

create table clusters (
  id            uuid primary key default gen_random_uuid(),
  org_id        text not null references "organization"(id) on delete cascade,
  name          text not null check (length(name) between 1 and 63),
  status        text not null default 'pending' check (status in ('pending','connected','offline')),
  k8s_version   text,
  agent_version text,
  last_seen_at  timestamptz,
  created_at    timestamptz not null default now(),
  unique (id, org_id),
  unique (org_id, name)
);

create table agent_tokens (
  id           uuid primary key default gen_random_uuid(),
  org_id       text not null,
  cluster_id   uuid not null,
  prefix       text not null,                   -- 'ape_' + first 8 chars, for display only
  token_hash   bytea not null unique,           -- sha256(token); the token itself is never stored
  created_by   text not null references "user"(id),
  expires_at   timestamptz,
  revoked_at   timestamptz,
  last_used_at timestamptz,
  created_at   timestamptz not null default now(),
  foreign key (cluster_id, org_id) references clusters(id, org_id) on delete cascade
);
create index on agent_tokens (cluster_id);

create table audit_runs (
  id            uuid primary key default gen_random_uuid(),
  org_id        text not null,
  cluster_id    uuid not null,
  status        text not null default 'queued' check (status in
                ('queued','collecting','collected','analyzing','reporting','completed','failed')),
  triggered_by  text not null references "user"(id),
  score         int check (score between 0 and 100),
  stats         jsonb not null default '{}',     -- resource counts, token and timing info
  snapshot_key  text,                            -- S3: {org_id}/{run_id}/snapshot.json.gz (masked)
  report_key    text,                            -- S3: {org_id}/{run_id}/report.pdf
  pr_url        text,
  error         text,
  locked_at     timestamptz,                     -- worker queue
  created_at    timestamptz not null default now(),
  finished_at   timestamptz,
  unique (id, org_id),
  foreign key (cluster_id, org_id) references clusters(id, org_id) on delete cascade
);
create index on audit_runs (cluster_id);
create index on audit_runs (created_at) where status = 'collected';  -- worker job queue

create table findings (
  id             uuid primary key default gen_random_uuid(),
  org_id         text not null,
  audit_run_id   uuid not null,
  rule_id        text not null,                  -- e.g. SEC-001
  category       text not null check (category in ('security','rbac','network','cost','reliability')),
  severity       text not null check (severity in ('critical','high','medium','low','info')),
  title          text not null,
  resource_kind  text not null,
  resource_ns    text,
  resource_name  text not null,
  evidence       jsonb not null,                 -- masked data only
  explanation    text,                           -- LLM
  remediation    text,                           -- LLM
  patch          text,                           -- LLM, YAML patch
  fingerprint    text not null,                  -- sha256(rule_id, kind, ns, name): finds repeats across runs
  status         text not null default 'open' check (status in ('open','accepted','ignored')),
  created_at     timestamptz not null default now(),
  unique (audit_run_id, fingerprint),
  foreign key (audit_run_id, org_id) references audit_runs(id, org_id) on delete cascade
);
create index on findings (org_id, fingerprint);   -- "was it there in the previous run" query

-- Row level security: the same pattern on every application table. FORCE makes the
-- table owner subject to the policies as well; only BYPASSRLS roles (app_migrator) skip them.
alter table organization_settings enable row level security;
alter table organization_settings force row level security;
create policy tenant_rw on organization_settings for all to app_backend
  using (org_id = app.current_org_id())
  with check (org_id = app.current_org_id());

alter table clusters enable row level security;
alter table clusters force row level security;
create policy tenant_rw on clusters for all to app_backend
  using (org_id = app.current_org_id())
  with check (org_id = app.current_org_id());

alter table agent_tokens enable row level security;
alter table agent_tokens force row level security;
create policy tenant_rw on agent_tokens for all to app_backend
  using (org_id = app.current_org_id())
  with check (org_id = app.current_org_id());

alter table audit_runs enable row level security;
alter table audit_runs force row level security;
create policy tenant_rw on audit_runs for all to app_backend
  using (org_id = app.current_org_id())
  with check (org_id = app.current_org_id());

alter table findings enable row level security;
alter table findings force row level security;
create policy tenant_rw on findings for all to app_backend
  using (org_id = app.current_org_id())
  with check (org_id = app.current_org_id());

-- Role grants. app_auth gets nothing here: it cannot reach application tables at all.
revoke all on organization_settings, clusters, agent_tokens, audit_runs, findings from public;
grant select, insert, update, delete on
  organization_settings, clusters, agent_tokens, audit_runs, findings
to app_backend;
grant select on "organization", "member" to app_backend;   -- membership check only

-- Agent token resolution: the single, narrow RLS exception.
-- Owned by app_migrator (BYPASSRLS), so it can read across tenants; only app_backend may call it.
create function app.resolve_agent_token(p_hash bytea)
returns table (org_id text, cluster_id uuid)
language sql security definer set search_path = public, pg_temp as $$
  update agent_tokens t set last_used_at = now()
  where t.token_hash = p_hash and t.revoked_at is null
    and (t.expires_at is null or t.expires_at > now())
  returning t.org_id, t.cluster_id
$$;
revoke all on function app.resolve_agent_token(bytea) from public;
grant execute on function app.resolve_agent_token(bytea) to app_backend;

-- Every new organization gets an empty settings row. Better Auth (app_auth) cannot write
-- application tables, so this runs as the function owner through a trigger.
create function app.create_organization_settings() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  insert into organization_settings (org_id) values (new.id) on conflict do nothing;
  return new;
end
$$;
revoke all on function app.create_organization_settings() from public;

create trigger organization_settings_default
  after insert on "organization"
  for each row execute function app.create_organization_settings();

-- migrate:down

drop trigger if exists organization_settings_default on "organization";
drop table if exists findings, audit_runs, agent_tokens, clusters, organization_settings cascade;
drop schema if exists app cascade;
