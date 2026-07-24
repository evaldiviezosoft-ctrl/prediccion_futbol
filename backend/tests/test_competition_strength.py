from __future__ import annotations

import math

import pytest

from app.services.competition_strength import (
    COMPETITION_STRENGTH_BY_ID,
    FALLBACK_STRENGTH_FACTOR,
    MAX_STRENGTH_FACTOR,
    MIN_STRENGTH_FACTOR,
    competition_strength_factor,
    resolve_competition_strength,
)


@pytest.mark.parametrize(
    ('league_id', 'canonical_code'),
    [
        (39, 'premier_league'),
        (61, 'ligue_1'),
        (78, 'bundesliga'),
        (135, 'serie_a_italy'),
        (140, 'laliga'),
        (3, 'uefa_europa_league'),
        (11, 'copa_sudamericana'),
        (13, 'copa_libertadores'),
        (71, 'brazil_serie_a'),
        (128, 'argentina_liga_profesional'),
        (281, 'peru_liga_1'),
        (667, 'friendlies_clubs'),
        (103, 'eliteserien'),
        (40, 'championship'),
        (62, 'ligue_2'),
        (79, '2_bundesliga'),
        (95, 'segunda_liga'),
        (129, 'primera_nacional'),
        (138, 'serie_c_italy'),
        (141, 'segunda_division'),
        (203, 'super_lig'),
        (206, 'turkish_cup'),
        (877, 'segunda_rfef'),
        (891, 'coppa_italia_serie_c'),
        (976, 'serie_c_playoffs'),
    ],
)
def test_configured_competitions_resolve_by_id(league_id, canonical_code):
    result = resolve_competition_strength(league_id=league_id)

    assert result.canonical_code == canonical_code
    assert result.league_id == league_id
    assert result.matched_by == 'league_id'
    assert result.source == 'maintained_conservative_catalog'
    assert result.is_fallback is False
    assert result.explanation


@pytest.mark.parametrize(
    ('league_code', 'canonical_code'),
    [
        ('E0', 'premier_league'),
        ('sp1', 'laliga'),
        ('Serie-A-Italy', 'serie_a_italy'),
        ('D1', 'bundesliga'),
        ('F1', 'ligue_1'),
        ('api_103', 'eliteserien'),
        ('club friendlies', 'friendlies_clubs'),
    ],
)
def test_codes_and_aliases_are_case_and_separator_insensitive(
    league_code,
    canonical_code,
):
    result = resolve_competition_strength(league_code=league_code)

    assert result.canonical_code == canonical_code
    assert result.league_code == league_code
    assert result.matched_by == 'league_code'
    assert result.is_fallback is False


def test_top_five_and_eliteserien_have_conservative_relative_order():
    premier_league = competition_strength_factor(league_id=39)
    other_top_five = [
        competition_strength_factor(league_id=league_id)
        for league_id in (61, 78, 135, 140)
    ]
    eliteserien = competition_strength_factor(league_id=103)

    assert all(premier_league >= factor > eliteserien for factor in other_top_five)
    assert premier_league / eliteserien < 1.20


@pytest.mark.parametrize(
    ('league_id', 'canonical_code'),
    [
        (40, 'championship'),
        (62, 'ligue_2'),
        (79, '2_bundesliga'),
        (95, 'segunda_liga'),
        (129, 'primera_nacional'),
        (138, 'serie_c_italy'),
        (141, 'segunda_division'),
        (203, 'super_lig'),
        (206, 'turkish_cup'),
        (877, 'segunda_rfef'),
        (891, 'coppa_italia_serie_c'),
        (976, 'serie_c_playoffs'),
    ],
)
def test_targeted_backfill_competitions_support_generated_api_alias(
    league_id,
    canonical_code,
):
    result = resolve_competition_strength(league_code=f'api_{league_id}')

    assert result.canonical_code == canonical_code
    assert result.league_id == league_id
    assert result.matched_by == 'league_code'
    assert result.is_fallback is False


def test_every_catalog_factor_is_finite_and_inside_supported_range():
    factors = [entry.factor for entry in COMPETITION_STRENGTH_BY_ID.values()]

    assert factors
    assert all(math.isfinite(factor) for factor in factors)
    assert all(MIN_STRENGTH_FACTOR <= factor <= MAX_STRENGTH_FACTOR for factor in factors)


@pytest.mark.parametrize(
    'kwargs',
    [
        {},
        {'league_id': 999_999},
        {'league_code': 'unknown_league'},
        {'league_code': '   '},
    ],
)
def test_unknown_or_missing_competition_returns_explainable_neutral_fallback(kwargs):
    result = resolve_competition_strength(**kwargs)

    assert result.factor == FALLBACK_STRENGTH_FACTOR == 1.00
    assert result.tier == 'unknown'
    assert result.source == 'explicit_neutral_fallback'
    assert result.is_fallback is True
    assert result.matched_by is None
    assert 'neutral' in result.explanation
    assert 'lower confidence' in result.explanation


def test_known_code_can_resolve_when_positive_id_is_unknown():
    result = resolve_competition_strength(league_id=999_999, league_code='E0')

    assert result.canonical_code == 'premier_league'
    assert result.matched_by == 'league_code'
    assert result.is_fallback is False


def test_known_id_is_authoritative_when_code_disagrees():
    result = resolve_competition_strength(league_id=103, league_code='E0')

    assert result.canonical_code == 'eliteserien'
    assert result.matched_by == 'league_id'


def test_metadata_is_json_serialisable_primitives():
    metadata = resolve_competition_strength(league_id=103).to_metadata()

    assert metadata['factor'] == 0.91
    assert metadata['canonical_code'] == 'eliteserien'
    assert metadata['is_fallback'] is False


@pytest.mark.parametrize(
    'kwargs',
    [
        {'league_id': 0},
        {'league_id': -1},
        {'league_id': True},
        {'league_id': '103'},
        {'league_code': 103},
    ],
)
def test_invalid_identifiers_are_rejected(kwargs):
    with pytest.raises(ValueError):
        resolve_competition_strength(**kwargs)
