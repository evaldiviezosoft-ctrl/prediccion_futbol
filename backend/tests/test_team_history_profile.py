from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.team_history_profile import build_team_history_profile


UTC = timezone.utc


def fixture(
    fixture_id: int,
    *,
    kickoff: datetime,
    team_home: bool,
    status: str = 'FT',
    home_goals: int = 2,
    away_goals: int = 1,
    api_team_id: int = 33,
    team_ref_id: int = 9001,
) -> dict:
    return {
        'id': fixture_id,
        'api_fixture_id': fixture_id,
        'league_id': 39,
        'season': 2026,
        'kickoff': kickoff.isoformat(),
        'status_short': status,
        'home_team_id': api_team_id if team_home else 10000 + fixture_id,
        'away_team_id': 10000 + fixture_id if team_home else api_team_id,
        'home_team_ref_id': team_ref_id if team_home else 20000 + fixture_id,
        'away_team_ref_id': 20000 + fixture_id if team_home else team_ref_id,
        'home_goals': home_goals,
        'away_goals': away_goals,
    }


def statistic(
    fixture_id: int,
    *,
    team_id: int = 9001,
    corners: float = 5,
    shots: float = 12,
    shots_on_goal: float = 4,
) -> dict:
    return {
        'fixture_id': fixture_id,
        'team_id': team_id,
        'corners': corners,
        'total_shots': shots,
        'shots_on_goal': shots_on_goal,
    }


def test_builds_leakage_safe_deduplicated_profile_in_team_perspective():
    cutoff = datetime(2026, 7, 24, 18, tzinfo=UTC)
    rows = [
        fixture(
            index,
            kickoff=cutoff - timedelta(days=8 - index),
            team_home=index % 2 == 1,
            home_goals=index % 3,
            away_goals=(index + 1) % 3,
        )
        for index in range(1, 7)
    ]
    rows.extend([
        dict(rows[2]),  # Exact duplicate must not increase the sample.
        fixture(98, kickoff=cutoff, team_home=True),  # Equal to cutoff is future.
        fixture(99, kickoff=cutoff - timedelta(days=1), team_home=True, status='NS'),
    ])
    stats = [
        statistic(
            index,
            corners=index,
            shots=10 + index,
            shots_on_goal=2 + index,
        )
        for index in range(1, 7)
    ]
    stats.extend([
        statistic(3),  # Duplicate for the correct surrogate.
        statistic(4, team_id=33, corners=99, shots=99, shots_on_goal=99),
        # team_id=33 is the API id, not the public.teams.id surrogate.
        statistic(98, corners=99, shots=99, shots_on_goal=99),
    ])

    profile = build_team_history_profile(
        api_team_id=33,
        team_name='Manchester United',
        fixture_rows=rows,
        team_statistics_rows=stats,
        cutoff=cutoff,
    )

    assert profile is not None
    assert profile['season_mp'] == 6
    assert profile['last_match_date'] == (cutoff - timedelta(days=2)).date().isoformat()
    assert profile['season_gfpg'] == pytest.approx(1.0)
    assert profile['season_gapg'] == pytest.approx(1.0)
    assert profile['gf5'] == pytest.approx(1.0)
    assert profile['ga5'] == pytest.approx(0.8)
    assert profile['season_corners_pg'] == pytest.approx(3.5)
    assert profile['corners5'] == pytest.approx(4.0)
    assert profile['season_shots_pg'] == pytest.approx(13.5)
    assert profile['shots5'] == pytest.approx(14.0)
    assert profile['season_sot_pg'] == pytest.approx(5.5)
    assert profile['sot5'] == pytest.approx(6.0)

    metadata = profile['metadata']
    assert metadata['surrogate_team_id'] == 9001
    assert metadata['sample_sizes']['finished_matches'] == 6
    assert metadata['sample_sizes']['statistics_matches'] == 6
    assert metadata['dominant_league_id'] == 39
    assert metadata['competitions'] == [{'league_id': 39, 'matches': 6}]
    assert metadata['deduplicated_fixture_rows'] == 1
    assert metadata['deduplicated_statistic_rows'] == 1
    assert 98 not in metadata['fixture_ids']
    assert 99 not in metadata['fixture_ids']


def test_returns_none_below_configurable_minimum_sample():
    cutoff = datetime(2026, 7, 24, tzinfo=UTC)
    rows = [
        fixture(
            index,
            kickoff=cutoff - timedelta(days=index),
            team_home=True,
        )
        for index in range(1, 5)
    ]

    assert build_team_history_profile(
        api_team_id=33,
        team_name='Manchester United',
        fixture_rows=rows,
        team_statistics_rows=[],
        cutoff=cutoff,
    ) is None

    profile = build_team_history_profile(
        api_team_id=33,
        team_name='Manchester United',
        fixture_rows=rows,
        team_statistics_rows=[],
        cutoff=cutoff,
        min_matches=4,
    )
    assert profile is not None
    assert profile['season_mp'] == 4


def test_missing_or_conflicting_surrogate_never_matches_stats_by_api_id():
    cutoff = datetime(2026, 7, 24, tzinfo=UTC)
    rows = [
        fixture(
            index,
            kickoff=cutoff - timedelta(days=7 - index),
            team_home=True,
            team_ref_id=9001 if index < 4 else 9002,
        )
        for index in range(1, 7)
    ]
    stats = [
        statistic(index, team_id=33, corners=99, shots=99, shots_on_goal=99)
        for index in range(1, 7)
    ]

    profile = build_team_history_profile(
        api_team_id=33,
        team_name='Manchester United',
        fixture_rows=rows,
        team_statistics_rows=stats,
        cutoff=cutoff,
    )

    assert profile is not None
    assert profile['season_corners_pg'] is None
    assert profile['corners5'] is None
    assert profile['metadata']['surrogate_team_id'] is None
    assert profile['metadata']['surrogate_conflict'] is True
    assert profile['metadata']['sample_sizes']['statistics_matches'] == 0


def test_explicit_surrogate_selects_only_that_internal_team():
    cutoff = datetime(2026, 7, 24, tzinfo=UTC)
    rows = [
        fixture(
            index,
            kickoff=cutoff - timedelta(days=index),
            team_home=index % 2 == 0,
            team_ref_id=9001,
        )
        for index in range(1, 6)
    ]
    stats = [
        statistic(index, team_id=7007, corners=index)
        for index in range(1, 6)
    ] + [
        statistic(index, team_id=9001, corners=99)
        for index in range(1, 6)
    ]

    profile = build_team_history_profile(
        api_team_id=33,
        team_name='Manchester United',
        fixture_rows=rows,
        team_statistics_rows=stats,
        cutoff=cutoff,
        team_ref_id=7007,
    )

    assert profile is not None
    assert profile['season_corners_pg'] == pytest.approx(3.0)
    assert profile['metadata']['surrogate_team_id'] == 7007


@pytest.mark.parametrize(
    ('changes', 'message'),
    [
        ({'api_team_id': 0}, 'api_team_id'),
        ({'team_name': '  '}, 'team_name'),
        ({'min_matches': 0}, 'min_matches'),
        ({'cutoff': 'not-a-date'}, 'cutoff'),
        ({'team_ref_id': 0}, 'team_ref_id'),
    ],
)
def test_rejects_invalid_contract_values(changes, message):
    kwargs = {
        'api_team_id': 33,
        'team_name': 'Manchester United',
        'fixture_rows': [],
        'team_statistics_rows': [],
        'cutoff': '2026-07-24T10:00:00Z',
        'team_ref_id': None,
        'min_matches': 5,
    }
    kwargs.update(changes)

    with pytest.raises(ValueError, match=message):
        build_team_history_profile(**kwargs)
