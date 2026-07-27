import pytest

from app.services.probable_forecast_service import (
    build_market_forecast,
    build_probable_forecast,
    empty_market_forecast,
    validated_market_forecast,
)


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


def test_market_forecast_builds_all_supported_match_totals():
    result = build_market_forecast(prediction())

    assert result['version'] == 'deterministic_lines_v1'
    assert result['method'] == 'poisson_mean_approximation'
    markets = {
        market['category']: market
        for market in result['markets']
    }
    assert list(markets) == [
        'goals',
        'corners',
        'yellow_cards',
        'shots',
        'shots_on_target',
    ]
    assert all(
        market['scope'] == 'match_total'
        and market['confidence'] == 'medium'
        and len(market['lines']) == 5
        for market in markets.values()
    )
    assert markets['goals']['expected_total'] == pytest.approx(2.6)
    assert markets['corners']['expected_total'] == pytest.approx(10.0)
    assert markets['yellow_cards']['expected_total'] == pytest.approx(4.9)
    assert markets['shots']['expected_total'] == pytest.approx(23.0)
    assert markets['shots_on_target']['expected_total'] == pytest.approx(8.5)


def test_goal_lines_prefer_stored_probabilities_and_fill_missing_lines():
    source = prediction()
    source['model_metadata']['goal_lines'] = [
        {'line': 0.5, 'probability': 0.8},
        {'line': 1.5, 'probability': 0.2},
        {'line': 2.5, 'probability': float('nan')},
        {'line': 9.5, 'probability': 0.9},
    ]

    goals = next(
        market
        for market in build_market_forecast(source)['markets']
        if market['category'] == 'goals'
    )

    assert [row['line'] for row in goals['lines']] == [
        0.5,
        1.5,
        2.5,
        3.5,
        4.5,
    ]
    assert goals['lines'][0] == {
        'line': 0.5,
        'over_probability': 0.8,
        'under_probability': 0.2,
        'selection': 'over',
        'selection_probability': 0.8,
    }
    assert goals['lines'][1] == {
        'line': 1.5,
        'over_probability': 0.2,
        'under_probability': 0.8,
        'selection': 'under',
        'selection_probability': 0.8,
    }
    assert goals['lines'][2]['over_probability'] == pytest.approx(0.4816)


def test_market_lines_are_complementary_and_can_decline_a_selection():
    corners = next(
        market
        for market in build_market_forecast(prediction())['markets']
        if market['category'] == 'corners'
    )

    undecided = next(
        row for row in corners['lines'] if row['selection'] == 'none'
    )
    assert undecided['selection_probability'] is None
    for row in corners['lines']:
        assert row['over_probability'] + row['under_probability'] == pytest.approx(1)
        if row['selection'] == 'over':
            assert row['selection_probability'] == row['over_probability']
            assert row['selection_probability'] >= 0.65
        elif row['selection'] == 'under':
            assert row['selection_probability'] == row['under_probability']
            assert row['selection_probability'] >= 0.65


def test_shot_lines_use_a_two_count_step_and_never_go_negative():
    source = prediction()
    source['expected']['home_shots'] = 0
    source['expected']['away_shots'] = 0

    shots = next(
        market
        for market in build_market_forecast(source)['markets']
        if market['category'] == 'shots'
    )
    lines = [row['line'] for row in shots['lines']]

    assert lines == [0.5, 2.5, 4.5, 6.5, 8.5]
    assert len(lines) == len(set(lines))
    assert all(line >= 0.5 for line in lines)


def test_market_forecast_never_manufactures_partial_profile_totals():
    source = prediction(low_quality=True)
    source['model_metadata'].update({
        'single_team_profile': True,
        'known_profile_sides': ['home'],
    })

    result = build_market_forecast(source)

    assert result['markets'] == []


def test_market_forecast_omits_cards_for_friendlies():
    categories = {
        market['category']
        for market in build_market_forecast(
            prediction(league_id=667)
        )['markets']
    }

    assert 'yellow_cards' not in categories
    assert {'goals', 'corners', 'shots', 'shots_on_target'} <= categories


def test_market_forecast_rejects_reference_only_totals():
    source = prediction()
    metrics = ('corners', 'yellow_cards', 'shots', 'shots_on_target')
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

    categories = {
        market['category']
        for market in build_market_forecast(source)['markets']
    }

    assert categories == {'goals'}


@pytest.mark.parametrize('value', [None, float('nan'), -1])
def test_market_forecast_omits_invalid_or_incomplete_pairs(value):
    source = prediction()
    source['expected']['home_shots_on_target'] = value

    categories = {
        market['category']
        for market in build_market_forecast(source)['markets']
    }

    assert 'shots_on_target' not in categories


def test_market_forecast_marks_low_quality_sources():
    source = prediction(low_quality=True)

    result = build_market_forecast(source)

    assert result['markets']
    assert all(
        market['confidence'] == 'low'
        for market in result['markets']
    )


def test_public_market_forecast_accepts_a_complete_stored_snapshot():
    forecast = build_market_forecast(prediction())

    assert validated_market_forecast(forecast) == forecast


def test_public_market_forecast_does_not_rebuild_legacy_or_invalid_rows():
    assert validated_market_forecast(None) == empty_market_forecast()
    assert validated_market_forecast({
        'version': 'deterministic_lines_v1',
        'method': 'poisson_mean_approximation',
        'markets': [],
    }) == empty_market_forecast()

    malformed = build_market_forecast(prediction())
    malformed['markets'][0]['lines'][0]['over_probability'] = 2

    assert validated_market_forecast(malformed) == empty_market_forecast()
