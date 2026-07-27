-- Keep normalized match history and prediction outcomes indefinitely while
-- bounding operational logs and duplicated provider payloads.
--
-- This function is intentionally invoked by the backend scheduler instead of
-- pg_cron so deployments do not depend on an optional database extension.

create or replace function public.run_safe_data_retention(
  p_raw_cutoff timestamptz,
  p_api_log_cutoff timestamptz,
  p_max_fixtures integer default 500,
  p_max_api_logs integer default 5000,
  p_dry_run boolean default false
)
returns table (
  dry_run boolean,
  raw_cutoff timestamptz,
  api_log_cutoff timestamptz,
  fixture_candidates bigint,
  fixtures_compacted bigint,
  api_log_candidates bigint,
  api_logs_deleted bigint
)
language plpgsql
set search_path = ''
as $$
declare
  v_fixture_ids bigint[] := array[]::bigint[];
  v_api_log_ids bigint[] := array[]::bigint[];
  v_fixture_candidates bigint := 0;
  v_fixtures_compacted bigint := 0;
  v_api_log_candidates bigint := 0;
  v_api_logs_deleted bigint := 0;
begin
  if p_raw_cutoff is null or p_api_log_cutoff is null then
    raise exception 'Retention cutoffs are required.';
  end if;
  if p_raw_cutoff > now() - interval '365 days' then
    raise exception 'Raw payload retention must be at least 365 days.';
  end if;
  if p_api_log_cutoff > now() - interval '7 days' then
    raise exception 'API request log retention must be at least 7 days.';
  end if;
  if p_max_fixtures not between 1 and 2000 then
    raise exception 'p_max_fixtures must be between 1 and 2000.';
  end if;
  if p_max_api_logs not between 1 and 10000 then
    raise exception 'p_max_api_logs must be between 1 and 10000.';
  end if;

  select coalesce(array_agg(candidate.id order by candidate.fixture_date_utc), array[]::bigint[])
  into v_fixture_ids
  from (
    select fixture.id, fixture.fixture_date_utc
    from public.fixtures as fixture
    where fixture.fixture_date_utc < p_raw_cutoff
      and upper(fixture.status_short) in ('FT', 'AET', 'PEN')
      and (
        fixture.raw_json <> '{}'::jsonb
        or fixture.raw_payload <> '{}'::jsonb
      )
    order by fixture.fixture_date_utc, fixture.id
    limit p_max_fixtures
    for update of fixture skip locked
  ) as candidate;

  v_fixture_candidates := cardinality(v_fixture_ids);

  select coalesce(array_agg(candidate.id order by candidate.requested_at), array[]::bigint[])
  into v_api_log_ids
  from (
    select request.id, request.requested_at
    from public.api_request_logs as request
    where request.requested_at < p_api_log_cutoff
    order by request.requested_at, request.id
    limit p_max_api_logs
    for update of request skip locked
  ) as candidate;

  v_api_log_candidates := cardinality(v_api_log_ids);

  if not p_dry_run then
    update public.fixtures
    set raw_json = '{}'::jsonb,
        raw_payload = '{}'::jsonb
    where id = any(v_fixture_ids)
      and (
        raw_json <> '{}'::jsonb
        or raw_payload <> '{}'::jsonb
      );
    get diagnostics v_fixtures_compacted = row_count;

    delete from public.api_request_logs
    where id = any(v_api_log_ids);
    get diagnostics v_api_logs_deleted = row_count;
  end if;

  return query
  select
    p_dry_run,
    p_raw_cutoff,
    p_api_log_cutoff,
    v_fixture_candidates,
    v_fixtures_compacted,
    v_api_log_candidates,
    v_api_logs_deleted;
end;
$$;

revoke all on function public.run_safe_data_retention(
  timestamptz,
  timestamptz,
  integer,
  integer,
  boolean
) from public, anon, authenticated;

grant execute on function public.run_safe_data_retention(
  timestamptz,
  timestamptz,
  integer,
  integer,
  boolean
) to service_role;

grant delete on table public.api_request_logs to service_role;

comment on function public.run_safe_data_retention(
  timestamptz,
  timestamptz,
  integer,
  integer,
  boolean
) is
  'Compacts duplicated provider JSON and prunes API logs in bounded batches; normalized history and prediction audit rows are never deleted.';
