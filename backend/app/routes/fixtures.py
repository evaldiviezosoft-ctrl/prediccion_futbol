from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.config import get_settings
from app.core.errors import BackendError, DatabaseError, FixtureNotFoundError
from app.db.supabase_client import get_supabase
from app.routes.dependencies import require_admin
from app.services.calendar_visibility import (
    filter_visible_calendar_fixtures,
    local_team_country,
)
from app.services.fixture_normalizer import UPCOMING_FIXTURE_STATUSES
from app.services.fixture_service import (
    SUPPORTED_LEAGUE_IDS,
    sync_fixtures_by_date,
)

router = APIRouter(prefix='/fixtures', tags=['fixtures'])
_TEAM_LOGO_CACHE: dict[int, tuple[bytes, str]] = {}
_MAX_TEAM_LOGO_BYTES = 1_000_000


@router.get('/upcoming')
def upcoming(
    days: int = Query(default=7, ge=1, le=30),
    competition: str | None = None,
    team_id: int | None = Query(default=None, gt=0),
):
    start = datetime.now(timezone.utc).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    try:
        database = get_supabase()
        response = (
            database.table('fixtures')
            .select(
                'id,league_id,season,round,kickoff,fixture_date_lima,timezone,venue_name,'
                'status_short,home_team_id,away_team_id,home_team_name,'
                'away_team_name,created_at,updated_at'
            )
            .gte('kickoff', start)
            .lt('kickoff', end)
            .in_('status_short', sorted(UPCOMING_FIXTURE_STATUSES))
            .order('kickoff')
            .execute()
        )
        rows = [dict(row) for row in (response.data or [])]
        rows = filter_visible_calendar_fixtures(database, rows)
        if not rows:
            return []

        league_ids = sorted({int(row['league_id']) for row in rows})
        fixture_ids = sorted({int(row['id']) for row in rows})
        team_ids = sorted({
            int(team_id)
            for row in rows
            for team_id in (row['home_team_id'], row['away_team_id'])
        })
        league_response = (
            database.table('leagues')
            .select('id,code,name,country')
            .in_('id', league_ids)
            .execute()
        )
        prediction_response = (
            database.table('predictions')
            .select('fixture_id,stage')
            .in_('fixture_id', fixture_ids)
            .eq('published', True)
            .execute()
        )
        team_response = (
            database.table('teams')
            .select('api_team_id,country,logo_url')
            .in_('api_team_id', team_ids)
            .execute()
        )
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read upcoming fixtures.') from exc

    leagues = {int(row['id']): row for row in (league_response.data or [])}
    predictions = {int(row['fixture_id']): row for row in (prediction_response.data or [])}
    teams = {
        int(row['api_team_id']): dict(row)
        for row in (team_response.data or [])
    }
    for row in rows:
        league = leagues.get(int(row['league_id']), {})
        prediction = predictions.get(int(row['id']))
        row['league_name'] = league.get('name')
        row['league_code'] = league.get('code')
        row['prediction_available'] = prediction is not None
        row['prediction_stage'] = prediction.get('stage') if prediction else None
        row['prediction_model_available'] = int(row['league_id']) in SUPPORTED_LEAGUE_IDS
        row['prediction_fallback_available'] = bool(
            row.get('prediction_fallback_available', False)
        )
        home_team = teams.get(int(row['home_team_id']), {})
        away_team = teams.get(int(row['away_team_id']), {})
        league_country = league.get('country')
        modeled_league_country = (
            league_country
            if int(row['league_id']) in SUPPORTED_LEAGUE_IDS
            else None
        )
        row['home_team_country'] = (
            home_team.get('country')
            or local_team_country(row['home_team_name'])
            or modeled_league_country
        )
        row['away_team_country'] = (
            away_team.get('country')
            or local_team_country(row['away_team_name'])
            or modeled_league_country
        )
        row['home_team_logo_url'] = home_team.get('logo_url')
        row['away_team_logo_url'] = away_team.get('logo_url')
        row['home_team_logo_proxy_path'] = (
            f"/fixtures/team-logo/{int(row['home_team_id'])}"
            if row['home_team_logo_url']
            else None
        )
        row['away_team_logo_proxy_path'] = (
            f"/fixtures/team-logo/{int(row['away_team_id'])}"
            if row['away_team_logo_url']
            else None
        )
    if competition:
        wanted = competition.casefold()
        rows = [
            row for row in rows
            if str(row.get('league_code') or '').casefold() == wanted
            or str(row.get('league_name') or '').casefold() == wanted
        ]
    if team_id is not None:
        rows = [
            row for row in rows
            if int(row['home_team_id']) == team_id or int(row['away_team_id']) == team_id
        ]
    return rows


@router.get('/today')
def today():
    """Return today's fixtures using the configured Lima-local calendar day."""

    lima = ZoneInfo(get_settings().api_timezone)
    local_today = datetime.now(timezone.utc).astimezone(lima).date()
    local_start = datetime.combine(local_today, time.min, tzinfo=lima)
    start = local_start.astimezone(timezone.utc).isoformat()
    end = (local_start + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    try:
        database = get_supabase()
        response = (
            database.table('fixtures')
            .select(
                'id,api_fixture_id,league_id,competition_id,season,round,stage,'
                'fixture_date_utc,fixture_date_lima,kickoff,timezone,venue_name,'
                'status_short,status_long,elapsed,home_team_id,away_team_id,'
                'home_team_name,away_team_name,home_goals,away_goals'
            )
            .gte('fixture_date_utc', start)
            .lt('fixture_date_utc', end)
            .order('fixture_date_utc')
            .execute()
        )
        return _enrich_fixture_rows(database, response.data or [])
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read today fixtures.') from exc


@router.post('/sync-date', dependencies=[Depends(require_admin)])
async def sync_date(date: date_type, timezone_name: str | None = None):
    result = await sync_fixtures_by_date(
        date,
        timezone_name or get_settings().default_timezone,
    )
    return {'synced': result.synced, 'rate_limit': result.rate_limit}


async def _fetch_team_logo(api_team_id: int) -> tuple[bytes, str]:
    cached = _TEAM_LOGO_CACHE.get(api_team_id)
    if cached is not None:
        return cached

    url = f'https://media.api-sports.io/football/teams/{api_team_id}.png'
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(8.0),
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    media_type = response.headers.get('content-type', '').split(';', 1)[0].lower()
    content = response.content
    if not media_type.startswith('image/') or len(content) > _MAX_TEAM_LOGO_BYTES:
        raise ValueError('The team logo response is invalid.')
    if len(_TEAM_LOGO_CACHE) >= 256:
        _TEAM_LOGO_CACHE.pop(next(iter(_TEAM_LOGO_CACHE)))
    value = (content, media_type)
    _TEAM_LOGO_CACHE[api_team_id] = value
    return value


@router.get('/team-logo/{api_team_id}')
async def team_logo(api_team_id: int):
    if api_team_id <= 0:
        raise HTTPException(status_code=404, detail='Escudo no encontrado.')
    try:
        content, media_type = await _fetch_team_logo(api_team_id)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail='No se pudo cargar el escudo.') from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={'Cache-Control': 'public, max-age=86400'},
    )


def _one_fixture(fixture_id: int) -> tuple[Any, dict[str, Any]]:
    try:
        database = get_supabase()
        response = (
            database.table('fixtures')
            .select(
                'id,api_fixture_id,league_id,competition_id,season,round,stage,'
                'group_name,leg,aggregate_home,aggregate_away,fixture_date_utc,'
                'fixture_date_lima,kickoff,timezone,status_short,status_long,elapsed,'
                'home_team_id,away_team_id,home_team_name,away_team_name,venue_id,'
                'venue_name,referee,home_goals,away_goals,halftime_home,halftime_away,'
                'fulltime_home,fulltime_away,extratime_home,extratime_away,'
                'penalties_home,penalties_away,winner_team_id,created_at,updated_at'
            )
            .eq('api_fixture_id', fixture_id)
            .limit(1)
            .execute()
        )
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read the fixture.') from exc
    rows = [dict(row) for row in (response.data or [])]
    if not rows:
        raise FixtureNotFoundError(f'Fixture {fixture_id} was not found.')
    return database, rows[0]


def _enrich_fixture_rows(database: Any, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in values]
    if not rows:
        return rows
    competition_ids = sorted(
        {int(row['competition_id']) for row in rows if row.get('competition_id') is not None}
    )
    fixture_ids = sorted({int(row.get('api_fixture_id') or row['id']) for row in rows})
    competition_rows = []
    if competition_ids:
        competition_rows = (
            database.table('competitions')
            .select('id,api_league_id,internal_code,name,country,competition_type,logo_url')
            .in_('id', competition_ids)
            .execute()
            .data
            or []
        )
    prediction_rows = (
        database.table('predictions')
        .select('fixture_id,stage')
        .in_('fixture_id', fixture_ids)
        .eq('published', True)
        .execute()
        .data
        or []
    )
    competitions = {int(row['id']): dict(row) for row in competition_rows}
    predictions = {int(row['fixture_id']): dict(row) for row in prediction_rows}
    for row in rows:
        fixture_id = int(row.get('api_fixture_id') or row['id'])
        competition = competitions.get(int(row['competition_id'])) if row.get('competition_id') else None
        row['competition'] = competition
        prediction = predictions.get(fixture_id)
        row['prediction_available'] = prediction is not None
        row['prediction_stage'] = prediction.get('stage') if prediction else None
    return rows


@router.get('/{fixture_id}')
def fixture_detail(fixture_id: int):
    database, row = _one_fixture(fixture_id)
    return _enrich_fixture_rows(database, [row])[0]


@router.get('/{fixture_id}/statistics')
def fixture_statistics(fixture_id: int):
    database, _fixture = _one_fixture(fixture_id)
    try:
        response = (
            database.table('fixture_team_statistics')
            .select(
                'fixture_id,team_id,is_home,shots_on_goal,shots_off_goal,total_shots,'
                'blocked_shots,shots_inside_box,shots_outside_box,fouls,corners,offsides,'
                'possession_percentage,yellow_cards,red_cards,goalkeeper_saves,total_passes,'
                'passes_accurate,passes_percentage,expected_goals'
            )
            .eq('fixture_id', fixture_id)
            .execute()
        )
        rows = [dict(row) for row in (response.data or [])]
        return _attach_teams(database, rows)
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read fixture statistics.') from exc


@router.get('/{fixture_id}/lineups')
def fixture_lineups(fixture_id: int):
    database, _fixture = _one_fixture(fixture_id)
    try:
        response = (
            database.table('lineups')
            .select('id,fixture_id,team_id,formation,coach_api_id,coach_name,confirmed,fetched_at')
            .eq('fixture_id', fixture_id)
            .execute()
        )
        rows = _attach_teams(database, [dict(row) for row in (response.data or [])])
        lineup_ids = [int(row['id']) for row in rows]
        players_by_lineup: dict[int, list[dict[str, Any]]] = {value: [] for value in lineup_ids}
        if lineup_ids:
            player_response = (
                database.table('lineup_players')
                .select(
                    'lineup_id,lineup_order,player_id,api_player_id,player_name,number,'
                    'position,grid_position,starter,substitute'
                )
                .in_('lineup_id', lineup_ids)
                .order('lineup_order')
                .execute()
            )
            for value in player_response.data or []:
                player = dict(value)
                players_by_lineup[int(player['lineup_id'])].append(player)
        for row in rows:
            row['players'] = players_by_lineup.get(int(row['id']), [])
        return rows
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read fixture lineups.') from exc


@router.get('/{fixture_id}/players')
def fixture_players(fixture_id: int):
    database, _fixture = _one_fixture(fixture_id)
    try:
        response = (
            database.table('fixture_player_statistics')
            .select(
                'fixture_id,player_id,team_id,position,starter,captain,substitute,minutes,'
                'rating,shots_total,shots_on,goals,assists,saves,passes_total,passes_key,'
                'passes_accuracy,tackles_total,interceptions,duels_total,duels_won,'
                'dribbles_attempts,dribbles_success,fouls_drawn,fouls_committed,'
                'yellow_cards,red_cards,penalty_won,penalty_committed,penalty_scored,'
                'penalty_missed,penalty_saved'
            )
            .eq('fixture_id', fixture_id)
            .execute()
        )
        rows = [dict(row) for row in (response.data or [])]
        player_ids = sorted({int(row['player_id']) for row in rows})
        players = {}
        if player_ids:
            player_response = (
                database.table('players')
                .select(
                    'id,api_player_id,name,firstname,lastname,age,birth_date,nationality,'
                    'height,weight,injured,photo_url'
                )
                .in_('id', player_ids)
                .execute()
            )
            players = {int(row['id']): dict(row) for row in (player_response.data or [])}
        rows = _attach_teams(database, rows)
        for row in rows:
            row['player'] = players.get(int(row['player_id']))
        return rows
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read fixture player statistics.') from exc


def _attach_teams(database: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    team_ids = sorted({int(row['team_id']) for row in rows if row.get('team_id') is not None})
    teams = {}
    if team_ids:
        response = (
            database.table('teams')
            .select('id,api_team_id,name,code,country,logo_url')
            .in_('id', team_ids)
            .execute()
        )
        teams = {int(row['id']): dict(row) for row in (response.data or [])}
    for row in rows:
        row['team'] = teams.get(int(row['team_id'])) if row.get('team_id') is not None else None
    return rows
