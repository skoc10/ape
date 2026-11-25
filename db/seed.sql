-- Local development seed: two tenants (A and B) with one user and one cluster each.
-- Run as app_migrator (bypasses RLS). Users have no password: sign up in the UI for real logins.

insert into "user" (id, name, email, "emailVerified") values
  ('seed_user_a', 'Alice (Tenant A)', 'alice@tenant-a.test', true),
  ('seed_user_b', 'Bob (Tenant B)',   'bob@tenant-b.test',   true)
on conflict do nothing;

insert into "organization" (id, name, slug, "createdAt") values
  ('seed_org_a', 'Tenant A', 'tenant-a', now()),
  ('seed_org_b', 'Tenant B', 'tenant-b', now())
on conflict do nothing;

insert into "member" (id, "organizationId", "userId", role, "createdAt") values
  ('seed_member_a', 'seed_org_a', 'seed_user_a', 'owner', now()),
  ('seed_member_b', 'seed_org_b', 'seed_user_b', 'owner', now())
on conflict do nothing;

insert into clusters (id, org_id, name) values
  ('00000000-0000-4000-8000-00000000000a', 'seed_org_a', 'tenant-a-prod'),
  ('00000000-0000-4000-8000-00000000000b', 'seed_org_b', 'tenant-b-prod')
on conflict do nothing;
