import pytest

from app.services.probable_forecast_service import build_probable_forecast


def prediction(*, league_id: int = 71, low_quality: bool = False):
    return {
        'league_id': league_id,
        'home_team_name': 'Local',
        'away_team_name': 'Visitante',
        'expected': {
            'home_goals': 1.5,
            'away_goals': 1.1,
            'home_corners': 5.4,
            'away_corners': 4.6,
            'home_yellow_cards': 2.1,
            'away_yellow_cards': 2.8,
            'home_shots': 13.0,
            'away_shots': 10.0,
            'home_shots_on_target': 4.8,
            'away_shots_on_target': 3.7,
            'home_goalkeeper_saves': 2.7,
            'away_goalkeeper_saves': 3.3,
        },
        'model_metadata': {
            'confidence': 'low' if low_quality else 'medium',
            'goal_lines': [
                {'line': 0.5, 'probability': 0.925},
                {'line': 1.5, 'probability': 0.733},
                {'line': 2.5, 'probability': 0.482},
            ],
        },
    }


def test_builds_only_the_requested_forecast_categories():
    result = build_probable_forecast(prediction())

    assert [row['category'] for row in result] == [
        'goals',
        'corners',
        'half_goals',
        'cards',
        'shots',
        'saves',
        'shots_on_target',
    ]
    assert result[0]['prediction'] == 'Más de 1.5'
    assert result[1]['prediction'] in {
        'Más de 7.5',
        'Más de 4.5 · Local',
        'Más de 4.5 · Visitante',
    }
    assert result[2]['prediction'] == 'Más de 0.5 · 2.º tiempo'
    assert result[3]['prediction'] == 'Más de 0.5 · Visitante'
    assert result[3]['title'] == 'Tarjetas amarillas'
    assert result[4]['prediction'] == '18–28'
    assert result[5]['prediction'] == '3–9'
    assert result[6]['prediction'] == '6–12'


def test_goal_forecast_uses_only_the_requested_one_point_five_line():
    source = prediction()
    source['model_metadata']['goal_lines'] = [
        {'line': 0.5, 'probability': 0.98},
        {'line': 1.5, 'probability': 0.61},
        {'line': 2.5, 'probability': 0.75},
    ]

    goals = next(
        row
        for row in build_probable_forecast(source)
        if row['category'] == 'goals'
    )

    assert goals['prediction'] == 'Más de 1.5'
    assert goals['probability'] == pytest.approx(0.61)


def test_goal_forecast_is_omitted_when_one_point_five_is_below_floor():
    source = prediction()
    source['model_metadata']['goal_lines'] = [
        {'line': 0.5, 'probability': 0.98},
        {'line': 1.5, 'probability': 0.59},
        {'line': 2.5, 'probability': 0.75},
    ]

    assert all(
        row['category'] != 'goals'
        for row in build_probable_forecast(source)
    )


def test_friendlies_do_not_publish_cards_or_saves():
    categories = {
        row['category']
        for row in build_probable_forecast(prediction(league_id=667))
    }

    assert 'cards' not in categories
    assert 'saves' not in categories


def test_single_team_profile_never_manufactures_match_totals():
    source = prediction(low_quality=True)
    source['model_metadata'].update({
        'single_team_profile': True,
        'known_profile_sides': ['home'],
    })

    result = build_probable_forecast(source)
    categories = {row['category'] for row in result}

    assert 'shots' not in categories
    assert 'shots_on_target' not in categories
    assert 'saves' not in categories
    assert 'half_goals' not in categories
    assert 'goals' not in categories
    corners = next(row for row in result if row['category'] == 'corners')
    assert corners['prediction'] == 'Más de 4.5 · Local'
    assert corners['confidence'] == 'low'


def test_omits_an_over_market_below_the_reliability_floor():
    source = prediction()
    source['expected']['home_corners'] = 0.5
    source['expected']['away_corners'] = 0.5

    result = build_probable_forecast(source)

    assert all(row['category'] != 'corners' for row in result)


def test_cards_can_use_the_total_when_neither_team_line_is_reliable():
    source = prediction()
    source['expected']['home_yellow_cards'] = 0.5
    source['expected']['away_yellow_cards'] = 0.5

    cards = next(
        row
        for row in build_probable_forecast(source)
        if row['category'] == 'cards'
    )

    assert cards['prediction'] == 'Más de 0.5 · Total'
    assert cards['probability'] == pytest.approx(0.6321)


def test_reference_only_or_cross_league_metrics_are_not_published_as_totals():
    source = prediction()
    metrics = ('corners', 'yellow_cards', 'shots', 'shots_on_target', 'goalkeeper_saves')
    source['model_metadata']['market_statistics'] = {
        'teams': {
            'home': {
                'metrics': {
                    metric: {
                        'status': 'estimated',
                        'team_sample_used': True,
                        'cross_league_reference': False,
                    }
                    for metric in metrics
                },
            },
            'away': {
                'metrics': {
                    metric: {
                        'status': 'reference_only',
                        'team_sample_used': False,
                        'cross_league_reference': True,
                    }
                    for metric in metrics
                },
            },
        },
    }

    result = build_probable_forecast(source)
    categories = {row['category'] for row in result}

    assert {'shots', 'saves', 'shots_on_target'}.isdisjoint(categories)
    corners = next(row for row in result if row['category'] == 'corners')
    cards = next(row for row in result if row['category'] == 'cards')
    assert corners['prediction'] == 'Más de 4.5 · Local'
    assert cards['prediction'] == 'Más de 0.5 · Local'


@pytest.mark.parametrize('value', [None, float('nan'), -1])
def test_invalid_expected_values_are_not_published(value):
    source = prediction()
    source['expected']['home_shots'] = value

    result = build_probable_forecast(source)

    assert all(row['category'] != 'shots' for row in result)
