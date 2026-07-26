from __future__ import annotations

import math
from typing import Any, Mapping


MIN_OVER_PROBABILITY = 0.60
FRIENDLY_LEAGUE_ID = 667


def _finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _poisson_cdf(maximum: int, mean: float) -> float:
    if maximum < 0:
        return 0.0
    if mean <= 0:
        return 1.0
    probability = math.exp(-mean)
    total = probability
    for value in range(1, maximum + 1):
        probability *= mean / value
        total += probability
    return min(1.0, max(0.0, total))


def _over_probability(mean: float, line: float) -> float:
    return 1.0 - _poisson_cdf(math.floor(line), mean)


def _poisson_quantile(mean: float, probability: float) -> int:
    if mean <= 0:
        return 0
    probability = min(1.0, max(0.0, probability))
    mass = math.exp(-mean)
    cumulative = mass
    value = 0
    upper_guard = max(100, math.ceil(mean * 4 + 20))
    while cumulative < probability and value < upper_guard:
        value += 1
        mass *= mean / value
        cumulative += mass
    return value


def _confidence(probability: float, *, low_quality: bool) -> str:
    if low_quality:
        return 'low'
    if probability >= 0.78:
        return 'high'
    if probability >= 0.64:
        return 'medium'
    return 'low'


def _over_pick(
    *,
    category: str,
    title: str,
    prediction: str,
    probability: float,
    low_quality: bool,
) -> dict[str, Any] | None:
    if probability < MIN_OVER_PROBABILITY:
        return None
    return {
        'category': category,
        'title': title,
        'prediction': prediction,
        'probability': round(probability, 4),
        'confidence': _confidence(probability, low_quality=low_quality),
    }


def _range_pick(
    *,
    category: str,
    title: str,
    mean: float,
    low_quality: bool,
) -> dict[str, Any]:
    minimum = _poisson_quantile(mean, 0.15)
    maximum = _poisson_quantile(mean, 0.85)
    return {
        'category': category,
        'title': title,
        'prediction': f'{minimum}–{maximum}',
        'probability': None,
        'confidence': 'low' if low_quality else 'medium',
    }


def _known_sides(metadata: Mapping[str, Any]) -> set[str] | None:
    if not bool(metadata.get('single_team_profile')):
        return None
    raw = metadata.get('known_profile_sides')
    if not isinstance(raw, list):
        return set()
    return {
        str(side).lower()
        for side in raw
        if str(side).lower() in {'home', 'away'}
    }


def _expected_value(
    expected: Mapping[str, Any],
    key: str,
    *,
    known_sides: set[str] | None,
) -> float | None:
    side = (
        'home'
        if key.startswith('home_')
        else 'away'
        if key.startswith('away_')
        else None
    )
    if side is not None and known_sides is not None and side not in known_sides:
        return None
    return _finite_nonnegative(expected.get(key))


def _metric_is_publishable(
    metadata: Mapping[str, Any],
    *,
    side: str,
    metric: str,
) -> bool:
    """Reject reference-only values while preserving trained model bundles."""

    market_statistics = metadata.get('market_statistics')
    if not isinstance(market_statistics, Mapping):
        return True
    teams = market_statistics.get('teams')
    if not isinstance(teams, Mapping):
        return False
    team = teams.get(side)
    if not isinstance(team, Mapping):
        return False
    metrics = team.get('metrics')
    if not isinstance(metrics, Mapping):
        return False
    evidence = metrics.get(metric)
    if not isinstance(evidence, Mapping):
        return False
    status = str(evidence.get('status') or '').lower()
    if status in {'', 'unavailable', 'reference_only'}:
        return False
    if 'published' in evidence:
        return bool(evidence.get('published'))
    return (
        bool(evidence.get('team_sample_used'))
        and not bool(evidence.get('cross_league_reference'))
    )


def _market_value(
    expected: Mapping[str, Any],
    metadata: Mapping[str, Any],
    key: str,
    *,
    metric: str,
    known_sides: set[str] | None,
) -> float | None:
    side = 'home' if key.startswith('home_') else 'away'
    value = _expected_value(expected, key, known_sides=known_sides)
    if value is None or not _metric_is_publishable(
        metadata,
        side=side,
        metric=metric,
    ):
        return None
    return value


def _goal_pick(
    prediction: Mapping[str, Any],
    *,
    low_quality: bool,
) -> dict[str, Any] | None:
    metadata = prediction.get('model_metadata')
    metadata = metadata if isinstance(metadata, Mapping) else {}
    rows = metadata.get('goal_lines')
    probability: float | None = None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            line = _finite_nonnegative(row.get('line'))
            candidate_probability = _finite_nonnegative(row.get('probability'))
            if (
                line == 1.5
                and candidate_probability is not None
                and candidate_probability <= 1
            ):
                probability = candidate_probability
                break
    if probability is None:
        return None
    return _over_pick(
        category='goals',
        title='Goles totales',
        prediction='Más de 1.5',
        probability=probability,
        low_quality=low_quality,
    )


def build_probable_forecast(
    prediction: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build only model-backed markets that the app is allowed to publish.

    This function is deterministic and performs no provider or AI calls. The
    AI may later rank these server-created picks, but it cannot create values.
    """

    expected = prediction.get('expected')
    expected = expected if isinstance(expected, Mapping) else {}
    metadata = prediction.get('model_metadata')
    metadata = metadata if isinstance(metadata, Mapping) else {}
    known_sides = _known_sides(metadata)
    low_quality = (
        str(metadata.get('confidence') or '').lower() == 'low'
        or known_sides is not None
    )
    league_id = int(prediction.get('league_id') or 0)
    official_match = league_id != FRIENDLY_LEAGUE_ID
    home_name = str(prediction.get('home_team_name') or 'Local')
    away_name = str(prediction.get('away_team_name') or 'Visitante')

    picks: list[dict[str, Any]] = []
    goal_pick = (
        _goal_pick(prediction, low_quality=low_quality)
        if known_sides is None
        else None
    )
    if goal_pick is not None:
        picks.append(goal_pick)

    home_corners = _market_value(
        expected,
        metadata,
        'home_corners',
        metric='corners',
        known_sides=known_sides,
    )
    away_corners = _market_value(
        expected,
        metadata,
        'away_corners',
        metric='corners',
        known_sides=known_sides,
    )
    corner_candidates: list[tuple[str, float]] = []
    if home_corners is not None:
        corner_candidates.append((
            f'Más de 4.5 · {home_name}',
            _over_probability(home_corners, 4.5),
        ))
    if away_corners is not None:
        corner_candidates.append((
            f'Más de 4.5 · {away_name}',
            _over_probability(away_corners, 4.5),
        ))
    if home_corners is not None and away_corners is not None:
        corner_candidates.append((
            'Más de 7.5',
            _over_probability(home_corners + away_corners, 7.5),
        ))
    if corner_candidates:
        label, probability = max(corner_candidates, key=lambda item: item[1])
        pick = _over_pick(
            category='corners',
            title='Córners',
            prediction=label,
            probability=probability,
            low_quality=low_quality,
        )
        if pick is not None:
            picks.append(pick)

    home_goals = _expected_value(
        expected, 'home_goals', known_sides=known_sides
    )
    away_goals = _expected_value(
        expected, 'away_goals', known_sides=known_sides
    )
    if home_goals is not None and away_goals is not None:
        total_goals = home_goals + away_goals
        half_candidates = [
            ('Más de 0.5 · 1.er tiempo', 1.0 - math.exp(-total_goals * 0.45)),
            ('Más de 0.5 · 2.º tiempo', 1.0 - math.exp(-total_goals * 0.55)),
        ]
        label, probability = max(half_candidates, key=lambda item: item[1])
        pick = _over_pick(
            category='half_goals',
            title='Gol por tiempo',
            prediction=label,
            probability=probability,
            # The current bundles do not have a dedicated half-goal target.
            low_quality=True,
        )
        if pick is not None:
            picks.append(pick)

    if official_match:
        home_cards = _market_value(
            expected,
            metadata,
            'home_yellow_cards',
            metric='yellow_cards',
            known_sides=known_sides,
        )
        away_cards = _market_value(
            expected,
            metadata,
            'away_yellow_cards',
            metric='yellow_cards',
            known_sides=known_sides,
        )
        card_candidates: list[tuple[str, float]] = []
        if home_cards is not None:
            card_candidates.append((
                f'Más de 0.5 · {home_name}',
                _over_probability(home_cards, 0.5),
            ))
        if away_cards is not None:
            card_candidates.append((
                f'Más de 0.5 · {away_name}',
                _over_probability(away_cards, 0.5),
            ))
        if card_candidates:
            label, probability = max(card_candidates, key=lambda item: item[1])
            if (
                probability < MIN_OVER_PROBABILITY
                and home_cards is not None
                and away_cards is not None
            ):
                label = 'Más de 0.5 · Total'
                probability = _over_probability(
                    home_cards + away_cards,
                    0.5,
                )
            pick = _over_pick(
                category='cards',
                title='Tarjetas amarillas',
                prediction=label,
                probability=probability,
                low_quality=low_quality,
            )
            if pick is not None:
                picks.append(pick)

    for category, title, home_key, away_key in (
        ('shots', 'Remates totales', 'home_shots', 'away_shots'),
        (
            'shots_on_target',
            'Remates al arco',
            'home_shots_on_target',
            'away_shots_on_target',
        ),
    ):
        metric = (
            'shots' if category == 'shots' else 'shots_on_target'
        )
        home_value = _market_value(
            expected,
            metadata,
            home_key,
            metric=metric,
            known_sides=known_sides,
        )
        away_value = _market_value(
            expected,
            metadata,
            away_key,
            metric=metric,
            known_sides=known_sides,
        )
        if home_value is not None and away_value is not None:
            picks.append(_range_pick(
                category=category,
                title=title,
                mean=home_value + away_value,
                low_quality=low_quality,
            ))

    if official_match:
        home_saves = _market_value(
            expected,
            metadata,
            'home_goalkeeper_saves',
            metric='goalkeeper_saves',
            known_sides=known_sides,
        )
        away_saves = _market_value(
            expected,
            metadata,
            'away_goalkeeper_saves',
            metric='goalkeeper_saves',
            known_sides=known_sides,
        )
        if home_saves is not None and away_saves is not None:
            picks.append(_range_pick(
                category='saves',
                title='Atajadas totales',
                mean=home_saves + away_saves,
                low_quality=low_quality,
            ))

    order = {
        'goals': 0,
        'corners': 1,
        'half_goals': 2,
        'cards': 3,
        'shots': 4,
        'saves': 5,
        'shots_on_target': 6,
    }
    return sorted(picks, key=lambda pick: order[pick['category']])
