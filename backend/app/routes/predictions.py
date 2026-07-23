from fastapi import APIRouter, Depends, HTTPException
from app.core.errors import BackendError, DatabaseError
from app.db.supabase_client import get_supabase
from app.routes.dependencies import require_admin
from app.services.prediction_service import refresh_prediction

router = APIRouter(prefix='/predictions', tags=['predictions'])


@router.get('/{fixture_id}')
def get_prediction(fixture_id: int):
    try:
        response = (
            get_supabase().table('predictions')
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
        raise HTTPException(status_code=404, detail='Todavía no existe una predicción.')
    payload = dict(response.data)
    metadata = payload.get('model_metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    goal_lines = metadata.get('goal_lines')
    possible_assistants = metadata.get('possible_assistants')
    payload['goal_lines'] = goal_lines if isinstance(goal_lines, list) else []
    payload['possible_assistants'] = (
        possible_assistants if isinstance(possible_assistants, list) else []
    )
    # This API no longer publishes exact-score tips. The legacy column remains
    # internal only so existing migrations and historical rows stay compatible.
    payload.pop('likely_scores', None)
    return payload


@router.post('/{fixture_id}/refresh', dependencies=[Depends(require_admin)])
async def refresh(fixture_id: int):
    return await refresh_prediction(fixture_id)
