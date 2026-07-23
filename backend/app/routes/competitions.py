from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.errors import BackendError, DatabaseError
from app.db.supabase_client import get_supabase


router = APIRouter(tags=['competitions'])


@router.get('/competitions')
def list_competitions() -> list[dict[str, Any]]:
    """Return the enabled catalog and its provider-verified seasons.

    This public endpoint reads the local Supabase copy only. It never spends an
    API-Football request.
    """

    try:
        database = get_supabase()
        competition_response = (
            database.table('competitions')
            .select(
                'id,api_league_id,internal_code,name,country,competition_type,'
                'logo_url,enabled,last_synced_at'
            )
            .eq('enabled', True)
            .order('country')
            .order('name')
            .execute()
        )
        competitions = [dict(row) for row in (competition_response.data or [])]
        if not competitions:
            return []

        competition_ids = [int(row['id']) for row in competitions]
        season_response = (
            database.table('competition_seasons')
            .select(
                'competition_id,season,start_date,end_date,is_current,'
                'coverage_json,availability_status,last_synced_at'
            )
            .in_('competition_id', competition_ids)
            .order('season', desc=True)
            .execute()
        )
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read the competition catalog.') from exc

    seasons_by_competition: dict[int, list[dict[str, Any]]] = {
        competition_id: [] for competition_id in competition_ids
    }
    for season in season_response.data or []:
        row = dict(season)
        seasons_by_competition.setdefault(int(row['competition_id']), []).append(row)
    for competition in competitions:
        competition['seasons'] = seasons_by_competition.get(int(competition['id']), [])
    return competitions
