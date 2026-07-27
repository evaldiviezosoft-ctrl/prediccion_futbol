-- Deterministic market snapshots and post-match settlement.
alter table public.predictions
  add column market_forecast jsonb not null default jsonb_build_object(
    'version', 'deterministic_lines_v1',
    'method', 'poisson_mean_approximation',
    'markets', '[]'::jsonb
  ),
  add constraint predictions_market_forecast_object check (
    jsonb_typeof(market_forecast) = 'object'
  );

comment on column public.predictions.market_forecast is
  'Immutable-at-publication deterministic over/under market lines. No external AI output.';

create table public.prediction_evaluations (
  fixture_id bigint primary key
    references public.fixtures(id) on delete cascade,
  prediction_version_id uuid
    references public.prediction_versions(id) on delete set null,
  forecast_version text not null,
  status text not null,
  actual jsonb not null default '{}'::jsonb,
  scored_selections integer not null default 0,
  correct_selections integer not null default 0,
  accuracy double precision,
  evaluated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint prediction_evaluations_forecast_version_not_blank check (
    btrim(forecast_version) <> ''
  ),
  constraint prediction_evaluations_status_valid check (
    status in ('completed', 'partial', 'void', 'legacy_unscored')
  ),
  constraint prediction_evaluations_actual_object check (
    jsonb_typeof(actual) = 'object'
  ),
  constraint prediction_evaluations_counts_valid check (
    scored_selections >= 0
    and correct_selections >= 0
    and correct_selections <= scored_selections
  ),
  constraint prediction_evaluations_accuracy_valid check (
    accuracy is null or accuracy between 0 and 1
  )
);

create table public.prediction_market_results (
  id bigint generated always as identity primary key,
  fixture_id bigint not null
    references public.prediction_evaluations(fixture_id) on delete cascade,
  prediction_version_id uuid not null
    references public.prediction_versions(id) on delete cascade,
  forecast_version text not null,
  market text not null,
  scope text not null default 'match_total',
  period text not null default 'full_time',
  line numeric(8, 2) not null,
  direction text not null,
  probability double precision not null,
  actual_value numeric(10, 3),
  outcome text not null,
  reason_code text,
  evaluated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint prediction_market_results_unique_selection unique (
    prediction_version_id,
    market,
    scope,
    period,
    line,
    direction
  ),
  constraint prediction_market_results_forecast_version_not_blank check (
    btrim(forecast_version) <> ''
  ),
  constraint prediction_market_results_market_valid check (
    market in (
      'goals',
      'corners',
      'yellow_cards',
      'shots',
      'shots_on_target'
    )
  ),
  constraint prediction_market_results_scope_valid check (
    scope in ('match_total', 'home_team', 'away_team')
  ),
  constraint prediction_market_results_period_valid check (
    period in ('full_time', 'first_half', 'second_half')
  ),
  constraint prediction_market_results_line_nonnegative check (line >= 0),
  constraint prediction_market_results_direction_valid check (
    direction in ('over', 'under')
  ),
  constraint prediction_market_results_probability_valid check (
    probability between 0 and 1
  ),
  constraint prediction_market_results_actual_nonnegative check (
    actual_value is null or actual_value >= 0
  ),
  constraint prediction_market_results_outcome_valid check (
    outcome in ('won', 'lost', 'push', 'void', 'pending')
  )
);

create index prediction_evaluations_status_evaluated_idx
  on public.prediction_evaluations (status, evaluated_at desc);

create index prediction_market_results_fixture_idx
  on public.prediction_market_results (fixture_id);

create index prediction_market_results_market_line_idx
  on public.prediction_market_results (
    market,
    scope,
    period,
    line,
    direction,
    outcome
  );

create trigger prediction_evaluations_set_updated_at
before update on public.prediction_evaluations
for each row execute function public.set_updated_at();

create trigger prediction_market_results_set_updated_at
before update on public.prediction_market_results
for each row execute function public.set_updated_at();

alter table public.prediction_evaluations enable row level security;
alter table public.prediction_market_results enable row level security;

revoke all on table public.prediction_evaluations
  from anon, authenticated, service_role;
revoke all on table public.prediction_market_results
  from anon, authenticated, service_role;

grant select, insert, update on table public.prediction_evaluations
  to service_role;
grant select, insert, update on table public.prediction_market_results
  to service_role;

revoke all on sequence public.prediction_market_results_id_seq
  from anon, authenticated;
grant usage, select on sequence public.prediction_market_results_id_seq
  to service_role;

comment on table public.prediction_evaluations is
  'Post-match evaluation summary for the last prediction version published before kickoff.';
comment on table public.prediction_market_results is
  'Per-line deterministic forecast outcomes compared with final provider statistics.';
