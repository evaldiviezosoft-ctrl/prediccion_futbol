from datetime import datetime, timedelta, timezone

import pytest

from app.services.baseline_market_service import (
    NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
    NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
    choose_team_history_sources,
    estimate_player_candidates,
    estimate_team_statistics,
)


CUTOFF = datetime(2026, 7, 23, 0, 30, tzinfo=timezone.utc)


def fixture(
    fixture_id: int,
    *,
    league_id: int,
    days_before: int,
    home: int,
    away: int,
    status: str = 'FT',
) -> dict:
    kickoff = CUTOFF - timedelta(days=days_before)
    return {
        'id': fixture_id,
        'league_id': league_id,
        'kickoff': kickoff.isoformat(),
        'status_short': status,
        'home_team_id': home,
        'away_team_id': away,
        'home_team_ref_id': home,
        'away_team_ref_id': away,
    }


def sources(rows: list[dict], *, league_id: int = 11):
    return choose_team_history_sources(
        target_league_id=league_id,
        home_team_id=10,
        away_team_id=20,
        home_team_ref_id=110,
        away_team_ref_id=120,
        target_kickoff=CUTOFF,
        historical_fixture_rows=rows,
    )


def test_cup_teams_choose_domestic_history_without_future_or_nonfinal_leakage():
    rows = [
        fixture(1, league_id=71, days_before=10, home=10, away=99),
        fixture(2, league_id=71, days_before=9, home=98, away=10),
        fixture(3, league_id=128, days_before=8, home=20, away=97),
        fixture(4, league_id=281, days_before=7, home=20, away=96),
        fixture(5, league_id=281, days_before=-1, home=10, away=20),
        fixture(6, league_id=281, days_before=1, home=10, away=20, status='NS'),
    ]

    selected, eligible = sources(rows)

    assert selected['home']['source_league_id'] == 71
    assert selected['home']['eligible_team_matches'] == 2
    # Equal counts are resolved deterministically by the configured mapping.
    assert selected['away']['source_league_id'] == 128
    assert {row['_fixture_id'] for row in eligible} == {1, 2, 3, 4}


def test_team_markets_use_explicit_peru_reference_and_never_future_stats():
    rows = [
        fixture(1, league_id=71, days_before=10, home=10, away=99),
        fixture(2, league_id=71, days_before=9, home=98, away=10),
        fixture(3, league_id=128, days_before=8, home=20, away=97),
        fixture(4, league_id=281, days_before=7, home=30, away=31),
        fixture(5, league_id=281, days_before=6, home=32, away=33),
        fixture(999, league_id=281, days_before=-1, home=30, away=31),
    ]
    selected, eligible = sources(rows)
    statistics = [
        {'fixture_id': 4, 'team_id': 30, 'is_home': True, 'corners': 6, 'total_shots': 14, 'shots_on_goal': 5, 'yellow_cards': 2},
        {'fixture_id': 4, 'team_id': 31, 'is_home': False, 'corners': 3, 'total_shots': 10, 'shots_on_goal': 3, 'yellow_cards': 3},
        {'fixture_id': 5, 'team_id': 32, 'is_home': True, 'corners': 8, 'total_shots': 16, 'shots_on_goal': 7, 'yellow_cards': 4},
        {'fixture_id': 5, 'team_id': 33, 'is_home': False, 'corners': 5, 'total_shots': 12, 'shots_on_goal': 4, 'yellow_cards': 5},
        # This tempting outlier belongs to an ineligible future fixture.
        {'fixture_id': 999, 'team_id': 30, 'is_home': True, 'corners': 99, 'total_shots': 99, 'shots_on_goal': 99},
    ]

    expected, metadata = estimate_team_statistics(
        sources=selected,
        eligible_fixture_rows=eligible,
        team_statistics_rows=statistics,
    )

    assert expected == {
        'home_corners': 7.0,
        'home_shots': 15.0,
        'home_shots_on_target': 6.0,
        'away_corners': 4.0,
        'away_shots': 11.0,
        'away_shots_on_target': 3.5,
    }
    home_corners = metadata['teams']['home']['metrics']['corners']
    assert home_corners == {
        'status': 'reference_only',
        'team_rows': 0,
        'prior_rows': 2,
        'prior_mean': 7.0,
        'prior_league_id': 281,
        'prior_scope': 'cross_league_reference_venue',
        'prior_selection_reason': 'selected_league_coverage_insufficient_using_reference',
        'coverage_gate': {
            'applies': True,
            'required_values': 40,
            'required_distinct_teams': 8,
            'observed_values': {'home': 0, 'away': 0},
            'observed_distinct_teams': {'home': 0, 'away': 0},
            'qualified': False,
        },
        'team_sample_used': False,
        'cross_league_reference': True,
        'confidence': 'low',
    }
    assert metadata['non_reference_prior_activation'] == {
        'minimum_valid_values_per_metric_and_venue': 40,
        'minimum_distinct_teams': 8,
    }
    assert metadata['teams']['home']['metrics']['yellow_cards'] == {
        'status': 'unavailable',
        'team_rows': 0,
        'prior_rows': 0,
        'prior_selection_reason':
            'selected_league_coverage_insufficient_no_cross_league_cards',
        'coverage_gate': {
            'applies': True,
            'required_values': 40,
            'required_distinct_teams': 8,
            'observed_values': {'home': 0, 'away': 0},
            'observed_distinct_teams': {'home': 0, 'away': 0},
            'qualified': False,
        },
    }


def test_cards_and_saves_require_qualified_selected_league_coverage():
    rows = [
        fixture(
            index + 1,
            league_id=71,
            days_before=100 + index,
            home=10 if index == 0 else 1000 + index,
            away=20 if index == 1 else 2000 + index,
        )
        for index in range(NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE)
    ]
    selected, eligible = sources(rows, league_id=71)
    statistics = []
    for index, row in enumerate(rows):
        statistics.extend([
            {
                'fixture_id': row['id'],
                'team_id': 110 if index == 0 else 3000 + index,
                'is_home': True,
                'yellow_cards': 2,
                'goalkeeper_saves': 4,
            },
            {
                'fixture_id': row['id'],
                'team_id': 120 if index == 1 else 4000 + index,
                'is_home': False,
                'yellow_cards': 3,
                'goalkeeper_saves': 5,
            },
        ])

    expected, metadata = estimate_team_statistics(
        sources=selected,
        eligible_fixture_rows=eligible,
        team_statistics_rows=statistics,
    )

    assert expected['home_yellow_cards'] == 2.0
    assert expected['away_yellow_cards'] == 3.0
    assert expected['home_goalkeeper_saves'] == 4.0
    assert expected['away_goalkeeper_saves'] == 5.0
    assert metadata['teams']['home']['metrics']['yellow_cards'][
        'prior_league_id'
    ] == 71
    assert metadata['teams']['home']['metrics']['yellow_cards'][
        'cross_league_reference'
    ] is False
    assert metadata['teams']['home']['metrics']['goalkeeper_saves'][
        'cross_league_reference'
    ] is False


@pytest.mark.parametrize(
    (
        'selected_value_count',
        'selected_team_count',
        'opposite_value_count',
        'opposite_team_count',
        'expected_prior_league_id',
        'expected_prior_mean',
        'qualified',
        'reason',
    ),
    [
        (
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE - 1,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE - 1,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
            281,
            7.0,
            False,
            'selected_league_coverage_insufficient_using_reference',
        ),
        (
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS - 1,
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS - 1,
            281,
            7.0,
            False,
            'selected_league_coverage_insufficient_using_reference',
        ),
        (
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
            0,
            0,
            281,
            7.0,
            False,
            'selected_league_coverage_insufficient_using_reference',
        ),
        (
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
            NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
            NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
            71,
            10.0,
            True,
            'selected_league_coverage_qualified',
        ),
    ],
)
def test_non_reference_prior_requires_safe_venue_coverage(
    selected_value_count,
    selected_team_count,
    opposite_value_count,
    opposite_team_count,
    expected_prior_league_id,
    expected_prior_mean,
    qualified,
    reason,
):
    brazil_fixture_count = max(selected_value_count, opposite_value_count)
    brazil_fixtures = [
        fixture(
            index + 1,
            league_id=71,
            days_before=100 + index,
            home=1000 + index,
            away=2000 + index,
        )
        for index in range(brazil_fixture_count)
    ]
    peru_fixtures = [
        fixture(1001, league_id=281, days_before=20, home=3001, away=4001),
        fixture(1002, league_id=281, days_before=10, home=3002, away=4002),
    ]
    selected, eligible = sources(
        brazil_fixtures + peru_fixtures,
        league_id=71,
    )
    statistics = [
        {
            'fixture_id': index + 1,
            'team_id': 5000 + index % selected_team_count,
            'is_home': True,
            'corners': 10,
            'total_shots': 20,
            'shots_on_goal': 7,
        }
        for index in range(selected_value_count)
    ]
    statistics.extend([
        {
            'fixture_id': index + 1,
            'team_id': 8000 + index % opposite_team_count,
            'is_home': False,
            'corners': 9,
            'total_shots': 18,
            'shots_on_goal': 6,
        }
        for index in range(opposite_value_count)
    ])
    statistics.extend([
        {
            'fixture_id': 1001,
            'team_id': 6001,
            'is_home': True,
            'corners': 6,
            'total_shots': 12,
            'shots_on_goal': 4,
        },
        {
            'fixture_id': 1001,
            'team_id': 7001,
            'is_home': False,
            'corners': 3,
            'total_shots': 9,
            'shots_on_goal': 3,
        },
        {
            'fixture_id': 1002,
            'team_id': 6002,
            'is_home': True,
            'corners': 8,
            'total_shots': 14,
            'shots_on_goal': 6,
        },
        {
            'fixture_id': 1002,
            'team_id': 7002,
            'is_home': False,
            'corners': 5,
            'total_shots': 11,
            'shots_on_goal': 4,
        },
    ])

    expected, metadata = estimate_team_statistics(
        sources=selected,
        eligible_fixture_rows=eligible,
        team_statistics_rows=statistics,
    )

    assert expected['home_corners'] == expected_prior_mean
    home_corners = metadata['teams']['home']['metrics']['corners']
    assert home_corners['prior_league_id'] == expected_prior_league_id
    assert home_corners['prior_mean'] == expected_prior_mean
    assert home_corners['prior_selection_reason'] == reason
    assert home_corners['coverage_gate'] == {
        'applies': True,
        'required_values': NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
        'required_distinct_teams': NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
        'observed_values': {
            'home': selected_value_count,
            'away': opposite_value_count,
        },
        'observed_distinct_teams': {
            'home': selected_team_count,
            'away': opposite_team_count,
        },
        'qualified': qualified,
    }


def test_team_observations_are_shrunk_toward_selected_league_prior():
    rows = [
        fixture(1, league_id=281, days_before=10, home=10, away=30),
        fixture(2, league_id=281, days_before=9, home=11, away=20),
        fixture(3, league_id=281, days_before=8, home=12, away=31),
    ]
    selected, eligible = sources(rows, league_id=281)
    statistics = [
        {'fixture_id': 1, 'team_id': 110, 'is_home': True, 'corners': 10, 'total_shots': 20, 'shots_on_goal': 8, 'yellow_cards': 2},
        {'fixture_id': 2, 'team_id': 120, 'is_home': False, 'corners': 2, 'total_shots': 8, 'shots_on_goal': 2, 'yellow_cards': 3},
        {'fixture_id': 3, 'team_id': 112, 'is_home': True, 'corners': 4, 'total_shots': 10, 'shots_on_goal': 4, 'yellow_cards': 4},
    ]

    expected, metadata = estimate_team_statistics(
        sources=selected,
        eligible_fixture_rows=eligible,
        team_statistics_rows=statistics,
        prior_strength=1,
    )

    # Home prior is (10 + 4) / 2 = 7; posterior with one team observation is 8.5.
    assert expected['home_corners'] == 8.5
    assert 'home_yellow_cards' not in expected
    assert 'away_yellow_cards' not in expected
    assert metadata['teams']['home']['metrics']['yellow_cards'][
        'prior_selection_reason'
    ] == 'selected_league_coverage_insufficient_no_cross_league_cards'
    assert metadata['teams']['home']['metrics']['yellow_cards'][
        'coverage_gate'
    ]['qualified'] is False
    assert metadata['teams']['home']['metrics']['corners']['status'] == 'estimated'
    assert metadata['teams']['home']['metrics']['corners']['team_rows'] == 1


def test_player_candidates_require_evidence_and_freshness():
    rows = [
        fixture(1, league_id=281, days_before=30, home=10, away=30),
        fixture(2, league_id=281, days_before=20, home=31, away=20),
    ]
    selected, _eligible = sources(rows, league_id=281)
    player_rows = [
        {'fixture_id': 1, 'team_id': 110, 'player_id': 1001, 'minutes': 90, 'starter': True, 'goals': 1, 'assists': 0},
        {'fixture_id': 1, 'team_id': 110, 'player_id': 1002, 'minutes': 90, 'starter': True, 'goals': 0, 'assists': 1},
        {'fixture_id': 2, 'team_id': 120, 'player_id': 2001, 'minutes': 45, 'starter': True, 'goals': 2, 'assists': 1},
    ]
    players = {
        1001: {'name': 'Home Scorer'},
        1002: {'name': 'Home Assistant'},
        2001: {'name': 'Away Scorer'},
    }

    scorers, assistants, metadata = estimate_player_candidates(
        sources=selected,
        target_kickoff=CUTOFF,
        expected_goals={'home_goals': 1.5, 'away_goals': 1.0},
        player_statistics_rows=player_rows,
        players_by_id=players,
        team_names={'home': 'Home', 'away': 'Away'},
    )

    assert {row['player'] for row in scorers} == {'Home Scorer', 'Away Scorer'}
    assert {row['player'] for row in assistants} == {'Home Assistant', 'Away Scorer'}
    assert all(0 < row['probability'] < 1 for row in scorers + assistants)
    assert metadata['teams']['home']['status'] == 'historical_candidates_available'

    stale_rows = [
        fixture(10, league_id=281, days_before=500, home=10, away=30),
        fixture(11, league_id=281, days_before=500, home=31, away=20),
    ]
    stale_sources, _eligible = sources(stale_rows, league_id=281)
    stale_player_rows = [dict(player_rows[0], fixture_id=10)]
    stale_scorers, stale_assistants, stale_metadata = estimate_player_candidates(
        sources=stale_sources,
        target_kickoff=CUTOFF,
        expected_goals={'home_goals': 1.5, 'away_goals': 1.0},
        player_statistics_rows=stale_player_rows,
        players_by_id=players,
        team_names={'home': 'Home', 'away': 'Away'},
    )
    assert stale_scorers == []
    assert stale_assistants == []
    assert stale_metadata['teams']['home']['status'] == 'insufficient_freshness'


def test_no_observed_event_means_no_named_candidate_even_with_shrinkage():
    rows = [fixture(1, league_id=281, days_before=10, home=10, away=20)]
    selected, _eligible = sources(rows, league_id=281)
    player_rows = [
        {'fixture_id': 1, 'team_id': 110, 'player_id': 1001, 'minutes': 90, 'starter': True, 'goals': 0, 'assists': 0},
    ]
    scorers, assistants, _metadata = estimate_player_candidates(
        sources=selected,
        target_kickoff=CUTOFF,
        expected_goals={'home_goals': 2.0, 'away_goals': 1.0},
        player_statistics_rows=player_rows,
        players_by_id={1001: {'name': 'No Event'}},
        team_names={'home': 'Home', 'away': 'Away'},
    )
    assert scorers == []
    assert assistants == []
