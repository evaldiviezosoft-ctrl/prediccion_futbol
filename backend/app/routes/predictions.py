from fastapi import APIRouter, Depends, HTTPException

from app.core.errors import BackendError, DatabaseError
from app.db.supabase_client import get_supabase
from app.routes.dependencies import require_admin
from app.services.calendar_visibility import local_team_country
from app.services.prediction_service import refresh_prediction
from app.services.probable_forecast_service import (
    build_probable_forecast,
    validated_market_forecast,
)
from app.services.supabase_repository import SupabaseRepository


router = APIRouter(prefix='/predictions', tags=['predictions'])


@router.get('/performance/summary')
async def performance_summary():
    try:
        return await SupabaseRepository().prediction_performance_summary()
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read prediction performance.') from exc


@router.get('/{fixture_id}')
def get_prediction(fixture_id: int):
    try:
        database = get_supabase()
        response = (
            database.table('predictions')
            .select('*')
            .eq('fixture_id', fixture_id)
            .eq('published', True)
            .maybe_single()
            .execute()
        )
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read the prediction.') from exc
    if response is None or not response.data:
        raise HTTPException(
            status_code=404,
            detail='Todavía no existe una predicción.',
        )
    payload = dict(response.data)
    metadata = payload.get('model_metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    goal_lines = metadata.get('goal_lines')
    possible_assistants = metadata.get('possible_assistants')
    payload['goal_lines'] = goal_lines if isinstance(goal_lines, list) else []
    payload['possible_assistants'] = (
        possible_assistants if isinstance(possible_assistants, list) else []
    )
    payload['probable_forecast'] = build_probable_forecast(payload)
    payload['market_forecast'] = validated_market_forecast(
        payload.get('market_forecast')
    )
    team_ids = [
        int(team_id)
        for team_id in (
            payload.get('home_team_id'),
            payload.get('away_team_id'),
        )
        if team_id is not None
    ]
    if team_ids:
        try:
            team_response = (
                database.table('teams')
                .select('api_team_id,country')
                .in_('api_team_id', team_ids)
                .execute()
            )
        except Exception as exc:
            raise DatabaseError(
                'Could not read prediction team metadata.'
            ) from exc
        countries = {
            int(row['api_team_id']): row.get('country')
            for row in (team_response.data or [])
        }
        payload['home_team_country'] = (
            countries.get(int(payload['home_team_id']))
            or local_team_country(payload.get('home_team_name'))
        )
        payload['away_team_country'] = (
            countries.get(int(payload['away_team_id']))
            or local_team_country(payload.get('away_team_name'))
        )
    # This API no longer publishes exact-score tips. The legacy column remains
    # internal only so existing migrations and historical rows stay compatible.
    payload.pop('likely_scores', None)
    return payload


@router.post('/{fixture_id}/refresh', dependencies=[Depends(require_admin)])
async def refresh(fixture_id: int):
    return await refresh_prediction(fixture_id)
