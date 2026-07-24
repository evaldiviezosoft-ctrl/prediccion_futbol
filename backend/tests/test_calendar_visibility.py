from types import SimpleNamespace

from app.services.calendar_visibility import (
    filter_visible_calendar_fixtures,
    local_profile_team_names,
    local_team_country,
)


class HistoryQuery:
    def __init__(self, database):
        self.database = database
        self.in_filters = []
        self.not_equal_filters = []
        self.less_than_filters = []
        self.candidate_ids = set()
        self.start = 0
        self.end = None

    def select(self, columns):
        assert columns == (
            'id,home_team_id,away_team_id,home_goals,away_goals'
        )
        return self

    def in_(self, column, values):
        self.in_filters.append((column, set(values)))
        return self

    def neq(self, column, value):
        self.not_equal_filters.append((column, value))
        return self

    def lt(self, column, value):
        self.less_than_filters.append((column, value))
        return self

    def or_(self, filters):
        encoded_values = filters.split('(', 1)[1].split(')', 1)[0]
        self.candidate_ids = {
            int(value) for value in encoded_values.split(',') if value
        }
        return self

    def order(self, column):
        self.database.orders.append(column)
        return self

    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def execute(self):
        rows = list(self.database.history)
        for column, values in self.in_filters:
            rows = [row for row in rows if row.get(column) in values]
        for column, value in self.not_equal_filters:
            rows = [row for row in rows if row.get(column) != value]
        for column, value in self.less_than_filters:
            rows = [row for row in rows if str(row.get(column) or '') < value]
        rows = [
            row for row in rows
            if (
                row.get('home_team_id') in self.candidate_ids
                or row.get('away_team_id') in self.candidate_ids
            )
        ]
        rows.sort(key=lambda row: int(row['id']))
        self.database.query_count += 1
        end = self.end + 1 if self.end is not None else None
        return SimpleNamespace(data=rows[self.start:end])


class HistoryDatabase:
    def __init__(self, history=()):
        self.history = list(history)
        self.query_count = 0
        self.orders = []

    def table(self, name):
        assert name == 'fixtures'
        return HistoryQuery(self)


def fixture(
    fixture_id,
    league_id,
    home_team_id,
    away_team_id,
    home_team_name,
    away_team_name,
    kickoff='2099-08-22T18:00:00+00:00',
):
    return {
        'id': fixture_id,
        'league_id': league_id,
        'home_team_id': home_team_id,
        'away_team_id': away_team_id,
        'home_team_name': home_team_name,
        'away_team_name': away_team_name,
        'kickoff': kickoff,
    }


def test_calendar_only_keeps_exact_profiles_aliases_and_safe_designators():
    database = HistoryDatabase()
    rows = [
        fixture(1, 667, 1, 2, 'Barcelona', 'Unknown XI'),
        fixture(2, 667, 3, 4, 'Rosenborg', 'Manchester United'),
        fixture(3, 3, 5, 6, 'VfL Wolfsburg', 'Unknown XI'),
        fixture(4, 3, 7, 8, 'SC Freiburg', 'Unknown XI'),
        fixture(5, 667, 9, 10, 'FC St. Pauli', 'Unknown XI'),
    ]

    result = filter_visible_calendar_fixtures(database, rows)

    assert [row['id'] for row in result] == [1, 2, 3, 4, 5]
    assert database.query_count == 0
    assert 'manchester united' in local_profile_team_names()
    assert local_team_country('Manchester United') == 'England'
    assert local_team_country('FC St. Pauli') == 'Germany'
    assert local_team_country('Unknown XI') is None


def test_calendar_only_uses_completed_past_history_from_dynamic_leagues():
    database = HistoryDatabase([
        {
            'id': 10 + index,
            'league_id': 40,
            'status_short': 'AET',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999 + index,
            'home_goals': 2,
            'away_goals': 1,
        }
        for index in range(5)
    ])
    rows = [
        fixture(1, 667, 100, 200, 'Known by history', 'Unknown XI'),
        fixture(2, 3, 300, 400, 'No local data', 'Unknown XI'),
        fixture(3, 39, 500, 600, 'Any team', 'Any opponent'),
    ]

    result = filter_visible_calendar_fixtures(database, rows)

    assert [row['id'] for row in result] == [1, 3]
    assert result[0]['prediction_fallback_available'] is True
    assert result[1]['prediction_fallback_available'] is False
    assert database.query_count == 1
    assert database.orders == ['id']


def test_calendar_history_rejects_future_cancelled_scoreless_and_calendar_matches():
    database = HistoryDatabase([
        {
            'id': 10,
            'league_id': 39,
            'status_short': 'FT',
            'kickoff': '2100-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999,
            'home_goals': 2,
            'away_goals': 1,
        },
        {
            'id': 11,
            'league_id': 39,
            'status_short': 'CANC',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999,
            'home_goals': None,
            'away_goals': None,
        },
        {
            'id': 12,
            'league_id': 667,
            'status_short': 'FT',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999,
            'home_goals': 2,
            'away_goals': 1,
        },
        {
            'id': 13,
            'league_id': 39,
            'status_short': 'FT',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999,
            'home_goals': None,
            'away_goals': None,
        },
    ])
    rows = [fixture(1, 667, 100, 200, 'No local data', 'Unknown XI')]

    assert filter_visible_calendar_fixtures(database, rows) == []


def test_calendar_history_accepts_penalty_result_from_dynamic_league():
    database = HistoryDatabase([
        {
            'id': 20 + index,
            'league_id': 203,
            'status_short': 'PEN',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999 + index,
            'home_goals': 1,
            'away_goals': 1,
        }
        for index in range(5)
    ])
    rows = [fixture(1, 667, 100, 200, 'Dynamic club', 'Unknown XI')]

    result = filter_visible_calendar_fixtures(database, rows)

    assert [row['id'] for row in result] == [1]


def test_calendar_history_requires_five_finished_matches():
    database = HistoryDatabase([
        {
            'id': 30 + index,
            'league_id': 40,
            'status_short': 'FT',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 900 + index,
            'home_goals': 1,
            'away_goals': 0,
        }
        for index in range(4)
    ])
    rows = [fixture(1, 667, 100, 200, 'Thin sample', 'Unknown XI')]

    assert filter_visible_calendar_fixtures(database, rows) == []


def test_history_query_paginates_until_an_unresolved_club_is_found():
    history = [
        {
            'id': fixture_id,
            'league_id': 39,
            'status_short': 'FT',
            'kickoff': '2098-08-22T18:00:00+00:00',
            'home_team_id': 100,
            'away_team_id': 999,
            'home_goals': 2,
            'away_goals': 1,
        }
        for fixture_id in range(1, 1001)
    ]
    history.append({
        'id': 1001,
        'league_id': 39,
        'status_short': 'FT',
        'kickoff': '2098-08-22T18:00:00+00:00',
        'home_team_id': 200,
        'away_team_id': 999,
        'home_goals': 1,
        'away_goals': 0,
    })
    database = HistoryDatabase(history)
    rows = [fixture(1, 3, 100, 200, 'No local data A', 'No local data B')]

    result = filter_visible_calendar_fixtures(database, rows)

    assert [row['id'] for row in result] == [1]
    assert database.query_count == 2
