alter table public.fixture_injuries
  add column active boolean not null default true;

create index fixture_injuries_active_fixture_idx
  on public.fixture_injuries (fixture_id, fetched_at desc)
  where active;

comment on column public.fixture_injuries.active is
  'True when the injury was present in the latest provider snapshot for the fixture.';
