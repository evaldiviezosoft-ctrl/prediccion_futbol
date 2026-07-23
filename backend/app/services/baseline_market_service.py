from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping


FINAL_STATUSES = frozenset({'FT', 'AET', 'PEN'})
CUP_LEAGUE_IDS = frozenset({11, 13})
DOMESTIC_LEAGUE_IDS = (71, 128, 281)
REFERENCE_STATISTICS_LEAGUE_ID = 281
TEAM_STAT_PRIOR_STRENGTH_MATCHES = 8.0
NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE = 40
NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS = 8
PLAYER_SHARE_PRIOR_EVENTS = 5.0
PLAYER_MAX_AGE_DAYS = 365

_STAT_METRICS = {
    'corners': ('home_corners', 'away_corners'),
    'total_shots': ('home_shots', 'away_shots'),
    'shots_on_goal': ('home_shots_on_target', 'away_shots_on_target'),
}


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 and math.isfinite(parsed) else None


def _eligible_fixture_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Apply the temporal/final-state guard independently of the repository."""

    eligible: list[dict[str, Any]] = []
    seen: set[int] = set()
    for source in rows:
        fixture_id = _positive_int(source.get('id') or source.get('api_fixture_id'))
        league_id = _positive_int(source.get('league_id'))
        if fixture_id is None or league_id is None or fixture_id in seen:
            continue
        if str(source.get('status_short') or '').upper() not in FINAL_STATUSES:
            continue
        try:
            kickoff = _utc_datetime(source.get('kickoff') or source.get('fixture_date_utc'))
        except (TypeError, ValueError):
            continue
        if kickoff >= cutoff:
            continue
        row = dict(source)
        row['_fixture_id'] = fixture_id
        row['_league_id'] = league_id
        row['_kickoff'] = kickoff
        eligible.append(row)
        seen.add(fixture_id)
    eligible.sort(key=lambda row: (row['_kickoff'], row['_fixture_id']))
    return eligible


def _team_appears(row: Mapping[str, Any], api_team_id: int) -> bool:
    return api_team_id in {
        _positive_int(row.get('home_team_id')),
        _positive_int(row.get('away_team_id')),
    }


def choose_team_history_sources(
    *,
    target_league_id: int,
    home_team_id: int,
    away_team_id: int,
    home_team_ref_id: int,
    away_team_ref_id: int,
    target_kickoff: datetime | str,
    historical_fixture_rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Choose a domestic league for cup teams, otherwise the target competition.

    Selection uses only finished fixtures before the target kickoff. The returned
    private fixture-id lists are used to batch-read statistics and are omitted
    from public metadata by :func:`public_history_sources`.
    """

    cutoff = _utc_datetime(target_kickoff)
    eligible = _eligible_fixture_rows(historical_fixture_rows, cutoff=cutoff)
    sources: dict[str, dict[str, Any]] = {}
    targets = {
        'home': (int(home_team_id), int(home_team_ref_id), True),
        'away': (int(away_team_id), int(away_team_ref_id), False),
    }
    for side, (api_team_id, team_ref_id, is_home) in targets.items():
        selected_league_id = int(target_league_id)
        source_kind = 'target_league'
        if target_league_id in CUP_LEAGUE_IDS:
            counts = Counter(
                row['_league_id']
                for row in eligible
                if row['_league_id'] in DOMESTIC_LEAGUE_IDS
                and _team_appears(row, api_team_id)
            )
            if counts:
                selected_league_id = min(
                    counts,
                    key=lambda league_id: (-counts[league_id], DOMESTIC_LEAGUE_IDS.index(league_id)),
                )
                source_kind = 'domestic_league'
            else:
                source_kind = 'target_competition_fallback'

        league_rows = [
            row for row in eligible if row['_league_id'] == selected_league_id
        ]
        team_rows = [row for row in league_rows if _team_appears(row, api_team_id)]
        sources[side] = {
            'team_api_id': api_team_id,
            'team_ref_id': team_ref_id,
            'venue': 'home' if is_home else 'away',
            'source_kind': source_kind,
            'source_league_id': selected_league_id,
            'eligible_league_fixtures': len(league_rows),
            'eligible_team_matches': len(team_rows),
            '_source_fixture_ids': tuple(row['_fixture_id'] for row in league_rows),
            '_team_fixture_ids': tuple(row['_fixture_id'] for row in team_rows),
            '_last_team_kickoff': (
                team_rows[-1]['_kickoff'].isoformat() if team_rows else None
            ),
        }
    return sources, eligible


def public_history_sources(sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        side: {
            key: value
            for key, value in source.items()
            if not key.startswith('_')
        }
        for side, source in sources.items()
    }


def _stat_rows_by_fixture(
    rows: Iterable[Mapping[str, Any]],
    *,
    allowed_fixture_ids: set[int],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for source in rows:
        fixture_id = _positive_int(source.get('fixture_id'))
        team_id = _positive_int(source.get('team_id'))
        if fixture_id not in allowed_fixture_ids or team_id is None:
            continue
        key = (fixture_id, team_id)
        if key in seen:
            continue
        row = dict(source)
        row['_fixture_id'] = fixture_id
        row['_team_id'] = team_id
        result.append(row)
        seen.add(key)
    return result


def estimate_team_statistics(
    *,
    sources: Mapping[str, Mapping[str, Any]],
    eligible_fixture_rows: Iterable[Mapping[str, Any]],
    team_statistics_rows: Iterable[Mapping[str, Any]],
    prior_strength: float = TEAM_STAT_PRIOR_STRENGTH_MATCHES,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Estimate team counts with venue-specific Empirical-Bayes means.

    If the selected domestic/competition source has no detailed statistics,
    Liga 1 Peru is used only as an explicitly labelled cross-league reference.
    A missing reference sample results in omitted values rather than fabricated
    numbers.
    """

    if prior_strength <= 0 or not math.isfinite(prior_strength):
        raise ValueError('prior_strength must be finite and positive.')
    fixtures = list(eligible_fixture_rows)
    fixture_leagues = {
        int(row['_fixture_id']): int(row['_league_id'])
        for row in fixtures
        if row.get('_fixture_id') is not None and row.get('_league_id') is not None
    }
    allowed_fixture_ids = set(fixture_leagues)
    statistics = _stat_rows_by_fixture(
        team_statistics_rows,
        allowed_fixture_ids=allowed_fixture_ids,
    )

    expected: dict[str, float] = {}
    side_metadata: dict[str, Any] = {}
    for side in ('home', 'away'):
        source = sources[side]
        selected_league_id = int(source['source_league_id'])
        team_ref_id = int(source['team_ref_id'])
        desired_home = side == 'home'
        metrics_metadata: dict[str, Any] = {}
        for metric, output_keys in _STAT_METRICS.items():
            def candidates(
                league_id: int,
                *,
                home_venue: bool | None,
            ) -> tuple[list[float], set[int]]:
                values: list[float] = []
                team_ids: set[int] = set()
                for row in statistics:
                    if fixture_leagues.get(row['_fixture_id']) != league_id:
                        continue
                    if (
                        home_venue is not None
                        and bool(row.get('is_home')) != home_venue
                    ):
                        continue
                    value = _nonnegative_float(row.get(metric))
                    if value is not None:
                        values.append(value)
                        team_ids.add(int(row['_team_id']))
                return values, team_ids

            selected_values, selected_team_ids = candidates(
                selected_league_id,
                home_venue=desired_home,
            )
            opposite_values, opposite_team_ids = candidates(
                selected_league_id,
                home_venue=not desired_home,
            )
            coverage_gate_applies = (
                selected_league_id != REFERENCE_STATISTICS_LEAGUE_ID
            )
            selected_prior_qualified = (
                bool(selected_values)
                if not coverage_gate_applies
                else (
                    len(selected_values) >= NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE
                    and len(selected_team_ids)
                    >= NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS
                    and len(opposite_values)
                    >= NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE
                    and len(opposite_team_ids)
                    >= NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS
                )
            )
            coverage_gate = {
                'applies': coverage_gate_applies,
                'required_values': NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
                'required_distinct_teams': NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
                'observed_values': {
                    'home': (
                        len(selected_values) if desired_home else len(opposite_values)
                    ),
                    'away': (
                        len(opposite_values) if desired_home else len(selected_values)
                    ),
                },
                'observed_distinct_teams': {
                    'home': (
                        len(selected_team_ids)
                        if desired_home
                        else len(opposite_team_ids)
                    ),
                    'away': (
                        len(opposite_team_ids)
                        if desired_home
                        else len(selected_team_ids)
                    ),
                },
                'qualified': selected_prior_qualified,
            }

            prior_values = selected_values if selected_prior_qualified else []
            prior_league_id = selected_league_id
            prior_scope = 'selected_league_venue'
            if prior_values:
                prior_selection_reason = (
                    'configured_reference_league'
                    if not coverage_gate_applies
                    else 'selected_league_coverage_qualified'
                )
            elif not coverage_gate_applies:
                prior_values, _reference_team_ids = candidates(
                    selected_league_id,
                    home_venue=None,
                )
                prior_scope = 'selected_league_all_venues'
                prior_selection_reason = (
                    'configured_reference_league_all_venues'
                    if prior_values
                    else 'configured_reference_league_unavailable'
                )
            else:
                prior_values, _reference_team_ids = candidates(
                    REFERENCE_STATISTICS_LEAGUE_ID,
                    home_venue=desired_home,
                )
                prior_league_id = REFERENCE_STATISTICS_LEAGUE_ID
                prior_scope = 'cross_league_reference_venue'
                if not prior_values:
                    prior_values, _reference_team_ids = candidates(
                        REFERENCE_STATISTICS_LEAGUE_ID,
                        home_venue=None,
                    )
                    prior_scope = 'cross_league_reference_all_venues'
                prior_selection_reason = (
                    'selected_league_coverage_insufficient_using_reference'
                    if prior_values
                    else 'selected_league_coverage_insufficient_reference_unavailable'
                )

            source_fixture_ids = set(source['_source_fixture_ids'])
            team_values = [
                value
                for row in statistics
                if row['_fixture_id'] in source_fixture_ids
                and row['_team_id'] == team_ref_id
                and bool(row.get('is_home')) == desired_home
                if (value := _nonnegative_float(row.get(metric))) is not None
            ]
            if not prior_values:
                metrics_metadata[metric] = {
                    'status': 'unavailable',
                    'team_rows': len(team_values),
                    'prior_rows': 0,
                    'prior_selection_reason': prior_selection_reason,
                    'coverage_gate': coverage_gate,
                }
                continue

            prior_mean = sum(prior_values) / len(prior_values)
            estimate = (
                sum(team_values) + prior_strength * prior_mean
            ) / (len(team_values) + prior_strength)
            output_key = output_keys[0] if side == 'home' else output_keys[1]
            expected[output_key] = round(estimate, 2)
            reference_only = not team_values
            cross_league_reference = prior_league_id != selected_league_id
            metrics_metadata[metric] = {
                'status': 'reference_only' if reference_only else 'estimated',
                'team_rows': len(team_values),
                'prior_rows': len(prior_values),
                'prior_mean': round(prior_mean, 4),
                'prior_league_id': prior_league_id,
                'prior_scope': prior_scope,
                'prior_selection_reason': prior_selection_reason,
                'coverage_gate': coverage_gate,
                'team_sample_used': bool(team_values),
                'cross_league_reference': cross_league_reference,
                'confidence': 'low' if reference_only or cross_league_reference else 'limited',
            }
        side_metadata[side] = {
            **public_history_sources({side: source})[side],
            'metrics': metrics_metadata,
        }

    return expected, {
        'method': 'empirical_bayes_team_venue_means',
        'prior_strength_matches': prior_strength,
        'reference_statistics_league_id': REFERENCE_STATISTICS_LEAGUE_ID,
        'non_reference_prior_activation': {
            'minimum_valid_values_per_metric_and_venue':
                NON_REFERENCE_PRIOR_MIN_VALUES_PER_VENUE,
            'minimum_distinct_teams':
                NON_REFERENCE_PRIOR_MIN_DISTINCT_TEAMS,
        },
        'cutoff_rule': 'statistics joined only to status FT/AET/PEN fixtures with kickoff < target kickoff',
        'teams': side_metadata,
    }


def _player_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    fixture_ids: set[int],
    team_ref_id: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for source in rows:
        fixture_id = _positive_int(source.get('fixture_id'))
        player_id = _positive_int(source.get('player_id'))
        team_id = _positive_int(source.get('team_id'))
        if fixture_id not in fixture_ids or player_id is None or team_id != team_ref_id:
            continue
        key = (fixture_id, player_id)
        if key in seen:
            continue
        row = dict(source)
        row['_fixture_id'] = fixture_id
        row['_player_id'] = player_id
        result.append(row)
        seen.add(key)
    return result


def estimate_player_candidates(
    *,
    sources: Mapping[str, Mapping[str, Any]],
    target_kickoff: datetime | str,
    expected_goals: Mapping[str, float],
    player_statistics_rows: Iterable[Mapping[str, Any]],
    players_by_id: Mapping[int, Mapping[str, Any]],
    team_names: Mapping[str, str],
    top_n: int = 5,
    max_age_days: int = PLAYER_MAX_AGE_DAYS,
    prior_events: float = PLAYER_SHARE_PRIOR_EVENTS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Rank evidenced historical scorers/assistants with exposure shrinkage.

    Candidates require at least one observed event and recent team statistics.
    This deliberately returns an empty list for stale data because historical
    names are not evidence of the current roster.
    """

    if top_n <= 0 or max_age_days < 0 or prior_events <= 0:
        raise ValueError('Candidate limits and priors must be positive.')
    cutoff = _utc_datetime(target_kickoff)
    all_rows = list(player_statistics_rows)
    scorers: list[dict[str, Any]] = []
    assistants: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        'method': 'exposure_weighted_event_share_empirical_bayes',
        'prior_events': prior_events,
        'max_age_days': max_age_days,
        'active_roster_verified': False,
        'cutoff_rule': 'player rows joined only to finished fixtures before target kickoff',
        'teams': {},
    }

    for side in ('home', 'away'):
        source = sources[side]
        fixture_ids = set(source['_team_fixture_ids'])
        team_ref_id = int(source['team_ref_id'])
        rows = _player_rows(
            all_rows,
            fixture_ids=fixture_ids,
            team_ref_id=team_ref_id,
        )
        last_kickoff_raw = source.get('_last_team_kickoff')
        last_kickoff = _utc_datetime(last_kickoff_raw) if last_kickoff_raw else None
        age_days = (cutoff - last_kickoff).days if last_kickoff else None
        team_meta: dict[str, Any] = {
            'source_league_id': source['source_league_id'],
            'source_kind': source['source_kind'],
            'statistics_rows': len(rows),
            'last_observation': last_kickoff.isoformat() if last_kickoff else None,
            'age_days': age_days,
        }
        if not rows:
            team_meta['status'] = 'no_player_sample'
            metadata['teams'][side] = team_meta
            continue
        if age_days is None or age_days > max_age_days:
            team_meta['status'] = 'insufficient_freshness'
            metadata['teams'][side] = team_meta
            continue

        aggregates: dict[int, dict[str, float]] = {}
        for row in rows:
            player_id = int(row['_player_id'])
            aggregate = aggregates.setdefault(
                player_id,
                {'appearances': 0.0, 'minutes': 0.0, 'goals': 0.0, 'assists': 0.0},
            )
            minutes = _nonnegative_float(row.get('minutes')) or 0.0
            appeared = minutes > 0 or bool(row.get('starter')) or bool(row.get('substitute'))
            appeared = appeared or any(
                (_nonnegative_float(row.get(key)) or 0.0) > 0
                for key in ('goals', 'assists')
            )
            if not appeared:
                continue
            aggregate['appearances'] += 1.0
            aggregate['minutes'] += minutes
            aggregate['goals'] += _nonnegative_float(row.get('goals')) or 0.0
            aggregate['assists'] += _nonnegative_float(row.get('assists')) or 0.0

        aggregates = {
            player_id: value
            for player_id, value in aggregates.items()
            if value['appearances'] > 0 and players_by_id.get(player_id, {}).get('name')
        }
        total_exposure = sum(
            value['minutes'] / 90.0 + value['appearances'] * 0.25
            for value in aggregates.values()
        )
        if not aggregates or total_exposure <= 0:
            team_meta['status'] = 'no_named_player_sample'
            metadata['teams'][side] = team_meta
            continue

        team_meta.update({
            'status': 'historical_candidates_available',
            'players_in_sample': len(aggregates),
            'total_minutes': round(sum(value['minutes'] for value in aggregates.values())),
        })
        team_lambda = max(0.0, float(expected_goals.get(f'{side}_goals', 0.0)))
        for event_key, output in (('goals', scorers), ('assists', assistants)):
            total_events = sum(value[event_key] for value in aggregates.values())
            team_meta[f'historical_{event_key}'] = round(total_events)
            if total_events <= 0:
                continue
            for player_id, aggregate in aggregates.items():
                observed_events = aggregate[event_key]
                if observed_events <= 0:
                    # Shrinkage must not manufacture a named candidate without
                    # at least one observed scorer/assist event.
                    continue
                exposure = aggregate['minutes'] / 90.0 + aggregate['appearances'] * 0.25
                prior_share = exposure / total_exposure
                posterior_share = (
                    observed_events + prior_events * prior_share
                ) / (total_events + prior_events)
                event_lambda = team_lambda * posterior_share
                probability = 1.0 - math.exp(-event_lambda)
                output.append({
                    'player_id': player_id,
                    'player': str(players_by_id[player_id]['name']),
                    'team': str(team_names[side]),
                    'team_id': int(source['team_api_id']),
                    'probability': round(probability, 4),
                    'appearances': int(aggregate['appearances']),
                    'minutes': round(aggregate['minutes']),
                    f'historical_{event_key}': round(observed_events),
                    'source_league_id': int(source['source_league_id']),
                })
        metadata['teams'][side] = team_meta

    scorers.sort(key=lambda item: (-item['probability'], item['player_id']))
    assistants.sort(key=lambda item: (-item['probability'], item['player_id']))
    return scorers[:top_n], assistants[:top_n], metadata
