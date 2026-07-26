from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

from app.services.fixture_normalizer import NormalizedFixture, normalize_fixture


INJURIES_REFRESH_INTERVAL = timedelta(hours=4)
LINEUPS_EARLIEST_WINDOW = timedelta(minutes=90)
LINEUPS_RETRY_INTERVAL = timedelta(minutes=15)


class OptionalFixtureApiClient(Protocol):
    async def fixture_injuries(self, fixture_id: int) -> list[dict[str, Any]]: ...

    async def fixture_odds(self, fixture_id: int) -> dict[str, Any]: ...

    async def fixture_external_prediction(self, fixture_id: int) -> dict[str, Any]: ...

    async def fixture_lineups(self, fixture_id: int) -> list[dict[str, Any]]: ...


class OptionalFixtureRepository(Protocol):
    async def optional_sync_status(self, fixture_id: int) -> dict[str, Any]: ...

    async def list_optional_fixture_candidates(
        self,
        *,
        starts_at: datetime,
        ends_at: datetime,
        fixture_ids: Sequence[int] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]: ...

    async def persist_injuries(
        self,
        fixture_id: int,
        injuries: Sequence[Mapping[str, Any]],
        *,
        fetched_at: str,
    ) -> None: ...

    async def persist_odds_snapshot(
        self,
        fixture_id: int,
        payload: Mapping[str, Any],
        *,
        fetched_at: str,
    ) -> None: ...

    async def persist_external_prediction(
        self,
        fixture_id: int,
        payload: Mapping[str, Any],
        *,
        fetched_at: str,
    ) -> None: ...

    async def persist_fixture_lineups(
        self,
        normalized: NormalizedFixture,
        *,
        fetched_at: str,
        next_retry_at: str | None,
        confirmed: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class OptionalFixtureSyncOptions:
    """Explicit opt-ins. The default performs no optional provider requests."""

    injuries: bool = False
    odds: bool = False
    external_predictions: bool = False
    lineups: bool = False

    @property
    def enabled(self) -> bool:
        return self.injuries or self.odds or self.external_predictions or self.lineups


# Backwards-compatible public name used by sync_upcoming.py.
OptionalUpcomingData = OptionalFixtureSyncOptions


@dataclass(slots=True)
class OptionalFixtureSyncResult:
    downloaded: int = 0
    skipped: int = 0
    injuries_downloaded: int = 0
    odds_snapshots_downloaded: int = 0
    external_predictions_downloaded: int = 0
    lineups_downloaded: int = 0
    confirmed_fixture_ids: list[int] = field(default_factory=list)

    def merge(self, other: OptionalFixtureSyncResult) -> None:
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.injuries_downloaded += other.injuries_downloaded
        self.odds_snapshots_downloaded += other.odds_snapshots_downloaded
        self.external_predictions_downloaded += other.external_predictions_downloaded
        self.lineups_downloaded += other.lineups_downloaded
        self.confirmed_fixture_ids.extend(other.confirmed_fixture_ids)


class OptionalFixtureSyncService:
    """Synchronize costly pre-match resources only when explicitly requested."""

    def __init__(
        self,
        client: OptionalFixtureApiClient,
        repository: OptionalFixtureRepository,
    ) -> None:
        self.client = client
        self.repository = repository

    async def sync_many(
        self,
        fixtures: Sequence[NormalizedFixture | Mapping[str, Any]],
        *,
        options: OptionalFixtureSyncOptions | None = None,
        now: datetime | None = None,
    ) -> OptionalFixtureSyncResult:
        result = OptionalFixtureSyncResult()
        selected = options or OptionalFixtureSyncOptions()
        if not selected.enabled:
            return result
        clock = _as_utc(now or datetime.now(timezone.utc))
        for fixture in fixtures:
            result.merge(
                await self.sync_fixture(fixture, options=selected, now=clock)
            )
        return result

    async def sync_eligible(
        self,
        *,
        options: OptionalFixtureSyncOptions,
        starts_at: datetime,
        ends_at: datetime,
        fixture_ids: Sequence[int] | None = None,
        limit: int = 1000,
        now: datetime | None = None,
    ) -> OptionalFixtureSyncResult:
        """Load eligible upcoming fixtures from Supabase, then apply opt-in rules."""

        if not options.enabled:
            return OptionalFixtureSyncResult()
        fixtures = await self.repository.list_optional_fixture_candidates(
            starts_at=starts_at,
            ends_at=ends_at,
            fixture_ids=fixture_ids,
            limit=limit,
        )
        return await self.sync_many(fixtures, options=options, now=now)

    async def sync_fixture(
        self,
        fixture: NormalizedFixture | Mapping[str, Any],
        *,
        options: OptionalFixtureSyncOptions,
        now: datetime | None = None,
    ) -> OptionalFixtureSyncResult:
        result = OptionalFixtureSyncResult()
        if not options.enabled:
            return result

        clock = _as_utc(now or datetime.now(timezone.utc))
        fetched_at = clock.isoformat()
        fixture_id = _fixture_id(fixture)
        status = await self.repository.optional_sync_status(fixture_id)

        if options.injuries:
            if _injuries_are_due(status, clock):
                injuries = await self.client.fixture_injuries(fixture_id)
                await self.repository.persist_injuries(
                    fixture_id,
                    injuries,
                    fetched_at=fetched_at,
                )
                result.downloaded += 1
                result.injuries_downloaded += 1
            else:
                result.skipped += 1

        if options.odds:
            snapshot = await self.client.fixture_odds(fixture_id)
            await self.repository.persist_odds_snapshot(
                fixture_id,
                snapshot,
                fetched_at=fetched_at,
            )
            result.downloaded += 1
            result.odds_snapshots_downloaded += 1

        if options.external_predictions:
            prediction = await self.client.fixture_external_prediction(fixture_id)
            await self.repository.persist_external_prediction(
                fixture_id,
                prediction,
                fetched_at=fetched_at,
            )
            result.downloaded += 1
            result.external_predictions_downloaded += 1

        if options.lineups:
            kickoff = _fixture_kickoff(fixture)
            confirmed_before = _timestamp(status.get('lineups_confirmed_at')) is not None
            retry_due = _is_due(status.get('lineups_next_retry_at'), clock)
            if confirmed_before or not _inside_lineup_window(kickoff, clock) or not retry_due:
                result.skipped += 1
            else:
                lineups = await self.client.fixture_lineups(fixture_id)
                normalized = _with_lineups(fixture, lineups)
                confirmed = len(normalized.lineups) >= 2 and all(
                    bool(row.get('confirmed')) for row in normalized.lineups
                )
                next_retry_at = (
                    None
                    if confirmed
                    else (clock + LINEUPS_RETRY_INTERVAL).isoformat()
                )
                await self.repository.persist_fixture_lineups(
                    normalized,
                    fetched_at=fetched_at,
                    next_retry_at=next_retry_at,
                    confirmed=confirmed,
                )
                result.downloaded += 1
                result.lineups_downloaded += 1
                if confirmed:
                    result.confirmed_fixture_ids.append(fixture_id)

        return result


def _fixture_row(fixture: NormalizedFixture | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(fixture, NormalizedFixture):
        return fixture.fixture
    return fixture


def _fixture_id(fixture: NormalizedFixture | Mapping[str, Any]) -> int:
    row = _fixture_row(fixture)
    raw_id = row.get('api_fixture_id', row.get('id'))
    try:
        fixture_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('Optional sync fixture is missing a valid API fixture id.') from exc
    if fixture_id < 1:
        raise ValueError('Optional sync fixture id must be positive.')
    return fixture_id


def _fixture_kickoff(
    fixture: NormalizedFixture | Mapping[str, Any],
) -> datetime | None:
    row = _fixture_row(fixture)
    return _timestamp(row.get('fixture_date_utc', row.get('kickoff')))


def _with_lineups(
    fixture: NormalizedFixture | Mapping[str, Any],
    lineups: Sequence[Mapping[str, Any]],
) -> NormalizedFixture:
    row = _fixture_row(fixture)
    raw = row.get('raw_json') or row.get('raw_payload')
    if not isinstance(raw, Mapping):
        raise ValueError('Optional lineup sync requires the fixture raw payload.')
    competition_id = row.get('competition_id')
    if competition_id is None:
        raise ValueError('Optional lineup sync requires competition_id.')
    payload = dict(raw)
    payload['lineups'] = [dict(lineup) for lineup in lineups]
    return normalize_fixture(payload, competition_id=int(competition_id))


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if value in (None, ''):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_due(value: Any, now: datetime) -> bool:
    parsed = _timestamp(value)
    return parsed is None or parsed <= now


def _injuries_are_due(status: Mapping[str, Any], now: datetime) -> bool:
    next_allowed = _timestamp(status.get('injuries_next_allowed_at'))
    if next_allowed is not None:
        return next_allowed <= now
    last_fetched = _timestamp(status.get('injuries_last_fetched_at'))
    return last_fetched is None or last_fetched + INJURIES_REFRESH_INTERVAL <= now


def _inside_lineup_window(kickoff: datetime | None, now: datetime) -> bool:
    if kickoff is None:
        return False
    remaining = kickoff - now
    return timedelta(0) <= remaining <= LINEUPS_EARLIEST_WINDOW


__all__ = [
    'INJURIES_REFRESH_INTERVAL',
    'LINEUPS_EARLIEST_WINDOW',
    'LINEUPS_RETRY_INTERVAL',
    'OptionalFixtureSyncOptions',
    'OptionalFixtureSyncResult',
    'OptionalFixtureSyncService',
    'OptionalUpcomingData',
]
