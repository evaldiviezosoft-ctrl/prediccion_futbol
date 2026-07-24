import pytest

from app.core.errors import PredictionInputError
from app.services.calendar_prediction_service import (
    GLOBAL_TEAM_HISTORY_PRIOR_CODE,
    PROFILE_PRIOR_STRENGTH_MATCHES,
    RECENT_ADJUSTMENT_WEIGHT,
    build_calendar_profile_prediction,
)
from app.services.calendar_visibility import (
    LocalTeamProfile,
    local_team_profile_catalog,
)


def _supabase_history_profile(
    *,
    league_code: str,
    with_statistics: bool = False,
    profile_name: str = 'Penafiel',
    dominant_league_id: int | None = None,
    goal_rates: tuple[float, float, float, float] | None = None,
) -> LocalTeamProfile:
    statistic_matches = 6 if with_statistics else 0
    season_gfpg, season_gapg, gf5, ga5 = goal_rates or (
        1.25,
        1.0,
        1.4,
        0.8,
    )
    metadata = {
        'source': 'loaded_finished_fixtures_and_team_statistics',
        'sample_sizes': {
            'finished_matches': 8,
            'statistics_matches': statistic_matches,
            'metrics': {
                'corners': {
                    'season': statistic_matches,
                    'last_five': min(statistic_matches, 5),
                },
                'total_shots': {
                    'season': statistic_matches,
                    'last_five': min(statistic_matches, 5),
                },
                'shots_on_goal': {
                    'season': statistic_matches,
                    'last_five': min(statistic_matches, 5),
                },
            },
        },
    }
    if dominant_league_id is not None:
        metadata['dominant_league_id'] = dominant_league_id
    return LocalTeamProfile(
        league_code=league_code,
        profile_name=profile_name,
        values={
            'season_mp': 8,
            'season_gfpg': season_gfpg,
            'season_gapg': season_gapg,
            'gf5': gf5,
            'ga5': ga5,
            'season_corners_pg': 4.75 if with_statistics else None,
            'corners5': 5.0 if with_statistics else None,
            'season_shots_pg': 10.5 if with_statistics else None,
            'shots5': 11.0 if with_statistics else None,
            'season_sot_pg': 3.5 if with_statistics else None,
            'sot5': 4.0 if with_statistics else None,
            'metadata': metadata,
        },
    )


def test_single_away_profile_uses_low_confidence_neutral_prior_without_rival_stats():
    result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Rosenborg',
        away_team_name='Manchester United',
    )

    assert result['model']['model_type'] == 'calendar_profile_fallback'
    assert result['model']['method'] == 'calendar_profile_poisson'
    assert result['model']['confidence'] == 'low'
    assert result['model']['known_profile_sides'] == ['away']
    assert result['model']['single_team_profile'] is True
    assert result['model']['venue_assumption'] == 'neutral'
    assert result['model']['not_calibrated_for_friendlies'] is True
    assert [market['line'] for market in result['goal_lines']] == [
        0.5, 1.5, 2.5, 3.5, 4.5,
    ]
    assert sum(
        result['probabilities'][key]
        for key in ('home_win', 'draw', 'away_win')
    ) == pytest.approx(1.0, abs=0.0001)

    expected = result['expected']
    assert {'home_goals', 'away_goals'} <= set(expected)
    assert {
        'away_corners',
        'away_shots',
        'away_shots_on_target',
    } <= set(expected)
    assert {
        'home_corners',
        'home_shots',
        'home_shots_on_target',
    }.isdisjoint(expected)

    home_metrics = result['model']['market_statistics']['teams']['home']['metrics']
    assert all(metric['status'] == 'reference_only' for metric in home_metrics.values())
    assert all(metric['published'] is False for metric in home_metrics.values())
    assert result['features']['goal_components']['home_attack']['source'] == (
        'neutral_league_profile_prior'
    )
    away_attack = result['features']['goal_components']['away_attack']
    assert away_attack['source'] == 'local_team_profile_empirical_bayes'
    assert away_attack['prior_strength_matches'] == PROFILE_PRIOR_STRENGTH_MATCHES
    assert away_attack['recent_adjustment_weight'] == RECENT_ADJUSTMENT_WEIGHT


def test_two_profiles_from_different_leagues_publish_both_team_statistics():
    result = build_calendar_profile_prediction(
        league_id=3,
        home_team_name='Barcelona',
        away_team_name='Manchester United',
    )

    assert result['model']['known_profile_sides'] == ['home', 'away']
    assert result['model']['single_team_profile'] is False
    assert result['model']['not_calibrated_for_friendlies'] is False
    assert {
        'home_corners',
        'away_corners',
        'home_shots',
        'away_shots',
        'home_shots_on_target',
        'away_shots_on_target',
    } <= set(result['expected'])
    profiles = result['features']['profiles']
    assert profiles['home']['league_code'] == 'SP1'
    assert profiles['away']['league_code'] == 'E0'
    assert all(
        component['source'] == 'local_team_profile_empirical_bayes'
        for component in result['features']['goal_components'].values()
    )


def test_calendar_profile_fallback_rejects_two_unknown_teams():
    with pytest.raises(PredictionInputError, match='At least one team'):
        build_calendar_profile_prediction(
            league_id=667,
            home_team_name='Unknown Home XI',
            away_team_name='Unknown Away XI',
        )


def test_supabase_team_history_publishes_only_metrics_with_team_samples():
    history_profile = _supabase_history_profile(league_code='SP1')

    result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Penafiel',
        away_team_name='Leganes',
        profile_overrides={'home': history_profile},
    )

    assert result['model']['known_profile_sides'] == ['home', 'away']
    assert result['model']['single_team_profile'] is False
    assert result['model']['data_source'] == (
        'local_profiles_and_supabase_team_history'
    )
    assert {
        'home_corners',
        'home_shots',
        'home_shots_on_target',
    }.isdisjoint(result['expected'])
    assert all(
        metric['status'] == 'unavailable'
        for metric in result['model']['market_statistics']['teams']['home'][
            'metrics'
        ].values()
    )


def test_supabase_history_uses_global_unique_profiles_prior_not_rival_league():
    spanish_code_result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Penafiel',
        away_team_name='Manchester United',
        profile_overrides={
            'home': _supabase_history_profile(
                league_code='SP1',
                with_statistics=True,
            ),
        },
    )
    english_code_result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Penafiel',
        away_team_name='Manchester United',
        profile_overrides={
            'home': _supabase_history_profile(
                league_code='E0',
                with_statistics=True,
            ),
        },
    )

    unique_profiles = {
        (profile.league_code, profile.profile_name): profile
        for profile in local_team_profile_catalog().values()
    }
    global_goal_values = [
        float(profile.values['season_gfpg'])
        for profile in unique_profiles.values()
        if profile.values.get('season_gfpg') is not None
    ]
    expected_global_prior = sum(global_goal_values) / len(global_goal_values)

    spanish_component = spanish_code_result['features']['goal_components'][
        'home_attack'
    ]
    english_component = english_code_result['features']['goal_components'][
        'home_attack'
    ]
    assert spanish_component['prior_league_code'] == (
        GLOBAL_TEAM_HISTORY_PRIOR_CODE
    )
    assert spanish_component['source_league_code'] is None
    assert spanish_component['league_code'] is None
    assert spanish_component['prior_rows'] == len(global_goal_values)
    assert spanish_component['league_prior'] == pytest.approx(
        round(expected_global_prior, 4)
    )
    assert english_component['league_prior'] == (
        spanish_component['league_prior']
    )
    assert english_component['value'] == spanish_component['value']

    profile_metadata = spanish_code_result['features']['profiles']['home']
    assert profile_metadata['source_kind'] == 'supabase_team_history'
    assert profile_metadata['source_league_code'] is None
    assert profile_metadata['league_code'] is None
    assert profile_metadata['league_id'] is None
    assert profile_metadata['prior_league_code'] == (
        GLOBAL_TEAM_HISTORY_PRIOR_CODE
    )
    assert profile_metadata['prior_league_id'] is None

    statistic_metadata = spanish_code_result['model'][
        'market_statistics'
    ]['teams']['home']
    assert statistic_metadata['source_league_code'] is None
    assert statistic_metadata['prior_league_code'] == (
        GLOBAL_TEAM_HISTORY_PRIOR_CODE
    )
    assert all(
        metric['source_league_code'] is None
        and metric['prior_league_code']
        == GLOBAL_TEAM_HISTORY_PRIOR_CODE
        for metric in statistic_metadata['metrics'].values()
    )


def test_cross_league_strength_calibration_favours_epl_over_eliteserien_history():
    """A strong local record must not erase a large cross-league strength gap.

    Contract for the future calibration API: explainability lives in
    ``features.cross_league_calibration`` and exposes raw versus adjusted goal
    means, source competitions, strength ratings and the bounded multipliers.
    """

    dominant_eliteserien_history = _supabase_history_profile(
        league_code='GLOBAL',
        profile_name='Rosenborg',
        dominant_league_id=103,
        goal_rates=(2.0, 0.9, 2.2, 0.8),
    )
    result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Rosenborg',
        away_team_name='Manchester United',
        profile_overrides={'home': dominant_eliteserien_history},
    )

    calibration = result['features']['cross_league_calibration']
    assert calibration['applied'] is True
    assert calibration['reason'] == 'different_competition_strength'
    assert calibration['home_source']['league_id'] == 103
    assert calibration['home_source']['source'] == 'dominant_league_id'
    assert calibration['away_source']['league_code'] == 'E0'
    assert calibration['away_source']['source'] == (
        'local_profile_league_code'
    )
    assert calibration['away_rating'] > calibration['home_rating']
    assert calibration['home_multiplier'] < 1.0
    assert calibration['away_multiplier'] > 1.0
    assert calibration['adjusted_expected_goals']['home'] < (
        calibration['raw_expected_goals']['home']
    )
    assert calibration['adjusted_expected_goals']['away'] > (
        calibration['raw_expected_goals']['away']
    )

    assert result['expected']['away_goals'] > result['expected']['home_goals']
    assert result['probabilities']['away_win'] > (
        result['probabilities']['home_win']
    )


def test_same_league_strength_calibration_is_neutral_and_explained():
    result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Manchester United',
        away_team_name='Arsenal',
    )

    calibration = result['features']['cross_league_calibration']
    assert calibration['applied'] is False
    assert calibration['reason'] == 'same_competition_strength'
    assert calibration['home_source']['league_code'] == 'E0'
    assert calibration['away_source']['league_code'] == 'E0'
    assert calibration['home_multiplier'] == 1.0
    assert calibration['away_multiplier'] == 1.0
    assert calibration['adjusted_expected_goals'] == (
        calibration['raw_expected_goals']
    )


def test_cross_league_calibration_is_bounded_and_preserves_probability_mass():
    result = build_calendar_profile_prediction(
        league_id=667,
        home_team_name='Rosenborg',
        away_team_name='Manchester United',
        profile_overrides={
            'home': _supabase_history_profile(
                league_code='GLOBAL',
                profile_name='Rosenborg',
                dominant_league_id=103,
                goal_rates=(8.0, 0.0, 9.0, 0.0),
            ),
        },
    )

    calibration = result['features']['cross_league_calibration']
    assert calibration['bounds']['rating_delta'] == [-220.0, 220.0]
    assert calibration['bounds']['goal_mean'] == [0.2, 4.0]
    assert 0.5 <= calibration['home_multiplier'] <= 2.0
    assert 0.5 <= calibration['away_multiplier'] <= 2.0
    assert calibration['total_preserved'] is True
    assert all(
        0.2 <= result['expected'][key] <= 4.0
        for key in ('home_goals', 'away_goals')
    )
    assert sum(
        result['probabilities'][key]
        for key in ('home_win', 'draw', 'away_win')
    ) == pytest.approx(1.0, abs=0.0001)
