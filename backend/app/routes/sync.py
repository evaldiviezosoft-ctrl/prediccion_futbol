from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.errors import BackendError, DatabaseError
from app.routes.dependencies import require_admin
from app.services.api_football_client import ApiFootballClient
from app.services.competition_resolver import CompetitionResolver
from app.services.historical_sync_service import HistoricalSyncService
from app.services.optional_fixture_sync_service import OptionalUpcomingData
from app.services.supabase_repository import SupabaseRepository
from app.services.upcoming_sync_service import UpcomingSyncService


router = APIRouter(
    prefix='/admin/sync',
    tags=['admin-sync'],
    dependencies=[Depends(require_admin)],
)

_sync_lock = asyncio.Lock()
CompetitionQuery = Annotated[list[str] | None, Query(alias='competition')]


async def _acquire_sync_lock() -> None:
    if _sync_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Ya existe una sincronización en curso.',
        )
    await _sync_lock.acquire()


def _summary_payload(summary: Any) -> dict[str, Any]:
    if hasattr(summary, 'to_dict'):
        return dict(summary.to_dict())
    if hasattr(summary, 'model_dump'):
        return dict(summary.model_dump(mode='json'))
    return dict(summary)


@router.post('/resolve-competitions')
async def resolve_competitions() -> dict[str, Any]:
    await _acquire_sync_lock()
    repository = SupabaseRepository()
    client = ApiFootballClient(request_log_sink=repository)
    try:
        batch = await CompetitionResolver(
            client,
            resolution_sink=repository,
        ).resolve_all()
        return {
            'resolved': [item.model_dump(mode='json') for item in batch.resolved],
            'unresolved': [item.model_dump(mode='json') for item in batch.unresolved],
            'rate_limit': client.rate_limit.snapshot.model_dump(mode='json'),
        }
    finally:
        await client.close()
        _sync_lock.release()


@router.post('/historical')
async def sync_historical(
    competition: CompetitionQuery = None,
    from_season: int = Query(default=2021, ge=2000, le=2100),
    to_season: int = Query(default=2026, ge=2000, le=2100),
    include_details: bool = True,
) -> dict[str, Any]:
    if from_season > to_season:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='from_season no puede ser mayor que to_season.',
        )
    await _acquire_sync_lock()
    repository = SupabaseRepository()
    client = ApiFootballClient(request_log_sink=repository)
    try:
        summary = await HistoricalSyncService(client, repository).sync(
            from_season=from_season,
            to_season=to_season,
            competitions=competition,
            include_details=include_details,
        )
        return _summary_payload(summary)
    finally:
        await client.close()
        _sync_lock.release()


@router.post('/upcoming')
async def sync_upcoming(
    competition: CompetitionQuery = None,
    days: int = Query(default=30, ge=1, le=90),
    with_injuries: bool = False,
    with_odds: bool = False,
    with_external_predictions: bool = False,
    with_lineups: bool = False,
) -> dict[str, Any]:
    await _acquire_sync_lock()
    repository = SupabaseRepository()
    client = ApiFootballClient(request_log_sink=repository)
    try:
        summary = await UpcomingSyncService(client, repository).sync(
            days=days,
            competitions=competition,
            optional=OptionalUpcomingData(
                injuries=with_injuries,
                odds=with_odds,
                external_predictions=with_external_predictions,
                lineups=with_lineups,
            ),
        )
        return _summary_payload(summary)
    finally:
        await client.close()
        _sync_lock.release()


@router.post('/resume')
async def resume_sync(
    competition: CompetitionQuery = None,
    limit: int | None = Query(default=None, ge=1, le=5000),
) -> dict[str, Any]:
    await _acquire_sync_lock()
    repository = SupabaseRepository()
    client = ApiFootballClient(request_log_sink=repository)
    try:
        summary = await HistoricalSyncService(
            client,
            repository,
        ).resume_missing_details(competitions=competition, limit=limit)
        return _summary_payload(summary)
    finally:
        await client.close()
        _sync_lock.release()


@router.get('/progress')
async def sync_progress() -> dict[str, Any]:
    try:
        return await SupabaseRepository().sync_progress()
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read synchronization progress.') from exc


@router.get('/logs')
async def sync_logs(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        return await SupabaseRepository().list_api_request_logs(limit=limit)
    except BackendError:
        raise
    except Exception as exc:
        raise DatabaseError('Could not read API request logs.') from exc
