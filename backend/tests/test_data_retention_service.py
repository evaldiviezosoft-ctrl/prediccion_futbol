from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.config import Settings
from app.services.data_retention_service import run_safe_data_retention


class RecordingRepository:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    async def run_safe_data_retention(self, **kwargs):
        self.calls.append(kwargs)
        return self.batches.pop(0)


def _settings(**overrides) -> Settings:
    values = {
        'retention_raw_payload_days': 1825,
        'retention_api_log_days': 90,
        'retention_fixture_batch_size': 2,
        'retention_api_log_batch_size': 3,
        'retention_max_batches': 4,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_retention_uses_fixed_utc_cutoffs_and_aggregates_bounded_batches():
    repository = RecordingRepository([
        {
            'fixture_candidates': 2,
            'fixtures_compacted': 2,
            'api_log_candidates': 1,
            'api_logs_deleted': 1,
        },
        {
            'fixture_candidates': 1,
            'fixtures_compacted': 1,
            'api_log_candidates': 0,
            'api_logs_deleted': 0,
        },
    ])
    now = datetime(2026, 7, 27, 8, 30, tzinfo=timezone.utc)

    result = asyncio.run(
        run_safe_data_retention(
            repository=repository,
            settings=_settings(),
            now=now,
            dry_run=False,
        )
    )

    assert result == {
        'dry_run': False,
        'raw_cutoff': '2021-07-28T08:30:00+00:00',
        'api_log_cutoff': '2026-04-28T08:30:00+00:00',
        'batches': 2,
        'fixture_candidates': 3,
        'fixtures_compacted': 3,
        'api_log_candidates': 1,
        'api_logs_deleted': 1,
        'batch_limit_reached': False,
    }
    assert len(repository.calls) == 2
    assert repository.calls[0]['raw_cutoff'] == repository.calls[1]['raw_cutoff']
    assert repository.calls[0]['api_log_cutoff'] == repository.calls[1]['api_log_cutoff']


def test_retention_dry_run_never_repeats_or_reports_mutations():
    repository = RecordingRepository([
        {
            'fixture_candidates': 2,
            'fixtures_compacted': 0,
            'api_log_candidates': 3,
            'api_logs_deleted': 0,
        },
    ])

    result = asyncio.run(
        run_safe_data_retention(
            repository=repository,
            settings=_settings(),
            now=datetime(2026, 7, 27),
            dry_run=True,
        )
    )

    assert result['dry_run'] is True
    assert result['batches'] == 1
    assert result['fixture_candidates'] == 2
    assert result['fixtures_compacted'] == 0
    assert result['api_log_candidates'] == 3
    assert result['api_logs_deleted'] == 0
    assert repository.calls[0]['dry_run'] is True


def test_retention_stops_at_the_configured_batch_limit():
    repository = RecordingRepository([
        {
            'fixture_candidates': 2,
            'fixtures_compacted': 2,
            'api_log_candidates': 3,
            'api_logs_deleted': 3,
        },
        {
            'fixture_candidates': 2,
            'fixtures_compacted': 2,
            'api_log_candidates': 3,
            'api_logs_deleted': 3,
        },
    ])

    result = asyncio.run(
        run_safe_data_retention(
            repository=repository,
            settings=_settings(retention_max_batches=2),
            now=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
    )

    assert result['batches'] == 2
    assert result['fixtures_compacted'] == 4
    assert result['api_logs_deleted'] == 6
    assert result['batch_limit_reached'] is True
