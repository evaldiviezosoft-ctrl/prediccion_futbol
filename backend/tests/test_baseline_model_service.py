from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import PredictionInputError
from app.services.baseline_model_service import (
    BASELINE_LEAGUE_IDS,
    predict_empirical_bayes_poisson,
)


CUTOFF = datetime(2026, 7, 23, 0, 30, tzinfo=timezone.utc)


def history_row(
    fixture_id: int,
    *,
    league_id: int = 71,
    kickoff: datetime,
    home_team_id: int,
    away_team_id: int,
    home_goals: int,
    away_goals: int,
    status: str = 'FT',
) -> dict:
    return {
        'id': fixture_id,
        'league_id': league_id,
        'season': kickoff.year,
        'kickoff': kickoff.isoformat(),
        'fixture_date_utc': kickoff.isoformat(),
        'status_short': status,
        'home_team_id': home_team_id,
        'away_team_id': away_team_id,
        'home_goals': home_goals,
        'away_goals': away_goals,
    }


def training_history(*, league_id: int = 71, count: int = 30) -> list[dict]:
    rows = []
    for index in range(count):
        if index < 8:
            home_team_id, away_team_id = 10, 100 + index
        elif index < 15:
            home_team_id, away_team_id = 200 + index, 20
        else:
            home_team_id, away_team_id = 300 + index, 400 + index
        rows.append(
            history_row(
                index + 1,
                league_id=league_id,
                kickoff=CUTOFF - timedelta(days=count - index),
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                home_goals=index % 4,
                away_goals=index % 3,
            )
        )
    return rows


def predict(rows: list[dict], *, league_id: int = 71) -> dict:
    return predict_empirical_bayes_poisson(
        league_id=league_id,
        home_team_id=10,
        away_team_id=20,
        target_kickoff=CUTOFF,
        historical_rows=rows,
    )


def test_baseline_has_normalized_probabilities_and_only_models_goals():
    result = predict(training_history())

    one_x_two = result['probabilities']
    assert one_x_two['home_win'] + one_x_two['draw'] + one_x_two['away_win'] == pytest.approx(1.0)
    assert all(0 <= value <= 1 for value in one_x_two.values())
    assert set(result['expected']) == {'home_goals', 'away_goals'}
    assert result['likely_scores'] == []
    assert [market['line'] for market in result['goal_lines']] == [0.5, 1.5, 2.5, 3.5, 4.5]
    assert [market['probability'] for market in result['goal_lines']] == sorted(
        (market['probability'] for market in result['goal_lines']),
        reverse=True,
    )
    assert result['probabilities']['over_2_5'] == next(
        market['probability']
        for market in result['goal_lines']
        if market['line'] == 2.5
    )
    assert result['model']['method'] == 'poisson_empirical_bayes'
    assert result['model']['trained_rows'] == 30
    assert result['model']['sample_sizes'] == {
        'league_finished_matches': 30,
        'home_team_home_matches': 8,
        'away_team_away_matches': 7,
    }


def test_future_nonfinal_and_other_league_rows_cannot_leak_into_result():
    rows = training_history()
    baseline = predict(rows)
    contaminated = rows + [
        history_row(
            499,
            kickoff=CUTOFF,
            home_team_id=10,
            away_team_id=20,
            home_goals=99,
            away_goals=0,
        ),
        history_row(
            500,
            kickoff=CUTOFF + timedelta(seconds=1),
            home_team_id=10,
            away_team_id=20,
            home_goals=99,
            away_goals=0,
        ),
        history_row(
            501,
            kickoff=CUTOFF - timedelta(hours=1),
            home_team_id=10,
            away_team_id=20,
            home_goals=99,
            away_goals=0,
            status='NS',
        ),
        history_row(
            502,
            league_id=128,
            kickoff=CUTOFF - timedelta(hours=1),
            home_team_id=10,
            away_team_id=20,
            home_goals=99,
            away_goals=0,
        ),
    ]

    assert predict(contaminated) == baseline
    assert baseline['model']['training_period']['last_kickoff'] < baseline['model']['cutoff_kickoff']


def test_unseen_teams_shrink_exactly_to_observed_league_goal_rates():
    rows = training_history()
    result = predict_empirical_bayes_poisson(
        league_id=71,
        home_team_id=9_001,
        away_team_id=9_002,
        target_kickoff=CUTOFF,
        historical_rows=rows,
    )
    features = result['features']

    assert result['expected']['home_goals'] == pytest.approx(
        features['league_home_goals_per_match'], abs=0.001
    )
    assert result['expected']['away_goals'] == pytest.approx(
        features['league_away_goals_per_match'], abs=0.001
    )
    assert result['model']['sample_sizes']['home_team_home_matches'] == 0
    assert result['model']['sample_sizes']['away_team_away_matches'] == 0


@pytest.mark.parametrize('league_id', sorted(BASELINE_LEAGUE_IDS))
def test_every_configured_south_american_competition_uses_the_baseline(league_id):
    result = predict(training_history(league_id=league_id), league_id=league_id)

    assert result['model']['league_id'] == league_id
    assert result['model']['training_seasons'] == [2026]


def test_baseline_rejects_an_honestly_insufficient_sample():
    with pytest.raises(PredictionInputError, match='found 19'):
        predict(training_history(count=19))
