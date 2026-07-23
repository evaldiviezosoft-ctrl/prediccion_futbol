with paired_legs as (
  select
    f.id,
    f.home_goals + reverse_fixture.away_goals as aggregate_home,
    f.away_goals + reverse_fixture.home_goals as aggregate_away,
    case
      when f.fixture_date_utc < reverse_fixture.fixture_date_utc then 'first'
      when f.fixture_date_utc > reverse_fixture.fixture_date_utc then 'second'
      else null
    end as inferred_leg
  from public.fixtures f
  join public.fixtures reverse_fixture
    on reverse_fixture.competition_id = f.competition_id
    and reverse_fixture.season = f.season
    and reverse_fixture.round is not distinct from f.round
    and reverse_fixture.home_team_id = f.away_team_id
    and reverse_fixture.away_team_id = f.home_team_id
    and reverse_fixture.id <> f.id
  join public.competitions c on c.id = f.competition_id
  where c.competition_type = 'cup'
    and f.status_short in ('FT', 'AET', 'PEN')
    and reverse_fixture.status_short in ('FT', 'AET', 'PEN')
    and f.home_goals is not null
    and f.away_goals is not null
    and reverse_fixture.home_goals is not null
    and reverse_fixture.away_goals is not null
)
update public.fixtures f
set
  aggregate_home = paired_legs.aggregate_home,
  aggregate_away = paired_legs.aggregate_away,
  leg = coalesce(f.leg, paired_legs.inferred_leg)
from paired_legs
where f.id = paired_legs.id;
