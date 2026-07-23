from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import joblib
import numpy as np
import pandas as pd
from app.core.config import get_settings
from app.services.baseline_model_service import goal_line_probabilities


@lru_cache
def load_bundle(league_code: str) -> dict[str, Any]:
    path = get_settings().model_root / league_code / 'model_bundle.joblib'
    if not path.exists():
        raise FileNotFoundError(f'No existe el modelo para {league_code}: {path}')
    return joblib.load(path)


def _poisson_probability(k: int, mean: float) -> float:
    return math.exp(-mean) * mean ** k / math.factorial(k)


def _poisson_over25(mean: float) -> float:
    return 1.0 - sum(_poisson_probability(k, mean) for k in range(3))


def _finite_mean(values: list[float | None], fallback: float) -> float:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(clean) / len(clean) if clean else fallback


def _blend(model_value: float, structural_value: float, model_weight: float = 0.35) -> float:
    return model_weight * float(model_value) + (1.0 - model_weight) * float(structural_value)


def _count(value: float) -> float:
    return round(max(0.0, float(value)), 2)


def _elo_probabilities(home_elo: float, away_elo: float) -> np.ndarray:
    # Ventaja local conservadora; el empate baja cuando la diferencia Elo es grande.
    diff = (home_elo + 60.0) - away_elo
    home_without_draw = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    draw = min(0.28, max(0.14, 0.28 - abs(diff) / 1500.0))
    remaining = 1.0 - draw
    return np.array([
        remaining * home_without_draw,
        draw,
        remaining * (1.0 - home_without_draw),
    ], dtype=float)


def _normalized(values: np.ndarray) -> np.ndarray:
    values = np.clip(values.astype(float), 1e-8, None)
    return values / values.sum()


def predict(league_code: str, features: dict[str, float | None]) -> dict[str, Any]:
    bundle = load_bundle(league_code)
    ordered = {name: features.get(name) for name in bundle['features']}
    X = pd.DataFrame([ordered], columns=bundle['features'])
    models = bundle['models']

    model_result = models['result_1x2'].predict_proba(X)[0]
    classes = models['result_1x2'].named_steps['model'].classes_
    mapped = {int(label): float(probability) for label, probability in zip(classes, model_result)}
    model_result = np.array([mapped.get(0, 0.0), mapped.get(1, 0.0), mapped.get(2, 0.0)])

    home_elo = float(features.get('HomeElo') or 1500.0)
    away_elo = float(features.get('AwayElo') or 1500.0)
    elo_result = _elo_probabilities(home_elo, away_elo)

    odds_values = [
        features.get('AvgOpenProbH'),
        features.get('AvgOpenProbD'),
        features.get('AvgOpenProbA'),
    ]
    has_market = all(value is not None and float(value) > 0 for value in odds_values)
    if has_market:
        market = _normalized(np.array([float(value) for value in odds_values]))
        result_proba = _normalized(0.55 * market + 0.25 * elo_result + 0.20 * model_result)
    else:
        # Sin cuotas actuales, Elo domina para evitar que un baseline pequeño se vuelva extremo.
        result_proba = _normalized(0.75 * elo_result + 0.25 * model_result)

    raw_home_goals = float(models['home_goals'].predict(X)[0])
    raw_away_goals = float(models['away_goals'].predict(X)[0])
    structural_home_goals = _finite_mean([
        features.get('HomeSeasonGFpg'), features.get('AwaySeasonGApg'),
        features.get('HomeVenueGF5'), features.get('AwayVenueGA5'),
    ], 1.25)
    structural_away_goals = _finite_mean([
        features.get('AwaySeasonGFpg'), features.get('HomeSeasonGApg'),
        features.get('AwayVenueGF5'), features.get('HomeVenueGA5'),
    ], 1.10)
    home_goals = _count(_blend(raw_home_goals, structural_home_goals, 0.30))
    away_goals = _count(_blend(raw_away_goals, structural_away_goals, 0.30))

    raw_home_corners = float(models['home_corners'].predict(X)[0])
    raw_away_corners = float(models['away_corners'].predict(X)[0])
    home_corners = _count(_blend(raw_home_corners, _finite_mean([
        features.get('HomeSeasonCornersPg'), features.get('HomeCorners5')], 4.8)))
    away_corners = _count(_blend(raw_away_corners, _finite_mean([
        features.get('AwaySeasonCornersPg'), features.get('AwayCorners5')], 4.5)))

    raw_home_shots = float(models['home_shots'].predict(X)[0])
    raw_away_shots = float(models['away_shots'].predict(X)[0])
    home_shots = _count(_blend(raw_home_shots, _finite_mean([
        features.get('HomeSeasonShotsPg'), features.get('HomeShots5')], 12.0)))
    away_shots = _count(_blend(raw_away_shots, _finite_mean([
        features.get('AwaySeasonShotsPg'), features.get('AwayShots5')], 11.0)))

    raw_home_sot = float(models['home_sot'].predict(X)[0])
    raw_away_sot = float(models['away_sot'].predict(X)[0])
    home_sot = _count(_blend(raw_home_sot, _finite_mean([
        features.get('HomeSeasonSOTpg'), features.get('HomeSOT5'),
        features.get('AwaySeasonSOTAgainstPg')], 4.0)))
    away_sot = _count(_blend(raw_away_sot, _finite_mean([
        features.get('AwaySeasonSOTpg'), features.get('AwaySOT5'),
        features.get('HomeSeasonSOTAgainstPg')], 3.8)))

    # Las atajadas se anclan a los remates al arco rivales menos los goles esperados.
    raw_home_saves = float(models['home_saves'].predict(X)[0])
    raw_away_saves = float(models['away_saves'].predict(X)[0])
    home_saves = _count(_blend(raw_home_saves, max(0.0, away_sot - away_goals), 0.30))
    away_saves = _count(_blend(raw_away_saves, max(0.0, home_sot - home_goals), 0.30))

    model_over = float(models['over25'].predict_proba(X)[0][1])
    model_btts = float(models['btts'].predict_proba(X)[0][1])
    poisson_over = _poisson_over25(home_goals + away_goals)
    poisson_btts = (1.0 - math.exp(-home_goals)) * (1.0 - math.exp(-away_goals))
    over25 = 0.45 * model_over + 0.55 * poisson_over
    btts = 0.45 * model_btts + 0.55 * poisson_btts
    goal_lines = goal_line_probabilities(home_goals + away_goals)

    output = {
        'probabilities': {
            'home_win': round(float(result_proba[0]), 4),
            'draw': round(float(result_proba[1]), 4),
            'away_win': round(float(result_proba[2]), 4),
            'over_2_5': round(float(over25), 4),
            'btts': round(float(btts), 4),
        },
        'expected': {
            'home_goals': home_goals,
            'away_goals': away_goals,
            'home_corners': home_corners,
            'away_corners': away_corners,
            'home_shots': home_shots,
            'away_shots': away_shots,
            'home_shots_on_target': home_sot,
            'away_shots_on_target': away_sot,
            'home_goalkeeper_saves': home_saves,
            'away_goalkeeper_saves': away_saves,
        },
        'goal_lines': goal_lines,
        # The app deliberately avoids exact-score betting tips. Keep the
        # legacy database field empty until it is removed by a future schema
        # migration.
        'likely_scores': [],
        'model': {
            'league': bundle['league_name'],
            'trained_rows': bundle['trained_rows'],
            'seasons': bundle['seasons'],
            'market_odds_used': has_market,
            'method': 'ensemble: league model + structural form + Elo + market when available',
        },
    }
    return output
