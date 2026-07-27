from fastapi import APIRouter, Depends, Query

from app.routes.dependencies import require_admin
from app.services.data_retention_service import run_safe_data_retention
from app.services.job_service import sync_and_predict
from app.services.prediction_evaluation_service import (
    sync_and_evaluate_published_predictions,
)


router = APIRouter(
    prefix='/admin/jobs',
    tags=['admin'],
    dependencies=[Depends(require_admin)],
)


@router.post('/sync-and-predict')
async def run_sync_and_predict(
    horizon_days: int | None = Query(default=None, ge=1, le=30),
    max_matches: int | None = Query(default=None, ge=1, le=25),
    timezone_name: str | None = None,
):
    return await sync_and_predict(
        horizon_days=horizon_days,
        max_matches=max_matches,
        timezone_name=timezone_name,
    )


@router.post('/evaluate-postmatch')
async def run_postmatch_evaluation(
    lookback_days: int = Query(default=7, ge=1, le=30),
    max_matches: int = Query(default=100, ge=1, le=100),
):
    return await sync_and_evaluate_published_predictions(
        lookback_days=lookback_days,
        max_matches=max_matches,
    )


@router.post('/data-retention')
async def run_data_retention(
    dry_run: bool = Query(default=True),
):
    return await run_safe_data_retention(dry_run=dry_run)
