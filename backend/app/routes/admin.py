from fastapi import APIRouter, Depends, Query

from app.routes.dependencies import require_admin
from app.services.job_service import sync_and_predict


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
