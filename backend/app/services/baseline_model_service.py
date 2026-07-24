from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from app.core.errors import PredictionInputError


BASELINE_FINAL_STATUSES = frozenset({'FT', 'AET', 'PEN'})
BASELINE_UPCOMING_STATUSES = frozenset({'NS', 'TBD', 'PST'})
BASELINE_PRIOR_STRENGTH_MATCHES = 8.0
MIN_BASELINE_TRAINING_MATCHES = 20


@dataclass(frozen=True, slots=True)
class BaselineLeague:
    league_id: int
    code: str
    name: str


BASELINE_LEAGUES: dict[int, BaselineLeague] = {
    11: BaselineLeague(11, 'copa_sudamericana', 'CONMEBOL Sudamericana'),
    13: BaselineLeague(13, 'copa_libertadores', 'CONMEBOL Libertadores'),
    71: BaselineLeague(71, 'brazil_serie_a', 'Serie A (Brasil)'),
    128: BaselineLeague(128, 'argentina_liga_profesional', 'Liga Profesional Argentina'),
    281: BaselineLeague(281, 'peru_liga_1', 'Primera Division (Peru)'),
}
BASELINE_LEAGUE_IDS = frozenset(BASELINE_LEAGUES)


@dataclass(frozen=True, slots=True)
class _Match:
    fixture_id: int
    league_id: int
    season: int | None
    kickoff: datetime
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _positive_int(value: Any) -> int | None:
    parsed = _nonnegative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _historical_matches(
    rows: Iterable[Mapping[str, Any]],
    *,
    league_id: int,
    cutoff: datetime,
) -> list[_Match]:
    """Validate the repository result and enforce the leakage guard again in memory."""

    matches: list[_Match] = []
    for row in rows:
        if str(row.get('status_short') or '').upper() not in BASELINE_FINAL_STATUSES:
            continue
        row_league_id = _positive_int(row.get('league_id'))
        fixture_id = _positive_int(row.get('id') or row.get('api_fixture_id'))
        home_team_id = _positive_int(row.get('home_team_id'))
        away_team_id = _positive_int(row.get('away_team_id'))
        home_goals = _nonnegative_int(row.get('home_goals'))
        away_goals = _nonnegative_int(row.get('away_goals'))
        try:
            kickoff = _utc_datetime(row.get('kickoff') or row.get('fixture_date_utc'))
        except (TypeError, ValueError):
            continue
        if (
            row_league_id != league_id
            or fixture_id is None
            or home_team_id is None
            or away_team_id is None
            or home_goals is None
            or away_goals is None
            or kickoff >= cutoff
        ):
            continue
        season = _nonnegative_int(row.get('season'))
        matches.append(
            _Match(
                fixture_id=fixture_id,
                league_id=row_league_id,
                season=season,
                kickoff=kickoff,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                home_goals=home_goals,
                away_goals=away_goals,
            )
        )
    matches.sort(key=lambda match: (match.kickoff, match.fixture_id))
    return matches


def _posterior_rate(
    *,
    goals: int,
    matches: int,
    league_rate: float,
    prior_strength: float,
) -> float:
    return (goals + prior_strength * league_rate) / (matches + prior_strength)


def _poisson_distribution(mean: float, *, tolerance: float = 1e-12) -> list[float]:
    if mean < 0 or not math.isfinite(mean):
        raise ValueError('A Poisson mean must be finite and nonnegative.')
    probabilities = [math.exp(-mean)]
    cumulative = probabilities[0]
    goals = 0
    while cumulative < 1.0 - tolerance and goals < 100:
        goals += 1
        probabilities.append(probabilities[-1] * mean / goals)
        cumulative += probabilities[-1]
    total = sum(probabilities)
    if total <= 0:
        raise ValueError('Could not construct the Poisson distribution.')
    return [probability / total for probability in probabilities]


def _score_probabilities(home_mean: float, away_mean: float) -> tuple[dict[str, float], list[dict[str, Any]]]:
    home_distribution = _poisson_distribution(home_mean)
    away_distribution = _poisson_distribution(away_mean)
    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    scores: list[tuple[int, int, float]] = []
    for home_goals, home_probability in enumerate(home_distribution):
        for away_goals, away_probability in enumerate(away_distribution):
            probability = home_probability * away_probability
            scores.append((home_goals, away_goals, probability))
            if home_goals > away_goals:
                home_win += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away_win += probability

    rounded_home = round(home_win, 4)
    rounded_draw = round(draw, 4)
    rounded_away = round(1.0 - rounded_home - rounded_draw, 4)
    top_scores = sorted(scores, key=lambda value: value[2], reverse=True)[:5]
    return (
        {
            'home_win': rounded_home,
            'draw': rounded_draw,
            'away_win': rounded_away,
        },
        [
            {
                'score': f'{home_goals}-{away_goals}',
                'probability': round(probability, 5),
            }
            for home_goals, away_goals, probability in top_scores
        ],
    )


def goal_line_probabilities(total_mean: float) -> list[dict[str, float]]:
    """Return P(total goals > line) for the standard half-goal lines."""

    distribution = _poisson_distribution(total_mean)
    cumulative = 0.0
    result: list[dict[str, float]] = []
    for goals in range(5):
        cumulative += distribution[goals] if goals < len(distribution) else 0.0
        result.append({
            'line': goals + 0.5,
            'probability': round(max(0.0, min(1.0, 1.0 - cumulative)), 4),
        })
    return result


def poisson_markets_from_expected_goals(
    home_mean: float,
    away_mean: float,
) -> dict[str, Any]:
    """Build the shared match-market contract from two evidenced goal means."""

    means = (float(home_mean), float(away_mean))
    if any(value < 0 or not math.isfinite(value) for value in means):
        raise ValueError('Expected-goal means must be finite and nonnegative.')
    probabilities, _likely_scores = _score_probabilities(*means)
    goal_lines = goal_line_probabilities(sum(means))
    over_2_5 = next(
        market['probability'] for market in goal_lines if market['line'] == 2.5
    )
    btts = (1.0 - math.exp(-means[0])) * (1.0 - math.exp(-means[1]))
    return {
        'probabilities': {
            **probabilities,
            'over_2_5': round(over_2_5, 4),
            'btts': round(btts, 4),
        },
        'goal_lines': goal_lines,
    }


def predict_empirical_bayes_poisson(
    *,
    league_id: int,
    home_team_id: int,
    away_team_id: int,
    target_kickoff: datetime | str,
    historical_rows: Iterable[Mapping[str, Any]],
    prior_strength: float = BASELINE_PRIOR_STRENGTH_MATCHES,
) -> dict[str, Any]:
    """Fit a per-request league baseline using only pre-kickoff finished fixtures.

    Team venue rates are Empirical-Bayes posterior means whose prior mean is
    measured from the same league history. Independent Poisson goal counts
    then provide 1X2, total-goal lines, and BTTS probabilities. Exact-score
    tips are intentionally not published by this baseline.
    """

    league = BASELINE_LEAGUES.get(int(league_id))
    if league is None:
        raise PredictionInputError(f'League {league_id} has no statistical baseline.')
    if prior_strength <= 0 or not math.isfinite(prior_strength):
        raise ValueError('prior_strength must be finite and positive.')
    cutoff = _utc_datetime(target_kickoff)
    matches = _historical_matches(
        historical_rows,
        league_id=league.league_id,
        cutoff=cutoff,
    )
    if len(matches) < MIN_BASELINE_TRAINING_MATCHES:
        raise PredictionInputError(
            f'At least {MIN_BASELINE_TRAINING_MATCHES} finished pre-kickoff fixtures are required; '
            f'found {len(matches)}.'
        )

    league_home_rate = sum(match.home_goals for match in matches) / len(matches)
    league_away_rate = sum(match.away_goals for match in matches) / len(matches)

    home_venue_matches = [match for match in matches if match.home_team_id == home_team_id]
    away_venue_matches = [match for match in matches if match.away_team_id == away_team_id]

    home_attack = _posterior_rate(
        goals=sum(match.home_goals for match in home_venue_matches),
        matches=len(home_venue_matches),
        league_rate=league_home_rate,
        prior_strength=prior_strength,
    )
    home_defence = _posterior_rate(
        goals=sum(match.away_goals for match in home_venue_matches),
        matches=len(home_venue_matches),
        league_rate=league_away_rate,
        prior_strength=prior_strength,
    )
    away_attack = _posterior_rate(
        goals=sum(match.away_goals for match in away_venue_matches),
        matches=len(away_venue_matches),
        league_rate=league_away_rate,
        prior_strength=prior_strength,
    )
    away_defence = _posterior_rate(
        goals=sum(match.home_goals for match in away_venue_matches),
        matches=len(away_venue_matches),
        league_rate=league_home_rate,
        prior_strength=prior_strength,
    )

    expected_home_goals = (home_attack + away_defence) / 2.0
    expected_away_goals = (away_attack + home_defence) / 2.0
    markets = poisson_markets_from_expected_goals(
        expected_home_goals,
        expected_away_goals,
    )

    first_kickoff = matches[0].kickoff.isoformat()
    last_kickoff = matches[-1].kickoff.isoformat()
    seasons = sorted({match.season for match in matches if match.season is not None})
    sample_sizes = {
        'league_finished_matches': len(matches),
        'home_team_home_matches': len(home_venue_matches),
        'away_team_away_matches': len(away_venue_matches),
    }
    posterior_rates = {
        'home_team_home_scored': round(home_attack, 4),
        'home_team_home_conceded': round(home_defence, 4),
        'away_team_away_scored': round(away_attack, 4),
        'away_team_away_conceded': round(away_defence, 4),
    }
    return {
        'probabilities': {
            **markets['probabilities'],
        },
        # This baseline has no statistical source for corners or shots. Do not
        # manufacture them: only modeled goal expectations belong here.
        'expected': {
            'home_goals': round(expected_home_goals, 3),
            'away_goals': round(expected_away_goals, 3),
        },
        'goal_lines': markets['goal_lines'],
        'likely_scores': [],
        'features': {
            'cutoff_kickoff': cutoff.isoformat(),
            'league_home_goals_per_match': round(league_home_rate, 4),
            'league_away_goals_per_match': round(league_away_rate, 4),
            'prior_strength_matches': prior_strength,
            'sample_sizes': sample_sizes,
            'posterior_rates': posterior_rates,
        },
        'model': {
            'league': league.name,
            'league_id': league.league_id,
            'league_code': league.code,
            'model_type': 'statistical_baseline',
            'method': 'poisson_empirical_bayes',
            'version': '1.1',
            'data_source': 'supabase.fixtures',
            'cutoff_rule': 'status in FT/AET/PEN and kickoff < target kickoff',
            'cutoff_kickoff': cutoff.isoformat(),
            'trained_rows': len(matches),
            'training_seasons': seasons,
            'training_period': {
                'first_kickoff': first_kickoff,
                'last_kickoff': last_kickoff,
            },
            'prior_strength_matches': prior_strength,
            'sample_sizes': sample_sizes,
            'market_odds_used': False,
        },
    }
