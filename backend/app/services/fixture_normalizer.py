from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


LIMA_TIMEZONE = ZoneInfo('America/Lima')
FINAL_FIXTURE_STATUSES = frozenset({'FT', 'AET', 'PEN', 'AWD', 'WO', 'CANC', 'ABD'})
UPCOMING_FIXTURE_STATUSES = frozenset({'TBD', 'NS', 'PST'})


class FixtureNormalizationError(ValueError):
    """Raised when API-Football omits an identity required for safe upserts."""


@dataclass(slots=True)
class NormalizedFixture:
    fixture: dict[str, Any]
    teams: list[dict[str, Any]] = field(default_factory=list)
    venue: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    team_statistics: list[dict[str, Any]] = field(default_factory=list)
    players: list[dict[str, Any]] = field(default_factory=list)
    player_statistics: list[dict[str, Any]] = field(default_factory=list)
    lineups: list[dict[str, Any]] = field(default_factory=list)
    lineup_players: list[dict[str, Any]] = field(default_factory=list)
    components_present: dict[str, bool] = field(default_factory=dict)

    @property
    def api_fixture_id(self) -> int:
        return int(self.fixture['api_fixture_id'])


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _positive_integer(value: Any) -> int | None:
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _bounded_nonnegative_integer(value: Any, maximum: int) -> int | None:
    parsed = _integer(value)
    return parsed if parsed is not None and 0 <= parsed <= maximum else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '.')
    if not text or text.lower() in {'null', 'none', 'n/a', '-'}:
        return None
    if text.endswith('%'):
        text = text[:-1].strip()
    try:
        return float(text)
    except ValueError:
        return None


def normalize_percentage(value: Any) -> float | None:
    """Return percentages on a consistent 0..100 scale without inventing zeroes."""

    number = _number(value)
    if number is None:
        return None
    if not 0 <= number <= 100:
        return None
    return number


def _normalized_key(value: Any) -> str:
    text = unicodedata.normalize('NFKD', str(value or ''))
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', '', text.lower())


STATISTIC_KEYS: dict[str, str] = {
    'shotsongoal': 'shots_on_goal',
    'shotsoffgoal': 'shots_off_goal',
    'totalshots': 'total_shots',
    'blockedshots': 'blocked_shots',
    'shotsinsidebox': 'shots_inside_box',
    'shotsoutsidebox': 'shots_outside_box',
    'fouls': 'fouls',
    'cornerkicks': 'corners',
    'corners': 'corners',
    'offsides': 'offsides',
    'ballpossession': 'possession_percentage',
    'possession': 'possession_percentage',
    'yellowcards': 'yellow_cards',
    'redcards': 'red_cards',
    'goalkeepersaves': 'goalkeeper_saves',
    'keepersaves': 'goalkeeper_saves',
    'totalpasses': 'total_passes',
    'passesaccurate': 'passes_accurate',
    'passespercentage': 'passes_percentage',
    'passaccuracy': 'passes_percentage',
    'expectedgoals': 'expected_goals',
    'xg': 'expected_goals',
}

PERCENTAGE_STATISTICS = frozenset({'possession_percentage', 'passes_percentage'})
FLOAT_STATISTICS = frozenset({'expected_goals'})


def normalize_team_statistics(values: Any) -> dict[str, int | float | None]:
    """Normalize API-Football statistic labels, including spacing/case variants."""

    normalized: dict[str, int | float | None] = {
        column: None for column in dict.fromkeys(STATISTIC_KEYS.values())
    }
    for item in _sequence(values):
        entry = _mapping(item)
        column = STATISTIC_KEYS.get(_normalized_key(entry.get('type')))
        if column is None:
            continue
        raw_value = entry.get('value')
        if column in PERCENTAGE_STATISTICS:
            normalized[column] = normalize_percentage(raw_value)
        elif column in FLOAT_STATISTICS:
            normalized[column] = _number(raw_value)
        else:
            normalized[column] = _integer(raw_value)
    return normalized


def _parse_fixture_dates(value: Any, timestamp: Any) -> tuple[str, str, int | None]:
    parsed: datetime | None = None
    if value:
        text = str(value).strip().replace('Z', '+00:00')
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
    parsed_timestamp = _integer(timestamp)
    if parsed is None and parsed_timestamp is not None:
        parsed = datetime.fromtimestamp(parsed_timestamp, tz=timezone.utc)
    if parsed is None:
        raise FixtureNormalizationError('Fixture date and timestamp are both invalid.')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_date = parsed.astimezone(timezone.utc)
    if parsed_timestamp is None:
        parsed_timestamp = int(utc_date.timestamp())
    lima_date = utc_date.astimezone(LIMA_TIMEZONE).replace(tzinfo=None)
    return utc_date.isoformat(), lima_date.isoformat(), parsed_timestamp


def _cup_metadata(round_name: str | None) -> tuple[str | None, str | None, str | None]:
    if not round_name:
        return None, None, None
    lower = round_name.lower()
    stage = None
    for marker, value in (
        ('qualif', 'qualifying'),
        ('preliminary', 'qualifying'),
        ('group', 'group_stage'),
        ('play-off', 'playoff'),
        ('playoff', 'playoff'),
        ('round of 16', 'round_of_16'),
        ('8th final', 'round_of_16'),
        ('quarter', 'quarterfinal'),
        ('semi', 'semifinal'),
        ('final', 'final'),
    ):
        if marker in lower:
            stage = value
            break
    group_match = re.search(r'\bgroup\s+([a-z0-9-]+)', round_name, flags=re.IGNORECASE)
    group_name = (
        group_match.group(1).upper()
        if group_match and group_match.group(1).lower() != 'stage'
        else None
    )
    leg = None
    if re.search(r'\b(1st|first)\s+leg\b', lower):
        leg = 'first'
    elif re.search(r'\b(2nd|second)\s+leg\b', lower):
        leg = 'second'
    return stage, group_name, leg


def _winner_team_id(
    home: Mapping[str, Any],
    away: Mapping[str, Any],
    goals: Mapping[str, Any],
    penalty: Mapping[str, Any],
) -> int | None:
    if home.get('winner') is True:
        return _integer(home.get('id'))
    if away.get('winner') is True:
        return _integer(away.get('id'))
    home_penalty, away_penalty = _integer(penalty.get('home')), _integer(penalty.get('away'))
    if home_penalty is not None and away_penalty is not None and home_penalty != away_penalty:
        return _integer(home.get('id') if home_penalty > away_penalty else away.get('id'))
    home_goals, away_goals = _integer(goals.get('home')), _integer(goals.get('away'))
    if home_goals is not None and away_goals is not None and home_goals != away_goals:
        return _integer(home.get('id') if home_goals > away_goals else away.get('id'))
    return None


def _team_row(team: Mapping[str, Any]) -> dict[str, Any]:
    api_team_id = _positive_integer(team.get('id'))
    if api_team_id is None:
        raise FixtureNormalizationError('Fixture team is missing its API identifier.')
    name = str(team.get('name') or '').strip()
    if not name:
        raise FixtureNormalizationError('Fixture team is missing its name.')
    return {
        'api_team_id': api_team_id,
        'name': name,
        'code': team.get('code'),
        'country': team.get('country'),
        'founded': _integer(team.get('founded')),
        'national': team.get('national') if isinstance(team.get('national'), bool) else None,
        'logo_url': team.get('logo'),
    }


def _player_row(player: Mapping[str, Any]) -> dict[str, Any] | None:
    api_player_id = _positive_integer(player.get('id'))
    if api_player_id is None:
        return None
    name = str(player.get('name') or '').strip()
    if not name:
        return None
    birth = _mapping(player.get('birth'))
    return {
        'api_player_id': api_player_id,
        'name': name,
        'firstname': player.get('firstname'),
        'lastname': player.get('lastname'),
        'age': _integer(player.get('age')),
        'birth_date': player.get('birth_date') or birth.get('date'),
        'nationality': player.get('nationality'),
        'height': player.get('height'),
        'weight': player.get('weight'),
        'injured': player.get('injured') if isinstance(player.get('injured'), bool) else None,
        'photo_url': player.get('photo'),
    }


def _player_stat_row(
    fixture_id: int,
    team_id: int,
    player_id: int,
    statistics: Mapping[str, Any],
) -> dict[str, Any]:
    games = _mapping(statistics.get('games'))
    shots = _mapping(statistics.get('shots'))
    goals = _mapping(statistics.get('goals'))
    passes = _mapping(statistics.get('passes'))
    tackles = _mapping(statistics.get('tackles'))
    duels = _mapping(statistics.get('duels'))
    dribbles = _mapping(statistics.get('dribbles'))
    fouls = _mapping(statistics.get('fouls'))
    cards = _mapping(statistics.get('cards'))
    penalty = _mapping(statistics.get('penalty'))
    substitute = games.get('substitute') if isinstance(games.get('substitute'), bool) else None
    return {
        'fixture_id': fixture_id,
        'player_id': player_id,
        'team_id': team_id,
        'position': games.get('position'),
        'starter': (not substitute) if substitute is not None else False,
        'captain': bool(games.get('captain')),
        'substitute': substitute if substitute is not None else False,
        'minutes': _integer(games.get('minutes')),
        'rating': _number(games.get('rating')),
        'shots_total': _integer(shots.get('total')),
        'shots_on': _integer(shots.get('on')),
        'goals': _integer(goals.get('total')),
        'assists': _integer(goals.get('assists')),
        'saves': _integer(goals.get('saves')),
        'passes_total': _integer(passes.get('total')),
        'passes_key': _integer(passes.get('key')),
        'passes_accuracy': normalize_percentage(passes.get('accuracy')),
        'tackles_total': _integer(tackles.get('total')),
        'interceptions': _integer(tackles.get('interceptions')),
        'duels_total': _integer(duels.get('total')),
        'duels_won': _integer(duels.get('won')),
        'dribbles_attempts': _integer(dribbles.get('attempts')),
        'dribbles_success': _integer(dribbles.get('success')),
        'fouls_drawn': _integer(fouls.get('drawn')),
        'fouls_committed': _integer(fouls.get('committed')),
        'yellow_cards': _integer(cards.get('yellow')),
        'red_cards': _integer(cards.get('red')),
        'penalty_won': _integer(penalty.get('won')),
        'penalty_committed': _integer(penalty.get('commited') or penalty.get('committed')),
        'penalty_scored': _integer(penalty.get('scored')),
        'penalty_missed': _integer(penalty.get('missed')),
        'penalty_saved': _integer(penalty.get('saved')),
        'raw_json': dict(statistics),
    }


def normalize_fixture(
    item: Mapping[str, Any],
    *,
    competition_id: Any,
) -> NormalizedFixture:
    """Normalize one API-Football fixture response into relational upsert rows."""

    fixture = _mapping(item.get('fixture'))
    league = _mapping(item.get('league'))
    teams = _mapping(item.get('teams'))
    home, away = _mapping(teams.get('home')), _mapping(teams.get('away'))
    goals = _mapping(item.get('goals'))
    score = _mapping(item.get('score'))
    halftime = _mapping(score.get('halftime'))
    fulltime = _mapping(score.get('fulltime'))
    extratime = _mapping(score.get('extratime'))
    penalty = _mapping(score.get('penalty'))
    status = _mapping(fixture.get('status'))
    venue = _mapping(fixture.get('venue'))

    api_fixture_id = _positive_integer(fixture.get('id'))
    api_league_id = _positive_integer(league.get('id'))
    season = _integer(league.get('season'))
    if api_fixture_id is None or api_league_id is None or season is None:
        raise FixtureNormalizationError('Fixture identity, league, or season is missing.')
    home_row, away_row = _team_row(home), _team_row(away)
    date_utc, date_lima, fixture_timestamp = _parse_fixture_dates(
        fixture.get('date'), fixture.get('timestamp')
    )
    round_name = str(league.get('round') or '').strip() or None
    stage, group_name, leg = _cup_metadata(round_name)
    status_short = str(status.get('short') or '').strip().upper() or None

    venue_row = None
    api_venue_id = _positive_integer(venue.get('id'))
    if api_venue_id is not None:
        venue_row = {
            'api_venue_id': api_venue_id,
            'name': venue.get('name'),
            'city': venue.get('city'),
            'address': venue.get('address'),
            'capacity': _integer(venue.get('capacity')),
            'surface': venue.get('surface'),
            'image_url': venue.get('image'),
        }

    fixture_row = {
        # Legacy columns remain populated so predictions and the Flutter API keep working.
        'id': api_fixture_id,
        'api_fixture_id': api_fixture_id,
        'league_id': api_league_id,
        'competition_id': competition_id,
        'season': season,
        'round': round_name,
        'stage': stage,
        'group_name': group_name,
        'leg': leg,
        'kickoff': date_utc,
        'fixture_date_utc': date_utc,
        'fixture_date_lima': date_lima,
        'timestamp': fixture_timestamp,
        'timezone': fixture.get('timezone'),
        'status_short': status_short,
        'status_long': status.get('long'),
        'elapsed': _integer(status.get('elapsed')),
        'home_team_id': home_row['api_team_id'],
        'away_team_id': away_row['api_team_id'],
        'home_team_name': home_row['name'],
        'away_team_name': away_row['name'],
        'venue_id': api_venue_id,
        'venue_name': venue.get('name'),
        'referee': fixture.get('referee'),
        'home_goals': _integer(goals.get('home')),
        'away_goals': _integer(goals.get('away')),
        'halftime_home': _integer(halftime.get('home')),
        'halftime_away': _integer(halftime.get('away')),
        'fulltime_home': _integer(fulltime.get('home')),
        'fulltime_away': _integer(fulltime.get('away')),
        'extratime_home': _integer(extratime.get('home')),
        'extratime_away': _integer(extratime.get('away')),
        'penalties_home': _integer(penalty.get('home')),
        'penalties_away': _integer(penalty.get('away')),
        'winner_team_id': _winner_team_id(home, away, goals, penalty),
        'raw_payload': dict(item),
        'raw_json': dict(item),
    }

    normalized = NormalizedFixture(
        fixture=fixture_row,
        teams=[home_row, away_row],
        venue=venue_row,
        components_present={
            'events': isinstance(item.get('events'), list),
            'statistics': isinstance(item.get('statistics'), list),
            'lineups': isinstance(item.get('lineups'), list),
            'players': isinstance(item.get('players'), list),
        },
    )

    for index, raw_event in enumerate(_sequence(item.get('events'))):
        event = _mapping(raw_event)
        event_time = _mapping(event.get('time'))
        event_team = _mapping(event.get('team'))
        player = _mapping(event.get('player'))
        assist = _mapping(event.get('assist'))
        event_type = str(event.get('type') or '').strip()
        if not event_type:
            continue
        normalized.events.append({
            'fixture_id': api_fixture_id,
            'event_order': index,
            'api_team_id': _positive_integer(event_team.get('id')),
            'api_player_id': _positive_integer(player.get('id')),
            'api_assist_id': _positive_integer(assist.get('id')),
            'minute': _bounded_nonnegative_integer(event_time.get('elapsed'), 200),
            'extra_minute': _bounded_nonnegative_integer(event_time.get('extra'), 100),
            'event_type': event_type,
            'detail': event.get('detail'),
            'comments': event.get('comments'),
            'raw_json': dict(event),
        })

    for raw_team_stats in _sequence(item.get('statistics')):
        team_stats = _mapping(raw_team_stats)
        team = _mapping(team_stats.get('team'))
        team_id = _positive_integer(team.get('id'))
        if team_id is None:
            continue
        normalized.team_statistics.append({
            'fixture_id': api_fixture_id,
            'team_id': team_id,
            'is_home': team_id == home_row['api_team_id'],
            **normalize_team_statistics(team_stats.get('statistics')),
            'raw_statistics_json': {'items': list(_sequence(team_stats.get('statistics')))},
        })

    seen_players: set[int] = set()
    for team_group_raw in _sequence(item.get('players')):
        team_group = _mapping(team_group_raw)
        team_id = _positive_integer(_mapping(team_group.get('team')).get('id'))
        if team_id is None:
            continue
        for player_entry_raw in _sequence(team_group.get('players')):
            player_entry = _mapping(player_entry_raw)
            player = _mapping(player_entry.get('player'))
            player_row = _player_row(player)
            if player_row is None:
                continue
            player_id = int(player_row['api_player_id'])
            if player_id not in seen_players:
                normalized.players.append(player_row)
                seen_players.add(player_id)
            stats_items = _sequence(player_entry.get('statistics'))
            if stats_items:
                normalized.player_statistics.append(
                    _player_stat_row(api_fixture_id, team_id, player_id, _mapping(stats_items[0]))
                )

    for raw_lineup in _sequence(item.get('lineups')):
        lineup = _mapping(raw_lineup)
        team = _mapping(lineup.get('team'))
        coach = _mapping(lineup.get('coach'))
        team_id = _positive_integer(team.get('id'))
        if team_id is None:
            continue
        lineup_key = f'{api_fixture_id}:{team_id}'
        normalized.lineups.append({
            'fixture_id': api_fixture_id,
            'team_id': team_id,
            'lineup_key': lineup_key,
            'formation': lineup.get('formation'),
            'coach_api_id': _positive_integer(coach.get('id')),
            'coach_name': coach.get('name'),
            'confirmed': bool(lineup.get('formation') or lineup.get('startXI')),
            'raw_json': dict(lineup),
        })
        lineup_order = 0
        for starter, entries in (
            (True, lineup.get('startXI')),
            (False, lineup.get('substitutes')),
        ):
            for player_entry_raw in _sequence(entries):
                player_entry = _mapping(player_entry_raw)
                player = _mapping(player_entry.get('player')) or player_entry
                player_id = _positive_integer(player.get('id'))
                player_name = str(player.get('name') or '').strip()
                if player_id is None or not player_name:
                    continue
                if player_id not in seen_players:
                    normalized.players.append({
                        'api_player_id': player_id,
                        'name': player_name,
                        'firstname': None,
                        'lastname': None,
                        'age': None,
                        'birth_date': None,
                        'nationality': None,
                        'height': None,
                        'weight': None,
                        'injured': None,
                        'photo_url': player.get('photo'),
                    })
                    seen_players.add(player_id)
                normalized.lineup_players.append({
                    'lineup_key': lineup_key,
                    'lineup_order': lineup_order,
                    'player_id': player_id,
                    'player_name': player_name,
                    'number': _integer(player.get('number')),
                    'position': player.get('pos') or player.get('position'),
                    'grid_position': player.get('grid'),
                    'starter': starter,
                    'substitute': not starter,
                })
                lineup_order += 1

    return normalized


def response_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
