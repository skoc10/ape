-- Tenant isolation tests (pgTAP). Run with `task db:test` as a superuser; the whole
-- file is one transaction that is rolled back, so it never leaves data behind.
begin;
create extension if not exists pgtap;

select no_plan();

-- ---------------------------------------------------------------------------
-- Fixtures (as superuser, bypassing RLS)
-- ---------------------------------------------------------------------------
insert into "user" (id, name, email, "emailVerified") values
  ('t_user_a', 'A', 'a@a.test', true), ('t_user_b', 'B', 'b@b.test', true);
insert into "organization" (id, name, slug, "createdAt") values
  ('t_org_a', 'A', 't-org-a', now()), ('t_org_b', 'B', 't-org-b', now());
insert into "member" (id, "organizationId", "userId", role, "createdAt") values
  ('t_m_a', 't_org_a', 't_user_a', 'owner', now()), ('t_m_b', 't_org_b', 't_user_b', 'owner', now());

insert into clusters (id, org_id, name) values
  ('aaaaaaaa-0000-4000-8000-000000000001', 't_org_a', 'a-cluster'),
  ('bbbbbbbb-0000-4000-8000-000000000001', 't_org_b', 'b-cluster');
insert into agent_tokens (id, org_id, cluster_id, prefix, token_hash, created_by, revoked_at, expires_at) values
  ('aaaaaaaa-0000-4000-8000-000000000002', 't_org_a', 'aaaaaaaa-0000-4000-8000-000000000001', 'ape_aaaa', '\x0a', 't_user_a', null, null),
  ('bbbbbbbb-0000-4000-8000-000000000002', 't_org_b', 'bbbbbbbb-0000-4000-8000-000000000001', 'ape_bbbb', '\x0b', 't_user_b', null, null),
  ('bbbbbbbb-0000-4000-8000-000000000003', 't_org_b', 'bbbbbbbb-0000-4000-8000-000000000001', 'ape_brev', '\x0c', 't_user_b', now(), null),
  ('bbbbbbbb-0000-4000-8000-000000000004', 't_org_b', 'bbbbbbbb-0000-4000-8000-000000000001', 'ape_bexp', '\x0d', 't_user_b', null, now() - interval '1 hour');
insert into audit_runs (id, org_id, cluster_id, triggered_by) values
  ('aaaaaaaa-0000-4000-8000-000000000005', 't_org_a', 'aaaaaaaa-0000-4000-8000-000000000001', 't_user_a'),
  ('bbbbbbbb-0000-4000-8000-000000000005', 't_org_b', 'bbbbbbbb-0000-4000-8000-000000000001', 't_user_b');
insert into findings (id, org_id, audit_run_id, rule_id, category, severity, title, resource_kind, resource_name, evidence, fingerprint) values
  ('aaaaaaaa-0000-4000-8000-000000000006', 't_org_a', 'aaaaaaaa-0000-4000-8000-000000000005', 'SEC-001', 'security', 'high', 'a', 'Pod', 'a', '{}', 'fp-a'),
  ('bbbbbbbb-0000-4000-8000-000000000006', 't_org_b', 'bbbbbbbb-0000-4000-8000-000000000005', 'SEC-001', 'security', 'high', 'b', 'Pod', 'b', '{}', 'fp-b');

-- ---------------------------------------------------------------------------
-- Static guarantees
-- ---------------------------------------------------------------------------
select is(
  (select count(*)::int from pg_class
    where relname in ('organization_settings','clusters','agent_tokens','audit_runs','findings')
      and relnamespace = 'public'::regnamespace and relrowsecurity and relforcerowsecurity),
  5, 'every application table has RLS enabled and forced');
select is((select rolbypassrls or rolsuper from pg_roles where rolname = 'app_backend'), false,
  'app_backend cannot bypass RLS');
select is((select rolbypassrls or rolsuper from pg_roles where rolname = 'app_auth'), false,
  'app_auth cannot bypass RLS');
select is((select rolsuper from pg_roles where rolname = 'app_migrator'), false,
  'app_migrator is not a superuser');
select is((select count(*)::int from organization_settings where org_id in ('t_org_a','t_org_b')), 2,
  'a settings row is created automatically for every new organization');

-- ---------------------------------------------------------------------------
-- app_backend without tenant context: sees nothing, writes nothing
-- ---------------------------------------------------------------------------
set local role app_backend;

select is((select count(*)::int from organization_settings), 0, 'no context: organization_settings is empty');
select is((select count(*)::int from clusters), 0, 'no context: clusters is empty');
select is((select count(*)::int from agent_tokens), 0, 'no context: agent_tokens is empty');
select is((select count(*)::int from audit_runs), 0, 'no context: audit_runs is empty');
select is((select count(*)::int from findings), 0, 'no context: findings is empty');
select throws_ok(
  $$ insert into clusters (org_id, name) values ('t_org_a', 'no-context') $$,
  '42501', null, 'no context: insert is rejected');

-- ---------------------------------------------------------------------------
-- app_backend as tenant A
-- ---------------------------------------------------------------------------
select set_config('app.org_id', 't_org_a', true);

select is((select count(*)::int from organization_settings), 1, 'A sees only its settings row');
select is((select count(*)::int from clusters), 1, 'A sees only its cluster');
select is((select count(*)::int from agent_tokens), 1, 'A sees only its token');
select is((select count(*)::int from audit_runs), 1, 'A sees only its audit run');
select is((select count(*)::int from findings), 1, 'A sees only its finding');

select is((select count(*)::int from organization_settings where org_id = 't_org_b'), 0, 'A reads 0 rows of B: organization_settings');
select is((select count(*)::int from clusters where org_id = 't_org_b'), 0, 'A reads 0 rows of B: clusters');
select is((select count(*)::int from agent_tokens where org_id = 't_org_b'), 0, 'A reads 0 rows of B: agent_tokens');
select is((select count(*)::int from audit_runs where org_id = 't_org_b'), 0, 'A reads 0 rows of B: audit_runs');
select is((select count(*)::int from findings where org_id = 't_org_b'), 0, 'A reads 0 rows of B: findings');

-- cross-tenant inserts fail the WITH CHECK clause
select throws_ok(
  $$ insert into organization_settings (org_id) values ('t_org_b') $$,
  '42501', null, 'A cannot insert into B: organization_settings');
select throws_ok(
  $$ insert into clusters (org_id, name) values ('t_org_b', 'evil') $$,
  '42501', null, 'A cannot insert into B: clusters');
select throws_ok(
  $$ insert into agent_tokens (org_id, cluster_id, prefix, token_hash, created_by)
     values ('t_org_b', 'bbbbbbbb-0000-4000-8000-000000000001', 'ape_evil', '\xee', 't_user_a') $$,
  '42501', null, 'A cannot insert into B: agent_tokens');
select throws_ok(
  $$ insert into audit_runs (org_id, cluster_id, triggered_by)
     values ('t_org_b', 'bbbbbbbb-0000-4000-8000-000000000001', 't_user_a') $$,
  '42501', null, 'A cannot insert into B: audit_runs');
select throws_ok(
  $$ insert into findings (org_id, audit_run_id, rule_id, category, severity, title, resource_kind, resource_name, evidence, fingerprint)
     values ('t_org_b', 'bbbbbbbb-0000-4000-8000-000000000005', 'SEC-001', 'security', 'high', 'x', 'Pod', 'x', '{}', 'fp-evil') $$,
  '42501', null, 'A cannot insert into B: findings');

-- composite foreign keys: an A row cannot hang off a B parent
select throws_ok(
  $$ insert into agent_tokens (org_id, cluster_id, prefix, token_hash, created_by)
     values ('t_org_a', 'bbbbbbbb-0000-4000-8000-000000000001', 'ape_evil', '\xef', 't_user_a') $$,
  '23503', null, 'A token cannot point at a cluster of B');
select throws_ok(
  $$ insert into audit_runs (org_id, cluster_id, triggered_by)
     values ('t_org_a', 'bbbbbbbb-0000-4000-8000-000000000001', 't_user_a') $$,
  '23503', null, 'A audit run cannot point at a cluster of B');
select throws_ok(
  $$ insert into findings (org_id, audit_run_id, rule_id, category, severity, title, resource_kind, resource_name, evidence, fingerprint)
     values ('t_org_a', 'bbbbbbbb-0000-4000-8000-000000000005', 'SEC-001', 'security', 'high', 'x', 'Pod', 'x', '{}', 'fp-evil') $$,
  '23503', null, 'A finding cannot point at an audit run of B');

-- cross-tenant updates and deletes touch nothing
select is_empty($$ update organization_settings set github_repo = 'evil/evil' where org_id = 't_org_b' returning 1 $$, 'A cannot update B: organization_settings');
select is_empty($$ update clusters set name = 'pwned' where org_id = 't_org_b' returning 1 $$, 'A cannot update B: clusters');
select is_empty($$ update agent_tokens set revoked_at = now() where org_id = 't_org_b' returning 1 $$, 'A cannot update B: agent_tokens');
select is_empty($$ update audit_runs set status = 'failed' where org_id = 't_org_b' returning 1 $$, 'A cannot update B: audit_runs');
select is_empty($$ update findings set status = 'ignored' where org_id = 't_org_b' returning 1 $$, 'A cannot update B: findings');
select is_empty($$ delete from clusters where org_id = 't_org_b' returning 1 $$, 'A cannot delete B: clusters');
select is_empty($$ delete from findings where org_id = 't_org_b' returning 1 $$, 'A cannot delete B: findings');

-- moving an own row into another tenant is rejected
select throws_ok(
  $$ update clusters set org_id = 't_org_b' where org_id = 't_org_a' $$,
  '42501', null, 'A cannot move its cluster to B');

-- own-tenant writes work
select lives_ok(
  $$ insert into clusters (org_id, name) values ('t_org_a', 'a-second') $$,
  'A can insert its own cluster');
select is((select count(*)::int from clusters), 2, 'A now sees two clusters');

-- ---------------------------------------------------------------------------
-- switching to tenant B flips the view
-- ---------------------------------------------------------------------------
select set_config('app.org_id', 't_org_b', true);
select is((select count(*)::int from clusters), 1, 'B sees only its cluster');
select is((select count(*)::int from clusters where org_id = 't_org_a'), 0, 'B reads 0 rows of A: clusters');
select is((select count(*)::int from agent_tokens), 3, 'B sees its three tokens');
select is((select count(*)::int from findings where org_id = 't_org_a'), 0, 'B reads 0 rows of A: findings');

-- ---------------------------------------------------------------------------
-- app_backend and the identity tables: membership lookup only
-- ---------------------------------------------------------------------------
select is(app.member_role('t_user_a', 't_org_a'), 'owner', 'app_backend can ask for one membership');
select is(app.member_role('t_user_a', 't_org_b'), null, 'no role in an organization the user is not in');
select throws_ok($$ select 1 from "member" $$, '42501', null, 'app_backend cannot enumerate memberships');
select throws_ok($$ select 1 from "organization" $$, '42501', null, 'app_backend cannot enumerate organizations');
select throws_ok($$ insert into "member" (id, "organizationId", "userId", role, "createdAt") values ('x', 't_org_b', 't_user_a', 'owner', now()) $$,
  '42501', null, 'app_backend cannot add members');
select throws_ok($$ update "organization" set name = 'x' $$, '42501', null, 'app_backend cannot modify organizations');
select throws_ok($$ select 1 from "session" $$, '42501', null, 'app_backend cannot read sessions');
select throws_ok($$ select 1 from "account" $$, '42501', null, 'app_backend cannot read accounts (password hashes)');
select throws_ok($$ select 1 from "jwks" $$, '42501', null, 'app_backend cannot read JWT signing keys');
select throws_ok($$ select 1 from "user" $$, '42501', null, 'app_backend cannot read users');

-- ---------------------------------------------------------------------------
-- agent token resolution: works without tenant context, honours revocation and expiry
-- ---------------------------------------------------------------------------
select set_config('app.org_id', '', true);
select results_eq(
  $$ select org_id, cluster_id from app.resolve_agent_token('\x0a') $$,
  $$ values ('t_org_a'::text, 'aaaaaaaa-0000-4000-8000-000000000001'::uuid) $$,
  'a valid token resolves to its own tenant and cluster');
select is_empty($$ select * from app.resolve_agent_token('\x0c') $$, 'a revoked token does not resolve');
select is_empty($$ select * from app.resolve_agent_token('\x0d') $$, 'an expired token does not resolve');
select is_empty($$ select * from app.resolve_agent_token('\xff') $$, 'an unknown token does not resolve');

-- ---------------------------------------------------------------------------
-- worker queue: one run at a time, with its tenant, and nothing else
-- ---------------------------------------------------------------------------
reset role;
-- Whatever a developer database already has queued is parked for the duration of the test.
update audit_runs set status = 'failed' where status in ('collected', 'analyzing', 'reporting');
update audit_runs set status = 'collected' where id = 'bbbbbbbb-0000-4000-8000-000000000005';
set local role app_backend;
select set_config('app.org_id', '', true);
select is((select count(*)::int from audit_runs), 0, 'the worker sees no runs before claiming');
select results_eq(
  $$ select id, org_id from app.claim_audit_run() $$,
  $$ values ('bbbbbbbb-0000-4000-8000-000000000005'::uuid, 't_org_b'::text) $$,
  'claiming hands out the collected run together with its tenant');
select is_empty($$ select * from app.claim_audit_run() $$, 'a claimed run is not handed out twice');
select is((select count(*)::int from audit_runs), 0, 'claiming does not open other tenants'' rows');
select set_config('app.org_id', 't_org_b', true);
select is((select status from audit_runs where id = 'bbbbbbbb-0000-4000-8000-000000000005'), 'analyzing',
  'the claimed run is marked as analyzing');

-- ---------------------------------------------------------------------------
-- app_auth: identity tables only, no application data at all
-- ---------------------------------------------------------------------------
reset role;
set local role app_auth;
select set_config('app.org_id', 't_org_a', true);

select throws_ok($$ select 1 from organization_settings $$, '42501', null, 'app_auth cannot read organization_settings');
select throws_ok($$ select 1 from clusters $$, '42501', null, 'app_auth cannot read clusters');
select throws_ok($$ select 1 from agent_tokens $$, '42501', null, 'app_auth cannot read agent_tokens');
select throws_ok($$ select 1 from audit_runs $$, '42501', null, 'app_auth cannot read audit_runs');
select throws_ok($$ select 1 from findings $$, '42501', null, 'app_auth cannot read findings');
select throws_ok($$ insert into clusters (org_id, name) values ('t_org_a', 'x') $$, '42501', null, 'app_auth cannot write clusters');
select throws_ok($$ select * from app.resolve_agent_token('\x0a') $$, '42501', null, 'app_auth cannot resolve agent tokens');
select throws_ok($$ select app.member_role('t_user_a', 't_org_a') $$, '42501', null, 'app_auth cannot call app.member_role');
select throws_ok($$ select * from app.claim_audit_run() $$, '42501', null, 'app_auth cannot claim audit runs');
select lives_ok(
  $$ insert into "organization" (id, name, slug, "createdAt") values ('t_org_c', 'C', 't-org-c', now()) $$,
  'app_auth can create an organization (settings trigger runs as its owner)');

reset role;
select is((select count(*)::int from organization_settings where org_id = 't_org_c'), 1,
  'the organization created by app_auth got its settings row');

select * from finish();
rollback;
