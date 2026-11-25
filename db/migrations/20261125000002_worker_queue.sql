-- migrate:up

-- How many times a worker has picked this run up. A run that keeps killing its worker
-- (for example by exhausting memory) must not be retried forever.
alter table audit_runs add column attempts int not null default 0;

-- The worker queue. The worker cannot see runs before it knows their tenant (RLS), so
-- claiming is the second narrow SECURITY DEFINER exception: it hands out exactly one run
-- at a time, together with its org_id, and the worker does everything else under that
-- tenant's context.
--
-- Claimable: runs that finished collecting, and runs whose worker stopped reporting
-- progress for p_stale_after (crashed or was killed). FOR UPDATE SKIP LOCKED lets several
-- workers poll without blocking each other or taking the same run.
create function app.claim_audit_run(
  p_stale_after interval default interval '10 minutes',
  p_max_attempts int default 3
) returns table (id uuid, org_id text, cluster_id uuid)
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  update audit_runs r
     set status = 'failed', finished_at = now(), locked_at = null,
         error = 'analysis did not finish after ' || p_max_attempts || ' attempts'
   where r.status in ('analyzing', 'reporting')
     and r.locked_at < now() - p_stale_after
     and r.attempts >= p_max_attempts;

  return query
  update audit_runs r
     set status = 'analyzing', locked_at = now(), attempts = r.attempts + 1
   where r.id = (
           select c.id from audit_runs c
            where c.status = 'collected'
               or (c.status in ('analyzing', 'reporting') and c.locked_at < now() - p_stale_after)
            order by c.created_at
            for update skip locked
            limit 1)
  returning r.id, r.org_id, r.cluster_id;
end
$$;
revoke all on function app.claim_audit_run(interval, int) from public;
grant execute on function app.claim_audit_run(interval, int) to app_backend;

create index audit_runs_in_progress_idx on audit_runs (locked_at)
  where status in ('analyzing', 'reporting');

-- migrate:down

drop index if exists audit_runs_in_progress_idx;
drop function if exists app.claim_audit_run(interval, int);
alter table audit_runs drop column if exists attempts;
