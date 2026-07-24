from __future__ import annotations

import math
from typing import Any, Mapping

from app.core.errors import PredictionInputError
from app.services.baseline_model_service import (
    poisson_markets_from_expected_goals,
)
from app.services.calendar_visibility import (
    LocalTeamProfile,
    local_team_profile,
    local_team_profile_catalog,
)
from app.services.competition_strength import (
    CompetitionStrength,
    resolve_competition_strength,
)
from app.services.fixture_service import LEAGUE_ID_TO_CODE


CALENDAR_PREDICTION_LEAGUES = {
    3: ('uefa_europa_league', 'UEFA Europa League'),
    667: ('friendlies_clubs', 'Friendlies Clubs'),
}
GLOBAL_TEAM_HISTORY_PRIOR_CODE = 'GLOBAL'
_LEAGUE_CODE_TO_ID = {
    league_code: league_id
    for league_id, league_code in LEAGUE_ID_TO_CODE.items()
}
PROFILE_PRIOR_STRENGTH_MATCHES = 8.0
RECENT_ADJUSTMENT_WEIGHT = 0.15
CROSS_LEAGUE_CALIBRATION_VERSION = 'competition_strength_v1'
CROSS_LEAGUE_RATING_CENTER = 1500.0
CROSS_LEAGUE_RATING_SCALE = 1000.0
CROSS_LEAGUE_FRIENDLY_WEIGHT = 0.80
CROSS_LEAGUE_RATING_DELTA_LIMIT = 220.0
GOAL_MEAN_MIN = 0.2
GOAL_MEAN_MAX = 4.0
_GOAL_SPECS = {
    'home_attack': ('season_gfpg', ('gf5',)),
    'home_defence': ('season_gapg', ('ga5',)),
    'away_attack': ('season_gfpg', ('gf5',)),
    'away_defence': ('season_gapg', ('ga5',)),
}
_STAT_SPECS = {
    'corners': ('season_corners_pg', ('corners5',)),
    'shots': ('season_shots_pg', ('shots5',)),
    'shots_on_target': ('season_sot_pg', ('sot5',)),
}
_HISTORY_STAT_SAMPLE_KEYS = {
    'season_corners_pg': 'corners',
    'season_shots_pg': 'total_shots',
    'season_sot_pg': 'shots_on_goal',
}


def _nonnegative_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 and math.isfinite(parsed) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _profile_mean(profile: LocalTeamProfile, keys: tuple[str, ...]) -> float | None:
    return _mean([
        value
        for key in keys
        if (value := _nonnegative_float(profile.values.get(key))) is not None
    ])


def _league_profiles(league_code: str) -> list[LocalTeamProfile]:
    profiles: dict[tuple[str, str], LocalTeamProfile] = {}
    for profile in local_team_profile_catalog().values():
        if (
            league_code == GLOBAL_TEAM_HISTORY_PRIOR_CODE
            or profile.league_code == league_code
        ):
            profiles.setdefault(
                (profile.league_code, profile.profile_name),
                profile,
            )
    return list(profiles.values())


def _league_season_prior(
    league_code: str,
    season_key: str,
) -> tuple[float | None, int]:
    values = [
        value
        for profile in _league_profiles(league_code)
        if (
            value := _nonnegative_float(profile.values.get(season_key))
        ) is not None
    ]
    return _mean(values), len(values)


def _profile_source_kind(profile: LocalTeamProfile) -> str:
    metadata = profile.values.get('metadata')
    if (
        isinstance(metadata, Mapping)
        and metadata.get('source')
        == 'loaded_finished_fixtures_and_team_statistics'
    ):
        return 'supabase_team_history'
    return 'local_team_profile'


def _profile_source_league_code(
    profile: LocalTeamProfile,
) -> str | None:
    if _profile_source_kind(profile) == 'supabase_team_history':
        return None
    return profile.league_code


def _profile_source_league_id(profile: LocalTeamProfile) -> int | None:
    if _profile_source_kind(profile) != 'supabase_team_history':
        return _LEAGUE_CODE_TO_ID.get(profile.league_code)
    metadata = profile.values.get('metadata')
    if not isinstance(metadata, Mapping):
        return None
    try:
        value = int(metadata.get('dominant_league_id'))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _profile_prior_league_code(profile: LocalTeamProfile) -> str:
    if _profile_source_kind(profile) == 'supabase_team_history':
        return GLOBAL_TEAM_HISTORY_PRIOR_CODE
    return profile.league_code


def _competition_rating(strength: CompetitionStrength) -> float:
    """Convert a positive relative factor into an Elo-like display scale."""

    return (
        CROSS_LEAGUE_RATING_CENTER
        + CROSS_LEAGUE_RATING_SCALE * math.log10(strength.factor)
    )


def _profile_competition_strength(
    profile: LocalTeamProfile | None,
) -> tuple[CompetitionStrength | None, dict[str, Any]]:
    """Resolve the profile's source competition without inventing context."""

    if profile is None:
        return None, {
            'league_id': None,
            'league_code': None,
            'source': 'missing_profile',
            'catalog_source': None,
            'factor': None,
            'rating': None,
            'is_fallback': True,
        }

    source_kind = _profile_source_kind(profile)
    league_id = _profile_source_league_id(profile)
    league_code = _profile_source_league_code(profile)
    if source_kind == 'supabase_team_history':
        source = (
            'dominant_league_id'
            if league_id is not None
            else 'missing_dominant_league_id'
        )
    else:
        source = 'local_profile_league_code'

    strength = resolve_competition_strength(
        league_id=league_id,
        league_code=league_code,
    )
    metadata = strength.to_metadata()
    metadata['catalog_source'] = metadata.pop('source')
    metadata.update({
        'source': source,
        'profile_name': profile.profile_name,
        'profile_source_kind': source_kind,
        'league_id': league_id,
        'league_code': league_code,
        'rating': round(_competition_rating(strength), 4),
    })
    return strength, metadata


def _cross_league_goal_calibration(
    *,
    fixture_league_id: int,
    home_profile: LocalTeamProfile | None,
    away_profile: LocalTeamProfile | None,
    raw_home_goals: float,
    raw_away_goals: float,
) -> tuple[float, float, dict[str, Any]]:
    """Rebalance goal share by source-competition strength.

    The raw Poisson total is preserved. Only the home/away share changes, so a
    cross-league correction cannot manufacture a higher over/under total.
    Unknown competition strength is never guessed.
    """

    home_strength, home_source = _profile_competition_strength(home_profile)
    away_strength, away_source = _profile_competition_strength(away_profile)
    raw_expected = {
        'home': round(raw_home_goals, 4),
        'away': round(raw_away_goals, 4),
    }
    adjusted_home = raw_home_goals
    adjusted_away = raw_away_goals
    applied = False
    rating_delta: float | None = None
    effective_delta = 0.0

    if home_strength is None or away_strength is None:
        reason = 'insufficient_competition_context'
    elif home_strength.is_fallback or away_strength.is_fallback:
        reason = 'unknown_competition_strength'
    else:
        home_rating = _competition_rating(home_strength)
        away_rating = _competition_rating(away_strength)
        rating_delta = home_rating - away_rating
        if math.isclose(rating_delta, 0.0, abs_tol=1e-9):
            reason = 'same_competition_strength'
        else:
            reason = 'different_competition_strength'
            context_weight = (
                CROSS_LEAGUE_FRIENDLY_WEIGHT
                if int(fixture_league_id) == 667
                else 1.0
            )
            effective_delta = max(
                -CROSS_LEAGUE_RATING_DELTA_LIMIT,
                min(
                    CROSS_LEAGUE_RATING_DELTA_LIMIT,
                    rating_delta * context_weight,
                ),
            )
            goal_ratio_multiplier = 10.0 ** (
                effective_delta / CROSS_LEAGUE_RATING_SCALE
            )
            raw_ratio = raw_home_goals / raw_away_goals
            adjusted_ratio = raw_ratio * goal_ratio_multiplier
            total_goals = raw_home_goals + raw_away_goals
            adjusted_home = _bounded_goal_mean(
                total_goals * adjusted_ratio / (1.0 + adjusted_ratio)
            )
            adjusted_away = _bounded_goal_mean(
                total_goals / (1.0 + adjusted_ratio)
            )
            applied = True

    adjusted_expected = {
        'home': round(adjusted_home, 4),
        'away': round(adjusted_away, 4),
    }
    total_preserved = math.isclose(
        raw_home_goals + raw_away_goals,
        adjusted_home + adjusted_away,
        abs_tol=0.001,
    )
    home_rating_value = home_source.get('rating')
    away_rating_value = away_source.get('rating')
    metadata = {
        'applied': applied,
        'reason': reason,
        'method': 'bounded_competition_strength_goal_share',
        'competition_strength_version': CROSS_LEAGUE_CALIBRATION_VERSION,
        'home_source': home_source,
        'away_source': away_source,
        'home_rating': home_rating_value,
        'away_rating': away_rating_value,
        'rating_delta': (
            round(rating_delta, 4) if rating_delta is not None else None
        ),
        'effective_delta': round(effective_delta, 4),
        'friendly_weight': (
            CROSS_LEAGUE_FRIENDLY_WEIGHT
            if int(fixture_league_id) == 667
            else 1.0
        ),
        'home_multiplier': round(adjusted_home / raw_home_goals, 6),
        'away_multiplier': round(adjusted_away / raw_away_goals, 6),
        'raw_expected_goals': raw_expected,
        'adjusted_expected_goals': adjusted_expected,
        'bounds': {
            'rating_delta': [
                -CROSS_LEAGUE_RATING_DELTA_LIMIT,
                CROSS_LEAGUE_RATING_DELTA_LIMIT,
            ],
            'goal_mean': [GOAL_MEAN_MIN, GOAL_MEAN_MAX],
        },
        'total_preserved': total_preserved,
    }
    return adjusted_home, adjusted_away, metadata


def _profile_sample_matches(
    profile: LocalTeamProfile,
    season_key: str,
) -> float:
    metadata = profile.values.get('metadata')
    if not isinstance(metadata, Mapping):
        return _nonnegative_float(profile.values.get('season_mp')) or 0.0
    sample_sizes = metadata.get('sample_sizes')
    if not isinstance(sample_sizes, Mapping):
        return _nonnegative_float(profile.values.get('season_mp')) or 0.0
    statistic_key = _HISTORY_STAT_SAMPLE_KEYS.get(season_key)
    metrics = sample_sizes.get('metrics')
    if statistic_key and isinstance(metrics, Mapping):
        metric = metrics.get(statistic_key)
        if isinstance(metric, Mapping):
            return _nonnegative_float(metric.get('season')) or 0.0
    return (
        _nonnegative_float(sample_sizes.get('finished_matches'))
        or _nonnegative_float(profile.values.get('season_mp'))
        or 0.0
    )


def _profile_empirical_bayes_rate(
    profile: LocalTeamProfile,
    *,
    season_key: str,
    recent_keys: tuple[str, ...],
) -> tuple[float, dict[str, Any]]:
    source_kind = _profile_source_kind(profile)
    source_league_code = _profile_source_league_code(profile)
    prior_league_code = _profile_prior_league_code(profile)
    prior, prior_rows = _league_season_prior(
        prior_league_code,
        season_key,
    )
    if prior is None:
        raise PredictionInputError(
            f'No {prior_league_code} prior is available for {season_key}.'
        )
    season_rate = _nonnegative_float(profile.values.get(season_key))
    matches = _profile_sample_matches(profile, season_key)
    if season_rate is None or matches <= 0:
        posterior = prior
        season_sample_used = False
    else:
        posterior = (
            matches * season_rate + PROFILE_PRIOR_STRENGTH_MATCHES * prior
        ) / (matches + PROFILE_PRIOR_STRENGTH_MATCHES)
        season_sample_used = True
    recent = _profile_mean(profile, recent_keys)
    recent_weight = RECENT_ADJUSTMENT_WEIGHT if recent is not None else 0.0
    estimate = (
        (1.0 - recent_weight) * posterior + recent_weight * recent
        if recent is not None
        else posterior
    )
    return estimate, {
        'source': f'{source_kind}_empirical_bayes',
        'source_kind': source_kind,
        'league_code': source_league_code,
        'source_league_code': source_league_code,
        'prior_league_code': prior_league_code,
        'profile_name': profile.profile_name,
        'season_key': season_key,
        'season_matches': round(matches),
        'season_sample_used': season_sample_used,
        'league_prior': round(prior, 4),
        'prior_rows': prior_rows,
        'prior_strength_matches': PROFILE_PRIOR_STRENGTH_MATCHES,
        'posterior_before_recent_adjustment': round(posterior, 4),
        'recent_keys': list(recent_keys),
        'recent_value': round(recent, 4) if recent is not None else None,
        'recent_adjustment_weight': recent_weight,
    }


def _bounded_goal_mean(value: float) -> float:
    return max(GOAL_MEAN_MIN, min(GOAL_MEAN_MAX, value))


def _goal_component(
    profile: LocalTeamProfile | None,
    *,
    profile_key: str,
    reference_league_code: str,
) -> tuple[float, dict[str, Any]]:
    season_key, recent_keys = _GOAL_SPECS[profile_key]
    if profile is not None:
        return _profile_empirical_bayes_rate(
            profile,
            season_key=season_key,
            recent_keys=recent_keys,
        )
    value, rows = _league_season_prior(reference_league_code, season_key)
    if value is None:
        raise PredictionInputError(
            f'No neutral {reference_league_code} prior is available for {profile_key}.'
        )
    return value, {
        'source': 'neutral_league_profile_prior',
        'league_code': (
            None
            if reference_league_code == GLOBAL_TEAM_HISTORY_PRIOR_CODE
            else reference_league_code
        ),
        'source_league_code': None,
        'prior_league_code': reference_league_code,
        'profile_rows': rows,
        'season_key': season_key,
        'recent_adjustment_weight': 0.0,
    }


def _statistic_projection(
    *,
    side: str,
    profile: LocalTeamProfile | None,
    reference_league_code: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    expected: dict[str, float] = {}
    metrics: dict[str, Any] = {}
    for metric, (season_key, recent_keys) in _STAT_SPECS.items():
        if profile is not None:
            value, evidence = _profile_empirical_bayes_rate(
                profile,
                season_key=season_key,
                recent_keys=recent_keys,
            )
            rows = int(evidence['prior_rows'])
            source_kind = str(evidence['source_kind'])
            has_team_metric = bool(evidence['season_sample_used'])
            status = (
                'profile_estimate'
                if source_kind == 'local_team_profile' or has_team_metric
                else 'unavailable'
            )
            source_league_code = _profile_source_league_code(profile)
            prior_league_code = _profile_prior_league_code(profile)
            team_sample_used = has_team_metric
            publish_value = (
                source_kind == 'local_team_profile' or has_team_metric
            )
        else:
            value, rows = _league_season_prior(
                reference_league_code,
                season_key,
            )
            status = 'reference_only' if value is not None else 'unavailable'
            source_league_code = None
            prior_league_code = reference_league_code
            team_sample_used = False
            publish_value = False
            evidence = {
                'season_key': season_key,
                'recent_keys': list(recent_keys),
                'league_prior': round(value, 4) if value is not None else None,
                'prior_rows': rows,
                'prior_strength_matches': PROFILE_PRIOR_STRENGTH_MATCHES,
                'reason': 'unknown_team_values_are_not_published',
            }
        if value is not None and publish_value:
            expected[f'{side}_{metric}'] = round(value, 2)
        metrics[metric] = {
            'status': status,
            'confidence': 'low',
            'team_sample_used': team_sample_used,
            'profile_name': profile.profile_name if profile is not None else None,
            'source_league_code': source_league_code,
            'prior_league_code': prior_league_code,
            'prior_rows': rows,
            'reference_only': profile is None,
            'published': value is not None and publish_value,
            'evidence': evidence,
        }
    return expected, {
        'source_kind': (
            _profile_source_kind(profile)
            if profile is not None
            else 'neutral_league_profile_prior'
        ),
        'source_league_code': (
            _profile_source_league_code(profile)
            if profile is not None
            else None
        ),
        'prior_league_code': (
            _profile_prior_league_code(profile)
            if profile is not None
            else reference_league_code
        ),
        'profile_name': profile.profile_name if profile is not None else None,
        'metrics': metrics,
    }


def build_calendar_profile_prediction(
    *,
    league_id: int,
    home_team_name: str,
    away_team_name: str,
    profile_overrides: Mapping[str, LocalTeamProfile] | None = None,
) -> dict[str, Any]:
    """Build a low-confidence calendar projection from stored team profiles."""

    if int(league_id) not in CALENDAR_PREDICTION_LEAGUES:
        raise PredictionInputError(f'League {league_id} has no calendar fallback.')
    profiles: dict[str, LocalTeamProfile | None] = {
        'home': local_team_profile(home_team_name),
        'away': local_team_profile(away_team_name),
    }
    for side, profile in (profile_overrides or {}).items():
        if side in profiles and profile is not None:
            profiles[side] = profile
    known_sides = [side for side, profile in profiles.items() if profile is not None]
    if not known_sides:
        raise PredictionInputError(
            'At least one team needs a local profile for the calendar fallback.'
        )

    first_profile = profiles[known_sides[0]]
    if first_profile is None:  # Defensive for type narrowing.
        raise PredictionInputError('The local profile catalog is inconsistent.')
    fallback_league_code = _profile_prior_league_code(first_profile)
    home_reference = (
        _profile_prior_league_code(profiles['home'])
        if profiles['home']
        else fallback_league_code
    )
    away_reference = (
        _profile_prior_league_code(profiles['away'])
        if profiles['away']
        else fallback_league_code
    )
    home_attack, home_attack_meta = _goal_component(
        profiles['home'],
        profile_key='home_attack',
        reference_league_code=away_reference,
    )
    home_defence, home_defence_meta = _goal_component(
        profiles['home'],
        profile_key='home_defence',
        reference_league_code=away_reference,
    )
    away_attack, away_attack_meta = _goal_component(
        profiles['away'],
        profile_key='away_attack',
        reference_league_code=home_reference,
    )
    away_defence, away_defence_meta = _goal_component(
        profiles['away'],
        profile_key='away_defence',
        reference_league_code=home_reference,
    )

    raw_home_goals = _bounded_goal_mean((home_attack + away_defence) / 2.0)
    raw_away_goals = _bounded_goal_mean((away_attack + home_defence) / 2.0)
    (
        expected_home_goals,
        expected_away_goals,
        cross_league_calibration,
    ) = _cross_league_goal_calibration(
        fixture_league_id=int(league_id),
        home_profile=profiles['home'],
        away_profile=profiles['away'],
        raw_home_goals=raw_home_goals,
        raw_away_goals=raw_away_goals,
    )
    markets = poisson_markets_from_expected_goals(
        expected_home_goals,
        expected_away_goals,
    )
    expected: dict[str, float] = {
        'home_goals': round(expected_home_goals, 3),
        'away_goals': round(expected_away_goals, 3),
    }
    statistics_teams: dict[str, Any] = {}
    for side, reference_league_code in (
        ('home', away_reference),
        ('away', home_reference),
    ):
        side_expected, side_metadata = _statistic_projection(
            side=side,
            profile=profiles[side],
            reference_league_code=reference_league_code,
        )
        expected.update(side_expected)
        statistics_teams[side] = side_metadata

    profile_metadata = {
        side: (
            {
                'status': 'available',
                'source_kind': _profile_source_kind(profile),
                'league_code': _profile_source_league_code(profile),
                'source_league_code': _profile_source_league_code(profile),
                'league_id': (
                    _profile_source_league_id(profile)
                ),
                'source_league_id': _profile_source_league_id(profile),
                'prior_league_code': _profile_prior_league_code(profile),
                'prior_league_id': _LEAGUE_CODE_TO_ID.get(
                    _profile_prior_league_code(profile)
                ),
                'profile_name': profile.profile_name,
                'history': (
                    profile.values.get('metadata')
                    if _profile_source_kind(profile)
                    == 'supabase_team_history'
                    else None
                ),
            }
            if profile is not None
            else {
                'status': 'neutral_prior_only',
                'league_code': None,
                'source_league_code': None,
                'prior_league_code': (
                    away_reference if side == 'home' else home_reference
                ),
            }
        )
        for side, profile in profiles.items()
    }
    source_kinds = {
        _profile_source_kind(profile)
        for profile in profiles.values()
        if profile is not None
    }
    return {
        'probabilities': markets['probabilities'],
        'expected': expected,
        'goal_lines': markets['goal_lines'],
        'features': {
            'known_profile_sides': known_sides,
            'single_team_profile': len(known_sides) == 1,
            'profiles': profile_metadata,
            'goal_components': {
                'home_attack': {
                    **home_attack_meta,
                    'value': round(home_attack, 4),
                },
                'home_defence': {
                    **home_defence_meta,
                    'value': round(home_defence, 4),
                },
                'away_attack': {
                    **away_attack_meta,
                    'value': round(away_attack, 4),
                },
                'away_defence': {
                    **away_defence_meta,
                    'value': round(away_defence, 4),
                },
            },
            'cross_league_calibration': cross_league_calibration,
        },
        'model': {
            'model_type': 'calendar_profile_fallback',
            'method': 'calendar_profile_poisson',
            'version': '1.3',
            'confidence': 'low',
            'league_id': int(league_id),
            'league_code': CALENDAR_PREDICTION_LEAGUES[int(league_id)][0],
            'league': CALENDAR_PREDICTION_LEAGUES[int(league_id)][1],
            'data_source': (
                'supabase_team_history'
                if source_kinds == {'supabase_team_history'}
                else (
                    'local_profiles_and_supabase_team_history'
                    if 'supabase_team_history' in source_kinds
                    else 'local_team_profiles'
                )
            ),
            'known_profile_sides': known_sides,
            'single_team_profile': len(known_sides) == 1,
            'market_odds_used': False,
            'cross_league_calibrated': cross_league_calibration['applied'],
            'cross_league_calibration': cross_league_calibration,
            'unknown_team_policy': 'neutral_prior_from_known_profile_league',
            'venue_assumption': 'neutral',
            'not_calibrated_for_friendlies': int(league_id) == 667,
            'market_statistics': {
                'method': 'local_profile_or_neutral_league_profile_prior',
                'confidence': 'low',
                'teams': statistics_teams,
            },
        },
    }
