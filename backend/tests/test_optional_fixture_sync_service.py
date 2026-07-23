from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.services.fixture_normalizer import normalize_fixture, response_hash
from app.services.optional_fixture_sync_service import (
    OptionalFixtureSyncOptions,
    OptionalFixtureSyncService,
)
from app.services.supabase_repository import SupabaseRepository
from tests.test_fixture_normalizer import fixture_payload


NOW = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)


def _fixture(*, kickoff_minutes: int = 60):
    item = fixture_payload(fixture_id=9001)
    item['fixture']['date'] = (NOW + timedelta(minutes=kickoff_minutes)).isoformat()
    return normalize_fixture(item, competition_id=7)


class FakeOptionalClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.lineups: list[dict[str, Any]] = []

    async def fixture_injuries(self, fixture_id: int):
        self.calls.append(('injuries', fixture_id))
        return [{'player': {'id': 8, 'name': 'Player', 'type': 'Missing Fixture'}}]

    async def fixture_odds(self, fixture_id: int):
        self.calls.append(('odds', fixture_id))
        return {'response': [{'fixture': {'id': fixture_id}, 'bookmakers': []}]}

    async def fixture_external_prediction(self, fixture_id: int):
        self.calls.append(('prediction', fixture_id))
        return {'response': [{'predictions': {'winner': {'id': 1}}}]}

    async def fixture_lineups(self, fixture_id: int):
        self.calls.append(('lineups', fixture_id))
        return self.lineups


class FakeOptionalRepository:
    def __init__(self) -> None:
        self.status: dict[str, Any] = {'fixture_id': 9001}
        self.persisted: list[tuple[str, Any]] = []
        self.status_reads = 0

    async def optional_sync_status(self, fixture_id: int):
        self.status_reads += 1
        return dict(self.status)

    async def list_optional_fixture_candidates(self, **_kwargs):
        return []

    async def persist_injuries(self, fixture_id, injuries, *, fetched_at):
        self.persisted.append(('injuries', list(injuries)))
        self.status.update({
            'injuries_last_fetched_at': fetched_at,
            'injuries_next_allowed_at': (
                datetime.fromisoformat(fetched_at) + timedelta(hours=4)
            ).isoformat(),
        })

    async def persist_odds_snapshot(self, fixture_id, payload, *, fetched_at):
        self.persisted.append(('odds', response_hash(payload)))

    async def persist_external_prediction(self, fixture_id, payload, *, fetched_at):
        self.persisted.append(('prediction', response_hash(payload)))

    async def persist_fixture_lineups(
        self,
        normalized,
        *,
        fetched_at,
        next_retry_at,
        confirmed,
    ):
        self.persisted.append(('lineups', confirmed))
        self.status.update({
            'lineups_last_fetched_at': fetched_at,
            'lineups_next_retry_at': next_retry_at,
            'lineups_confirmed_at': fetched_at if confirmed else None,
        })


def test_default_options_make_no_optional_requests_or_status_reads():
    client = FakeOptionalClient()
    repository = FakeOptionalRepository()
    result = asyncio.run(
        OptionalFixtureSyncService(client, repository).sync_many([_fixture()])
    )

    assert result.downloaded == 0
    assert result.skipped == 0
    assert client.calls == []
    assert repository.status_reads == 0


def test_injuries_are_fetched_at_most_once_every_four_hours():
    client = FakeOptionalClient()
    repository = FakeOptionalRepository()
    repository.status['injuries_last_fetched_at'] = (NOW - timedelta(hours=2)).isoformat()
    service = OptionalFixtureSyncService(client, repository)
    options = OptionalFixtureSyncOptions(injuries=True)

    first = asyncio.run(service.sync_fixture(_fixture(), options=options, now=NOW))
    assert first.skipped == 1
    assert client.calls == []

    second = asyncio.run(
        service.sync_fixture(_fixture(), options=options, now=NOW + timedelta(hours=2))
    )
    assert second.injuries_downloaded == 1
    assert client.calls == [('injuries', 9001)]

    third = asyncio.run(
        service.sync_fixture(
            _fixture(kickoff_minutes=300),
            options=options,
            now=NOW + timedelta(hours=2, minutes=1),
        )
    )
    assert third.skipped == 1
    assert client.calls == [('injuries', 9001)]


def test_lineups_wait_for_ninety_minute_window_retry_and_stop_when_confirmed():
    client = FakeOptionalClient()
    repository = FakeOptionalRepository()
    service = OptionalFixtureSyncService(client, repository)
    options = OptionalFixtureSyncOptions(lineups=True)

    too_early = asyncio.run(
        service.sync_fixture(_fixture(kickoff_minutes=91), options=options, now=NOW)
    )
    assert too_early.skipped == 1
    assert client.calls == []

    fixture = _fixture(kickoff_minutes=90)
    unavailable = asyncio.run(service.sync_fixture(fixture, options=options, now=NOW))
    assert unavailable.lineups_downloaded == 1
    assert repository.status['lineups_next_retry_at'] == (
        NOW + timedelta(minutes=15)
    ).isoformat()

    before_retry = asyncio.run(
        service.sync_fixture(fixture, options=options, now=NOW + timedelta(minutes=14))
    )
    assert before_retry.skipped == 1
    assert client.calls == [('lineups', 9001)]

    client.lineups = [
        {
            'team': {'id': 10, 'name': 'Home'},
            'formation': '4-3-3',
            'startXI': [{'player': {'id': 101, 'name': 'Home Player'}}],
            'substitutes': [],
        },
        {
            'team': {'id': 20, 'name': 'Away'},
            'formation': '4-4-2',
            'startXI': [{'player': {'id': 201, 'name': 'Away Player'}}],
            'substitutes': [],
        },
    ]
    confirmed = asyncio.run(
        service.sync_fixture(fixture, options=options, now=NOW + timedelta(minutes=15))
    )
    assert confirmed.lineups_downloaded == 1
    assert repository.status['lineups_confirmed_at'] is not None
    assert repository.status['lineups_next_retry_at'] is None

    after_confirmation = asyncio.run(
        service.sync_fixture(fixture, options=options, now=NOW + timedelta(minutes=16))
    )
    assert after_confirmation.skipped == 1
    assert client.calls == [('lineups', 9001), ('lineups', 9001)]


class RecordingRepository(SupabaseRepository):
    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []
        self.status_changes: list[dict[str, Any]] = []

    async def _upsert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        on_conflict: str,
        select: str | None = None,
        ignore_duplicates: bool = False,
    ):
        self.upserts.append({
            'table': table,
            'rows': dict(rows) if isinstance(rows, Mapping) else [dict(row) for row in rows],
            'on_conflict': on_conflict,
            'ignore_duplicates': ignore_duplicates,
        })
        return []

    async def _id_map(self, table, rows, *, api_column):
        return {
            int(row[api_column]): int(row[api_column])
            for row in rows
            if row.get(api_column) is not None
        }

    async def _select(self, *_args, **_kwargs):
        return []

    async def update_optional_sync_status(self, fixture_id, changes):
        self.status_changes.append(dict(changes))


def test_snapshot_hashes_are_stable_and_conflicts_do_not_update_existing_rows():
    repository = RecordingRepository()
    payload = {'response': [{'fixture': {'id': 9001}, 'value': '1.90'}]}

    async def exercise():
        await repository.persist_odds_snapshot(9001, payload, fetched_at=NOW.isoformat())
        await repository.persist_odds_snapshot(
            9001,
            {'response': [{'value': '1.90', 'fixture': {'id': 9001}}]},
            fetched_at=(NOW + timedelta(minutes=5)).isoformat(),
        )

    asyncio.run(exercise())
    first, second = repository.upserts
    assert first['rows']['response_hash'] == second['rows']['response_hash']
    assert first['on_conflict'] == 'fixture_id,response_hash'
    assert first['ignore_duplicates'] is True
    assert second['ignore_duplicates'] is True


def test_duplicate_injury_items_are_collapsed_before_bulk_upsert():
    repository = RecordingRepository()
    injury = {
        'team': {'id': 10, 'name': 'Home'},
        'player': {'id': 101, 'name': 'Player', 'type': 'Missing Fixture'},
    }
    asyncio.run(
        repository.persist_injuries(
            9001,
            [injury, dict(injury)],
            fetched_at=NOW.isoformat(),
        )
    )
    injury_upsert = next(
        item for item in repository.upserts if item['table'] == 'fixture_injuries'
    )
    assert len(injury_upsert['rows']) == 1
    assert injury_upsert['on_conflict'] == 'fixture_id,source_key'
