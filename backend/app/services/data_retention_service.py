from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import Settings, get_settings
from app.services.supabase_repository import SupabaseRepository


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def run_safe_data_retention(
    *,
    repository: SupabaseRepository | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Prune only bounded operational data and exact duplicate fixture JSON.

    Normalized fixtures, statistics, prediction snapshots, evaluations, and
    per-market outcomes are deliberately outside this retention operation.
    """

    active_settings = settings or get_settings()
    active_repository = repository or SupabaseRepository()
    reference_time = _as_utc(now or datetime.now(timezone.utc))
    raw_cutoff = reference_time - timedelta(
        days=active_settings.retention_raw_payload_days
    )
    api_log_cutoff = reference_time - timedelta(
        days=active_settings.retention_api_log_days
    )
    is_dry_run = (
        active_settings.retention_dry_run if dry_run is None else bool(dry_run)
    )

    summary: dict[str, Any] = {
        'dry_run': is_dry_run,
        'raw_cutoff': raw_cutoff.isoformat(),
        'api_log_cutoff': api_log_cutoff.isoformat(),
        'batches': 0,
        'fixture_candidates': 0,
        'fixtures_compacted': 0,
        'api_log_candidates': 0,
        'api_logs_deleted': 0,
        'batch_limit_reached': False,
    }

    for batch_number in range(1, active_settings.retention_max_batches + 1):
        batch = await active_repository.run_safe_data_retention(
            raw_cutoff=raw_cutoff,
            api_log_cutoff=api_log_cutoff,
            max_fixtures=active_settings.retention_fixture_batch_size,
            max_api_logs=active_settings.retention_api_log_batch_size,
            dry_run=is_dry_run,
        )
        summary['batches'] = batch_number
        for field in (
            'fixture_candidates',
            'fixtures_compacted',
            'api_log_candidates',
            'api_logs_deleted',
        ):
            summary[field] += int(batch.get(field) or 0)

        fixture_batch_full = (
            int(batch.get('fixture_candidates') or 0)
            >= active_settings.retention_fixture_batch_size
        )
        log_batch_full = (
            int(batch.get('api_log_candidates') or 0)
            >= active_settings.retention_api_log_batch_size
        )
        if is_dry_run or not (fixture_batch_full or log_batch_full):
            break
    else:
        summary['batch_limit_reached'] = True

    return summary
