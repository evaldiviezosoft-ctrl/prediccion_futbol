create table public.leagues (
  id bigint primary key,
  code text not null unique,
  name text not null,
  country text not null,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint leagues_id_positive check (id > 0),
  constraint leagues_code_not_blank check (btrim(code) <> ''),
  constraint leagues_name_not_blank check (btrim(name) <> ''),
  constraint leagues_country_not_blank check (btrim(country) <> '')
);

create table public.fixtures (
  id bigint primary key,
  league_id bigint not null references public.leagues(id),
  season integer not null,
  round text,
  kickoff timestamptz not null,
  timezone text,
  venue_name text,
  status_short text,
  home_team_id bigint not null,
  away_team_id bigint not null,
  home_team_name text not null,
  away_team_name text not null,
  raw_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fixtures_id_positive check (id > 0),
  constraint fixtures_season_reasonable check (season between 2000 and 2100),
  constraint fixtures_team_ids_positive check (home_team_id > 0 and away_team_id > 0),
  constraint fixtures_distinct_teams check (home_team_id <> away_team_id),
  constraint fixtures_team_names_not_blank check (
    btrim(home_team_name) <> '' and btrim(away_team_name) <> ''
  ),
  constraint fixtures_raw_payload_object check (jsonb_typeof(raw_payload) = 'object')
);

create index fixtures_kickoff_idx on public.fixtures (kickoff);

create table public.predictions (
  fixture_id bigint primary key references public.fixtures(id) on delete cascade,
  league_id bigint not null references public.leagues(id),
  league_code text not null,
  home_team_id bigint not null,
  away_team_id bigint not null,
  home_team_name text not null,
  away_team_name text not null,
  kickoff timestamptz not null,
  stage text not null,
  lineups_confirmed boolean not null default false,
  home_win_probability double precision not null,
  draw_probability double precision not null,
  away_win_probability double precision not null,
  over25_probability double precision,
  btts_probability double precision,
  expected jsonb not null default '{}'::jsonb,
  likely_scores jsonb not null default '[]'::jsonb,
  possible_scorers jsonb not null default '[]'::jsonb,
  model_metadata jsonb not null default '{}'::jsonb,
  features_snapshot jsonb not null default '{}'::jsonb,
  published boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint predictions_stage_valid check (
    stage in ('initial', 'prematch', 'waiting_lineups', 'lineups_confirmed', 'final_prematch')
  ),
  constraint predictions_team_ids_positive check (home_team_id > 0 and away_team_id > 0),
  constraint predictions_distinct_teams check (home_team_id <> away_team_id),
  constraint predictions_team_names_not_blank check (
    btrim(home_team_name) <> '' and btrim(away_team_name) <> ''
  ),
  constraint predictions_league_code_not_blank check (btrim(league_code) <> ''),
  constraint predictions_home_win_range check (home_win_probability between 0 and 1),
  constraint predictions_draw_range check (draw_probability between 0 and 1),
  constraint predictions_away_win_range check (away_win_probability between 0 and 1),
  constraint predictions_over25_range check (
    over25_probability is null or over25_probability between 0 and 1
  ),
  constraint predictions_btts_range check (
    btts_probability is null or btts_probability between 0 and 1
  ),
  constraint predictions_1x2_sum check (
    abs(home_win_probability + draw_probability + away_win_probability - 1.0) <= 0.001
  ),
  constraint predictions_lineups_stage_consistent check (
    not lineups_confirmed or stage in ('lineups_confirmed', 'final_prematch')
  ),
  constraint predictions_expected_object check (jsonb_typeof(expected) = 'object'),
  constraint predictions_likely_scores_array check (jsonb_typeof(likely_scores) = 'array'),
  constraint predictions_possible_scorers_array check (jsonb_typeof(possible_scorers) = 'array'),
  constraint predictions_model_metadata_object check (jsonb_typeof(model_metadata) = 'object'),
  constraint predictions_features_snapshot_object check (jsonb_typeof(features_snapshot) = 'object')
);

create table public.prediction_versions (
  id uuid primary key default gen_random_uuid(),
  fixture_id bigint not null references public.fixtures(id) on delete cascade,
  stage text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  constraint prediction_versions_stage_valid check (
    stage in ('initial', 'prematch', 'waiting_lineups', 'lineups_confirmed', 'final_prematch')
  ),
  constraint prediction_versions_payload_object check (jsonb_typeof(payload) = 'object')
);

create index prediction_versions_fixture_created_idx
  on public.prediction_versions (fixture_id, created_at desc);

insert into public.leagues (id, code, name, country)
values
  (39, 'E0', 'Premier League', 'Inglaterra'),
  (61, 'F1', 'Ligue 1', 'Francia'),
  (78, 'D1', 'Bundesliga', 'Alemania'),
  (135, 'I1', 'Serie A', 'Italia'),
  (140, 'SP1', 'LaLiga', 'España');

create function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
begin
  new.updated_at = pg_catalog.now();
  return new;
end;
$$;

create trigger leagues_set_updated_at
before update on public.leagues
for each row execute function public.set_updated_at();

create trigger fixtures_set_updated_at
before update on public.fixtures
for each row execute function public.set_updated_at();

create trigger predictions_set_updated_at
before update on public.predictions
for each row execute function public.set_updated_at();

alter table public.leagues enable row level security;
alter table public.fixtures enable row level security;
alter table public.predictions enable row level security;
alter table public.prediction_versions enable row level security;

revoke all on table public.leagues from anon, authenticated, service_role;
revoke all on table public.fixtures from anon, authenticated, service_role;
revoke all on table public.predictions from anon, authenticated, service_role;
revoke all on table public.prediction_versions from anon, authenticated, service_role;

grant usage on schema public to anon, authenticated, service_role;
grant select on table public.predictions to anon, authenticated;

grant select on table public.leagues to service_role;
grant select, insert, update on table public.fixtures to service_role;
grant select, insert, update on table public.predictions to service_role;
grant select, insert on table public.prediction_versions to service_role;

revoke all on function public.set_updated_at() from public;
grant execute on function public.set_updated_at() to service_role;

create policy predictions_public_read
on public.predictions
for select
to anon, authenticated
using (published = true);

do $$
begin
  if not exists (
    select 1
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'predictions'
  ) then
    execute 'alter publication supabase_realtime add table public.predictions';
  end if;
end;
$$;
