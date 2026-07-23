from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.fixture_normalizer import (
    FINAL_FIXTURE_STATUSES,
    normalize_fixture,
    normalize_percentage,
    normalize_team_statistics,
)
from app.services.supabase_repository import should_apply_fixture_update


def fixture_payload(
    status: str = 'FT',
    *,
    fixture_id: int = 1001,
    include_statistics: bool = True,
) -> dict:
    payload = {
        'fixture': {
            'id': fixture_id,
            'date': '2026-07-22T20:30:00-05:00',
            'timestamp': 1784770200,
            'timezone': 'America/Lima',
            'referee': 'A. Referee',
            'venue': {'id': 50, 'name': 'Estadio Nacional', 'city': 'Lima'},
            'status': {'long': 'Match Finished', 'short': status, 'elapsed': 90},
        },
        'league': {
            'id': 281,
            'name': 'Primera División',
            'country': 'Peru',
            'season': 2026,
            'round': 'Clausura - 3',
        },
        'teams': {
            'home': {'id': 1, 'name': 'Local', 'winner': True, 'logo': 'home.png'},
            'away': {'id': 2, 'name': 'Visita', 'winner': False, 'logo': 'away.png'},
        },
        'goals': {'home': 2, 'away': 1},
        'score': {
            'halftime': {'home': 1, 'away': 0},
            'fulltime': {'home': 2, 'away': 1},
            'extratime': {'home': None, 'away': None},
            'penalty': {'home': None, 'away': None},
        },
        'events': [],
        'lineups': [],
        'players': [],
    }
    if include_statistics:
        payload['statistics'] = [{
            'team': {'id': 1, 'name': 'Local'},
            'statistics': [
                {'type': 'Shots on Goal', 'value': 7},
                {'type': 'Shots off Goal', 'value': '4'},
                {'type': 'Total Shots', 'value': 15},
                {'type': 'Corner Kicks', 'value': '6'},
                {'type': 'Goalkeeper Saves', 'value': 3},
                {'type': 'Ball Possession', 'value': '54%'},
                {'type': 'expected_goals', 'value': '1.67'},
            ],
        }]
    return payload


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [('54%', 54.0), ('0%', 0.0), (100, 100.0), (None, None), ('bad', None), ('101%', None)],
)
def test_percentage_normalization(raw, expected):
    assert normalize_percentage(raw) == expected


def test_statistics_normalize_shots_corners_saves_and_percentages():
    values = fixture_payload()['statistics'][0]['statistics']
    result = normalize_team_statistics(values)
    assert result['shots_on_goal'] == 7
    assert result['shots_off_goal'] == 4
    assert result['total_shots'] == 15
    assert result['corners'] == 6
    assert result['goalkeeper_saves'] == 3
    assert result['possession_percentage'] == 54.0
    assert result['expected_goals'] == 1.67


def test_fixture_without_statistics_keeps_null_semantics():
    normalized = normalize_fixture(
        fixture_payload(include_statistics=False), competition_id=9
    )
    assert normalized.team_statistics == []
    assert normalized.components_present['statistics'] is False


@pytest.mark.parametrize('status', ['FT', 'AET', 'PEN', 'CANC', 'ABD'])
def test_terminal_fixture_statuses(status):
    normalized = normalize_fixture(fixture_payload(status), competition_id=9)
    assert normalized.fixture['status_short'] == status
    assert status in FINAL_FIXTURE_STATUSES


@pytest.mark.parametrize('status', ['NS', 'TBD', 'PST'])
def test_scheduled_and_postponed_fixture_statuses(status):
    payload = fixture_payload(status)
    payload['fixture']['status']['elapsed'] = None
    payload['goals'] = {'home': None, 'away': None}
    normalized = normalize_fixture(payload, competition_id=9)
    assert normalized.fixture['status_short'] == status
    assert normalized.fixture['home_goals'] is None


def test_penalty_winner_and_score_are_normalized():
    payload = fixture_payload('PEN')
    payload['teams']['home']['winner'] = None
    payload['teams']['away']['winner'] = None
    payload['goals'] = {'home': 1, 'away': 1}
    payload['score']['fulltime'] = {'home': 1, 'away': 1}
    payload['score']['penalty'] = {'home': 5, 'away': 4}
    normalized = normalize_fixture(payload, competition_id=9)
    assert normalized.fixture['penalties_home'] == 5
    assert normalized.fixture['penalties_away'] == 4
    assert normalized.fixture['winner_team_id'] == 1


def test_utc_and_lima_dates_are_both_preserved():
    normalized = normalize_fixture(fixture_payload(), competition_id=9)
    assert normalized.fixture['fixture_date_utc'].startswith('2026-07-23T01:30:00+00:00')
    assert normalized.fixture['fixture_date_lima'] == '2026-07-22T20:30:00'


def test_cup_stage_group_and_leg_are_kept_separate():
    payload = fixture_payload()
    payload['league']['round'] = 'Quarter-finals - 2nd Leg'
    knockout = normalize_fixture(payload, competition_id=10)
    assert knockout.fixture['stage'] == 'quarterfinal'
    assert knockout.fixture['leg'] == 'second'
    payload['league']['round'] = 'Group A - 4'
    group = normalize_fixture(payload, competition_id=10)
    assert group.fixture['stage'] == 'group_stage'
    assert group.fixture['group_name'] == 'A'


def test_incomplete_upcoming_payload_cannot_replace_terminal_fixture():
    for terminal in ('FT', 'AET', 'PEN', 'CANC', 'ABD'):
        assert should_apply_fixture_update(terminal, 'NS') is False
    assert should_apply_fixture_update('PST', 'NS') is True
    assert should_apply_fixture_update('NS', 'FT') is True


def test_normalizer_does_not_mutate_provider_payload():
    payload = fixture_payload()
    original = deepcopy(payload)
    normalize_fixture(payload, competition_id=9)
    assert payload == original


def test_invalid_provider_zero_ids_and_negative_event_minutes_become_null():
    payload = fixture_payload()
    payload['events'] = [{
        'time': {'elapsed': -5, 'extra': -1},
        'team': {'id': 1},
        'player': {'id': 0, 'name': 'Unknown provider player'},
        'assist': {'id': 0},
        'type': 'Card',
        'detail': 'Red Card',
    }]
    payload['players'] = [{
        'team': {'id': 1},
        'players': [{'player': {'id': 0, 'name': 'Unknown'}, 'statistics': []}],
    }]

    normalized = normalize_fixture(payload, competition_id=9)

    assert normalized.events[0]['minute'] is None
    assert normalized.events[0]['extra_minute'] is None
    assert normalized.events[0]['api_player_id'] is None
    assert normalized.events[0]['api_assist_id'] is None
    assert normalized.players == []
