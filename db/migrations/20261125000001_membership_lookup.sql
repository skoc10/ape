-- migrate:up

-- app_backend could read every tenant's memberships and organizations, because the
-- Better Auth tables are outside RLS. It only ever needs one answer: "what is this
-- user's role in this organization?". Replace the table grants with that question.
revoke select on "organization", "member" from app_backend;

create function app.member_role(p_user_id text, p_org_id text) returns text
language sql stable security definer set search_path = public, pg_temp as $$
  select m.role from "member" m
  where m."userId" = p_user_id and m."organizationId" = p_org_id
$$;
revoke all on function app.member_role(text, text) from public;
grant execute on function app.member_role(text, text) to app_backend;

-- migrate:down

drop function if exists app.member_role(text, text);
grant select on "organization", "member" to app_backend;
