-- Private, append-oriented history of AI calibration attempts.
--
-- A row represents one attempt. Retrying a terminal attempt must insert the
-- next attempt_number instead of overwriting the previous result. Replaying
-- the same job uses idempotency_key to address the same row.
create table public.prediction_calibrations (
  id uuid primary key default gen_random_uuid(),
  fixture_id bigint not null references public.fixtures(id) on delete cascade,
  attempt_number integer not null,
  idempotency_key text not null,
  input_hash text not null,
  output_hash text,
  provider text not null default 'openai',
  model text not null,
  reasoning_effort text not null,
  prompt_version text not null,
  schema_version text not null,
  status text not null default 'pending',
  published boolean not null default false,
  base_home_win_probability numeric(6, 5) not null,
  base_draw_probability numeric(6, 5) not null,
  base_away_win_probability numeric(6, 5) not null,
  adjusted_home_win_probability numeric(6, 5),
  adjusted_draw_probability numeric(6, 5),
  adjusted_away_win_probability numeric(6, 5),
  input_snapshot jsonb not null default '{}'::jsonb,
  analysis jsonb not null default '{}'::jsonb,
  safe_message text,
  reason_code text,
  safe_error_message text,
  response_id text,
  input_tokens integer,
  cached_input_tokens integer,
  output_tokens integer,
  reasoning_tokens integer,
  total_tokens integer,
  estimated_cost_usd numeric(16, 8),
  duration_ms integer,
  base_prediction_updated_at timestamptz not null,
  retry_after timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  generated_at timestamptz,
  published_at timestamptz,
  superseded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint prediction_calibrations_fixture_attempt_key unique (
    fixture_id,
    attempt_number
  ),
  constraint prediction_calibrations_idempotency_key_key unique (
    idempotency_key
  ),
  constraint prediction_calibrations_attempt_number_valid check (
    attempt_number between 1 and 1000
  ),
  constraint prediction_calibrations_idempotency_key_sha256 check (
    idempotency_key ~ '^[0-9a-f]{64}$'
  ),
  constraint prediction_calibrations_input_hash_sha256 check (
    input_hash ~ '^[0-9a-f]{64}$'
  ),
  constraint prediction_calibrations_output_hash_sha256 check (
    output_hash is null or output_hash ~ '^[0-9a-f]{64}$'
  ),
  constraint prediction_calibrations_provider_not_blank check (
    char_length(provider) between 1 and 50
    and btrim(provider) <> ''
  ),
  constraint prediction_calibrations_model_not_blank check (
    char_length(model) between 1 and 100
    and btrim(model) <> ''
  ),
  constraint prediction_calibrations_reasoning_effort_valid check (
    reasoning_effort in ('none', 'low', 'medium', 'high', 'xhigh', 'max')
  ),
  constraint prediction_calibrations_prompt_version_not_blank check (
    char_length(prompt_version) between 1 and 100
    and btrim(prompt_version) <> ''
  ),
  constraint prediction_calibrations_schema_version_not_blank check (
    char_length(schema_version) between 1 and 100
    and btrim(schema_version) <> ''
  ),
  constraint prediction_calibrations_status_valid check (
    status in ('pending', 'processing', 'updated', 'unavailable', 'error')
  ),
  constraint prediction_calibrations_base_home_probability_range check (
    base_home_win_probability between 0.00000 and 1.00000
  ),
  constraint prediction_calibrations_base_draw_probability_range check (
    base_draw_probability between 0.00000 and 1.00000
  ),
  constraint prediction_calibrations_base_away_probability_range check (
    base_away_win_probability between 0.00000 and 1.00000
  ),
  constraint prediction_calibrations_base_probability_sum check (
    base_home_win_probability
      + base_draw_probability
      + base_away_win_probability = 1.00000
  ),
  constraint prediction_calibrations_adjusted_probability_presence check (
    (
      adjusted_home_win_probability is null
      and adjusted_draw_probability is null
      and adjusted_away_win_probability is null
    )
    or
    (
      adjusted_home_win_probability is not null
      and adjusted_draw_probability is not null
      and adjusted_away_win_probability is not null
    )
  ),
  constraint prediction_calibrations_adjusted_probability_ranges check (
    adjusted_home_win_probability is null
    or (
      adjusted_home_win_probability between 0.00000 and 1.00000
      and adjusted_draw_probability between 0.00000 and 1.00000
      and adjusted_away_win_probability between 0.00000 and 1.00000
    )
  ),
  constraint prediction_calibrations_adjusted_probability_sum check (
    adjusted_home_win_probability is null
    or (
      adjusted_home_win_probability
        + adjusted_draw_probability
        + adjusted_away_win_probability = 1.00000
    )
  ),
  constraint prediction_calibrations_analysis_object check (
    jsonb_typeof(analysis) = 'object'
  ),
  constraint prediction_calibrations_input_snapshot_object check (
    jsonb_typeof(input_snapshot) = 'object'
  ),
  constraint prediction_calibrations_safe_message_valid check (
    safe_message is null
    or (
      char_length(btrim(safe_message)) between 1 and 500
      and safe_message !~* (
        'sk-[[:alnum:]_-]{10,}'
        || '|authorization[[:space:]]*:'
        || '|bearer[[:space:]]+[[:alnum:]_.-]{10,}'
      )
    )
  ),
  constraint prediction_calibrations_reason_code_valid check (
    reason_code is null
    or (
      char_length(reason_code) between 1 and 100
      and reason_code ~ '^[a-z0-9_]+$'
    )
  ),
  constraint prediction_calibrations_safe_error_message_valid check (
    safe_error_message is null
    or (
      char_length(btrim(safe_error_message)) between 1 and 500
      and safe_error_message !~* (
        'sk-[[:alnum:]_-]{10,}'
        || '|authorization[[:space:]]*:'
        || '|bearer[[:space:]]+[[:alnum:]_.-]{10,}'
      )
    )
  ),
  constraint prediction_calibrations_response_id_valid check (
    response_id is null
    or (
      char_length(response_id) between 1 and 255
      and btrim(response_id) <> ''
    )
  ),
  constraint prediction_calibrations_usage_nonnegative check (
    (input_tokens is null or input_tokens >= 0)
    and (cached_input_tokens is null or cached_input_tokens >= 0)
    and (output_tokens is null or output_tokens >= 0)
    and (reasoning_tokens is null or reasoning_tokens >= 0)
    and (total_tokens is null or total_tokens >= 0)
    and (estimated_cost_usd is null or estimated_cost_usd >= 0)
    and (duration_ms is null or duration_ms >= 0)
  ),
  constraint prediction_calibrations_cached_tokens_consistent check (
    cached_input_tokens is null
    or (
      input_tokens is not null
      and cached_input_tokens <= input_tokens
    )
  ),
  constraint prediction_calibrations_reasoning_tokens_consistent check (
    reasoning_tokens is null
    or (
      output_tokens is not null
      and reasoning_tokens <= output_tokens
    )
  ),
  constraint prediction_calibrations_total_tokens_consistent check (
    total_tokens is null
    or (
      input_tokens is not null
      and output_tokens is not null
      and total_tokens >= input_tokens + output_tokens
    )
  ),
  constraint prediction_calibrations_status_payload_consistent check (
    (
      status = 'pending'
      and started_at is null
      and completed_at is null
      and generated_at is null
      and adjusted_home_win_probability is null
    )
    or
    (
      status = 'processing'
      and started_at is not null
      and completed_at is null
      and generated_at is null
      and adjusted_home_win_probability is null
    )
    or
    (
      status = 'updated'
      and started_at is not null
      and completed_at is not null
      and generated_at is not null
      and adjusted_home_win_probability is not null
      and output_hash is not null
      and analysis <> '{}'::jsonb
      and safe_error_message is null
    )
    or
    (
      status in ('unavailable', 'error')
      and completed_at is not null
      and generated_at is null
      and adjusted_home_win_probability is null
    )
  ),
  constraint prediction_calibrations_terminal_reason_consistent check (
    (
      status = 'unavailable'
      and reason_code is not null
      and safe_message is not null
      and safe_error_message is null
    )
    or
    (
      status = 'error'
      and reason_code is not null
      and safe_message is not null
      and safe_error_message is not null
    )
    or status in ('pending', 'processing', 'updated')
  ),
  constraint prediction_calibrations_publication_consistent check (
    (
      status = 'updated'
      and (
        (
          published
          and published_at is not null
          and superseded_at is null
        )
        or (
          not published
          and (
            (
              published_at is null
              and superseded_at is null
            )
            or (
              published_at is not null
              and superseded_at is not null
              and superseded_at >= published_at
            )
          )
        )
      )
    )
    or (
      status <> 'updated'
      and
      not published
      and published_at is null
      and superseded_at is null
    )
  ),
  constraint prediction_calibrations_retry_after_consistent check (
    retry_after is null or status = 'pending'
  ),
  constraint prediction_calibrations_timestamps_ordered check (
    updated_at >= created_at
    and (started_at is null or started_at >= created_at)
    and (
      completed_at is null
      or started_at is null
      or completed_at >= started_at
    )
    and (
      generated_at is null
      or completed_at is null
      or generated_at <= completed_at
    )
    and (
      published_at is null
      or generated_at is null
      or published_at >= generated_at
    )
    and (
      superseded_at is null
      or published_at is null
      or superseded_at >= published_at
    )
  )
);

-- Fast path for idempotent lookup and chronological attempt history.
create index prediction_calibrations_fixture_input_idx
  on public.prediction_calibrations (
    fixture_id,
    input_hash,
    model,
    reasoning_effort,
    attempt_number desc
  );

create index prediction_calibrations_fixture_created_idx
  on public.prediction_calibrations (fixture_id, created_at desc);

-- At most one calibration is currently published for a fixture. Older attempts
-- remain available as history after their published flag is cleared.
create unique index prediction_calibrations_one_published_fixture_idx
  on public.prediction_calibrations (fixture_id)
  where published and status = 'updated';

create index prediction_calibrations_pending_idx
  on public.prediction_calibrations (coalesce(retry_after, created_at), created_at)
  where status = 'pending' and not published;

create index prediction_calibrations_processing_idx
  on public.prediction_calibrations (started_at)
  where status = 'processing' and not published;

create trigger prediction_calibrations_set_updated_at
before update on public.prediction_calibrations
for each row execute function public.set_updated_at();

-- Publish/replacement must be atomic so readers never observe two current
-- calibrations and concurrent workers cannot race the partial unique index.
-- The function is intentionally SECURITY INVOKER: it cannot exceed the
-- caller's table privileges or bypass RLS.
create function public.publish_prediction_calibration(
  p_calibration_id uuid
)
returns public.prediction_calibrations
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  target_fixture_id bigint;
  published_calibration public.prediction_calibrations;
begin
  select calibration.fixture_id
  into target_fixture_id
  from public.prediction_calibrations as calibration
  where calibration.id = p_calibration_id;

  if not found then
    raise exception 'Prediction calibration not found'
      using errcode = 'P0002';
  end if;

  -- Serialize publication by fixture before acquiring row locks. This ordering
  -- avoids deadlocks when two successful attempts finish concurrently.
  perform pg_catalog.pg_advisory_xact_lock(target_fixture_id);

  select calibration.*
  into published_calibration
  from public.prediction_calibrations as calibration
  where calibration.id = p_calibration_id
  for update;

  if not found then
    raise exception 'Prediction calibration not found'
      using errcode = 'P0002';
  end if;

  if published_calibration.status <> 'updated' then
    raise exception 'Only an updated prediction calibration can be published'
      using errcode = '23514';
  end if;

  update public.prediction_calibrations
  set
    published = false,
    superseded_at = coalesce(superseded_at, pg_catalog.now())
  where fixture_id = target_fixture_id
    and id <> p_calibration_id
    and published;

  update public.prediction_calibrations
  set
    published = true,
    published_at = coalesce(published_at, pg_catalog.now()),
    superseded_at = null
  where id = p_calibration_id
  returning *
  into published_calibration;

  return published_calibration;
end;
$$;

-- This table lives in the exposed public schema for backend Data API access,
-- but it is private: RLS has no anon/authenticated policies and only the
-- backend service role receives table privileges.
alter table public.prediction_calibrations enable row level security;

revoke all on table public.prediction_calibrations
  from public, anon, authenticated, service_role;

grant select, insert, update on table public.prediction_calibrations
  to service_role;

revoke all on function public.publish_prediction_calibration(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.publish_prediction_calibration(uuid)
  to service_role;

comment on table public.prediction_calibrations is
  'Private append-oriented history of AI calibration attempts; backend service role only.';
comment on column public.prediction_calibrations.idempotency_key is
  'SHA-256 of the canonical attempt identity. Replayed jobs must reuse this key.';
comment on column public.prediction_calibrations.input_hash is
  'SHA-256 of canonical non-secret model input; prompts and API credentials are never stored here.';
comment on column public.prediction_calibrations.input_snapshot is
  'Allowlisted canonical model inputs only; never store credentials, request headers, raw provider payloads, or raw provider responses.';
comment on column public.prediction_calibrations.analysis is
  'Validated structured calibration output only; never a raw provider response.';
comment on column public.prediction_calibrations.safe_error_message is
  'Sanitized diagnostic safe for storage; raw exceptions, headers, and credentials are forbidden.';
comment on column public.prediction_calibrations.published is
  'True only for the current client-visible successful calibration for a fixture.';
comment on function public.publish_prediction_calibration(uuid) is
  'Atomically replaces the current published calibration for one fixture; service role only.';
