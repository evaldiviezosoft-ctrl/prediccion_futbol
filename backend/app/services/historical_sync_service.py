from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import logging
from typing import Any, Mapping, Protocol, Sequence

from app.core.errors import ProviderAccessRestrictionError, ProviderRateLimitError
from app.services.fixture_normalizer import (
    FixtureNormalizationError,
    normalize_fixture,
)
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)
MAX_FIXTURE_IDS_PER_REQUEST = 20


class HistoricalApiClient(Protocol):
    rate_limit: Any

    async def fixtures(
        self,
        league: int,
        season: int,
        *,
        status: str | None = None,
        timezone_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def fixture_details(
        self,
        ids: Sequence[int],
        timezone_name: str | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class SyncSummary:
    competitions_processed: int = 0
    seasons_available: int = 0
    seasons_unavailable: int = 0
    fixtures_downloaded: int = 0
    fixtures_updated: int = 0
    details_complete: int = 0
    details_incomplete: int = 0
    optional_downloaded: int = 0
    optional_skipped: int = 0
    errors: int = 0
    requests_consumed: int | None = None
    requests_remaining: int | None = None
    stopped_safely: bool = False
    stop_reason: str | None = None
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chunks(values: Sequence[int], size: int = MAX_FIXTURE_IDS_PER_REQUEST) -> list[list[int]]:
    if not 1 <= size <= MAX_FIXTURE_IDS_PER_REQUEST:
        raise ValueError(f'Fixture detail batches must contain 1..{MAX_FIXTURE_IDS_PER_REQUEST} IDs.')
    return [list(values[index:index + size]) for index in range(0, len(values), size)]


def _is_rate_limit_error(exc: Exception) -> bool:
    return isinstance(exc, ProviderRateLimitError) or type(exc).__name__ in {
        'RateLimitExhaustedError',
        'ApiFootballRateLimitError',
        'RequestBudgetExhaustedError',
    }


def _is_access_restriction(exc: Exception) -> bool:
    return isinstance(exc, ProviderAccessRestrictionError) or type(exc).__name__ in {
        'ApiFootballAccessRestrictionError',
        'SeasonUnavailableError',
    }


def _snapshot(rate_limit: Any) -> dict[str, Any]:
    if rate_limit is None:
        return {}
    value = getattr(rate_limit, 'snapshot', {})
    if callable(value):
        value = value()
    if hasattr(value, 'model_dump'):
        return dict(value.model_dump(mode='json'))
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _update_rate_summary(summary: SyncSummary, client: HistoricalApiClient) -> None:
    snapshot = _snapshot(getattr(client, 'rate_limit', None))
    consumed = snapshot.get('requests_this_run', snapshot.get('run_requests'))
    remaining = snapshot.get('daily_remaining', snapshot.get('remaining_day'))
    summary.requests_consumed = int(consumed) if consumed is not None else summary.requests_consumed
    summary.requests_remaining = int(remaining) if remaining is not None else summary.requests_remaining


def _component_has_normalized_rows(detail: Any, component: str) -> bool:
    """Required backfills need usable normalized rows, not only a JSON key."""

    collections = {
        'statistics': getattr(detail, 'team_statistics', None),
        'players': getattr(detail, 'player_statistics', None),
        'lineups': getattr(detail, 'lineups', None),
        'events': getattr(detail, 'events', None),
    }
    if component in collections:
        return bool(collections[component])
    return bool(getattr(detail, 'components_present', {}).get(component, False))


class HistoricalSyncService:
    def __init__(
        self,
        client: HistoricalApiClient,
        repository: SupabaseRepository,
        *,
        timezone: str = 'America/Lima',
    ) -> None:
        self.client = client
        self.repository = repository
        self.timezone = timezone
        self._batch_detail_requests_supported: bool | None = None

    async def sync(
        self,
        *,
        from_season: int = 2021,
        to_season: int = 2026,
        competitions: Sequence[str] | None = None,
        include_details: bool = True,
    ) -> SyncSummary:
        if from_season > to_season:
            raise ValueError('from_season cannot be greater than to_season.')
        summary = SyncSummary()
        targets = await self.repository.list_enabled_competitions(competitions)
        if competitions:
            found = {str(row['internal_code']) for row in targets}
            missing = sorted(set(competitions) - found)
            if missing:
                raise ValueError(f'Unknown or disabled competitions: {", ".join(missing)}')

        # Newest data has explicit priority, independently of CLI argument order.
        seasons = list(range(to_season, from_season - 1, -1))
        for competition in targets:
            summary.competitions_processed += 1
            await self.repository.ensure_legacy_league(competition)
            season_rows = await self.repository.list_competition_seasons(
                int(competition['id']),
                from_season=from_season,
                to_season=to_season,
            )
            availability = {int(row['season']): row for row in season_rows}
            for season in seasons:
                known = availability.get(season, {})
                if known.get('availability_status') == 'unavailable':
                    summary.seasons_unavailable += 1
                    summary.messages.append(
                        f'[{competition["name"]}][{season}] no disponible para este plan'
                    )
                    continue
                stopped = await self._sync_season(
                    competition,
                    season,
                    known.get('coverage_json') or {},
                    summary,
                    include_details=include_details,
                )
                _update_rate_summary(summary, self.client)
                if stopped:
                    return summary
        return summary

    async def _sync_season(
        self,
        competition: Mapping[str, Any],
        season: int,
        coverage: Mapping[str, Any],
        summary: SyncSummary,
        *,
        include_details: bool,
    ) -> bool:
        competition_id = int(competition['id'])
        api_league_id = competition.get('api_league_id')
        if api_league_id is None:
            summary.errors += 1
            summary.messages.append(
                f'[{competition["name"]}][{season}] competición todavía no resuelta'
            )
            return False
        try:
            fixture_items = await self.client.fixtures(
                league=int(api_league_id),
                season=season,
                status='FT-AET-PEN',
                timezone_name=self.timezone,
            )
        except Exception as exc:
            if _is_rate_limit_error(exc):
                self._stop_for_limit(summary, exc)
                return True
            if _is_access_restriction(exc):
                await self.repository.mark_season_availability(
                    competition_id, season, 'unavailable', coverage=coverage
                )
                summary.seasons_unavailable += 1
                summary.messages.append(
                    f'[{competition["name"]}][{season}] no disponible para este plan'
                )
                return False
            summary.errors += 1
            logger.exception(
                'Historical fixture list failed for %s/%s', competition['internal_code'], season
            )
            summary.messages.append(
                f'[{competition["name"]}][{season}] error al descargar fixtures: {type(exc).__name__}'
            )
            return False

        await self.repository.mark_season_availability(
            competition_id, season, 'available', coverage=coverage
        )
        summary.seasons_available += 1
        normalized = []
        for item in fixture_items:
            try:
                normalized.append(normalize_fixture(item, competition_id=competition_id))
            except FixtureNormalizationError as exc:
                summary.errors += 1
                logger.warning('Skipped invalid fixture for %s/%s: %s', competition['internal_code'], season, exc)
        persisted = await self.repository.persist_fixtures_basic(
            normalized, competition=competition
        )
        summary.fixtures_downloaded += len(normalized)
        summary.fixtures_updated += sum(persisted.values())
        summary.messages.append(
            f'[{competition["name"]}][{season}] fixtures básicos: {len(normalized)} encontrados'
        )
        if not include_details or not normalized:
            return False

        completed = await self.repository.completed_fixture_ids(competition_id, season)
        pending_ids = [item.api_fixture_id for item in normalized if item.api_fixture_id not in completed]
        batches = _chunks(pending_ids)
        for batch_index, batch in enumerate(batches, start=1):
            stopped = await self._sync_detail_ids(
                competition=competition,
                fixture_ids=batch,
                seasons={fixture_id: season for fixture_id in batch},
                coverage_by_fixture={fixture_id: coverage for fixture_id in batch},
                summary=summary,
            )
            if stopped:
                return True
            summary.messages.append(
                f'[{competition["name"]}][{season}] lote {batch_index}/{len(batches)} guardado'
            )
            _update_rate_summary(summary, self.client)
        return False

    async def _sync_detail_ids(
        self,
        *,
        competition: Mapping[str, Any],
        fixture_ids: Sequence[int],
        seasons: Mapping[int, int],
        coverage_by_fixture: Mapping[int, Mapping[str, Any]],
        summary: SyncSummary,
        required_components: frozenset[str] = frozenset(),
        force_singular: bool = False,
    ) -> bool:
        """Persist requested details immediately, including a free-plan fallback.

        Some API-Football plans reject `ids=...` while allowing `id=...`. Once
        detected, the service switches to singular requests for the rest of the
        run. Each response is saved before the next request so a quota stop never
        discards already-downloaded payloads.
        """

        competition_id = int(competition['id'])
        queue = (
            [[fixture_id] for fixture_id in fixture_ids]
            if force_singular or self._batch_detail_requests_supported is False
            else [list(fixture_ids)]
        )
        while queue:
            requested_ids = queue.pop(0)
            try:
                detail_items = await self.client.fixture_details(
                    requested_ids,
                    timezone_name=self.timezone,
                )
                if len(requested_ids) > 1:
                    self._batch_detail_requests_supported = True
            except Exception as exc:
                if _is_access_restriction(exc) and len(requested_ids) > 1:
                    self._batch_detail_requests_supported = False
                    queue = [[fixture_id] for fixture_id in requested_ids] + queue
                    summary.messages.append(
                        'El plan no permite lotes ids; continuando con consultas id individuales.'
                    )
                    continue
                if _is_rate_limit_error(exc):
                    self._stop_for_limit(summary, exc)
                    return True
                summary.errors += 1
                summary.details_incomplete += len(requested_ids)
                for fixture_id in requested_ids:
                    await self.repository.mark_sync_error(
                        competition_id,
                        seasons[fixture_id],
                        fixture_id,
                        type(exc).__name__,
                    )
                logger.exception(
                    'Fixture detail request failed for %s.', competition['internal_code']
                )
                continue

            returned_ids: set[int] = set()
            for detail_item in detail_items:
                fixture_id = _fixture_id_or_none(detail_item)
                if fixture_id is None or fixture_id not in seasons:
                    summary.errors += 1
                    summary.details_incomplete += 1
                    continue
                returned_ids.add(fixture_id)
                try:
                    detail = normalize_fixture(detail_item, competition_id=competition_id)
                    complete = await self.repository.persist_fixture(
                        detail,
                        competition=competition,
                        details=True,
                        coverage=coverage_by_fixture.get(fixture_id, {}),
                    )
                    missing_required = sorted(
                        component
                        for component in required_components
                        if not _component_has_normalized_rows(detail, component)
                    )
                    if missing_required:
                        summary.details_incomplete += 1
                        for component in missing_required:
                            await self.repository.mark_sync_component_pending(
                                competition_id,
                                seasons[fixture_id],
                                fixture_id,
                                component,
                                'Required component has no normalized rows: '
                                + component,
                            )
                    elif required_components or complete:
                        summary.details_complete += 1
                    else:
                        summary.details_incomplete += 1
                except Exception as exc:
                    summary.errors += 1
                    summary.details_incomplete += 1
                    await self.repository.mark_sync_error(
                        competition_id,
                        seasons[fixture_id],
                        fixture_id,
                        type(exc).__name__,
                    )
                    logger.exception('Could not persist fixture detail payload.')

            for missing_id in set(requested_ids) - returned_ids:
                summary.details_incomplete += 1
                await self.repository.mark_sync_error(
                    competition_id,
                    seasons[missing_id],
                    missing_id,
                    'API-Football returned no detail for this fixture.',
                )
            _update_rate_summary(summary, self.client)
        return False

    async def resume_missing_details(
        self,
        *,
        competitions: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> SyncSummary:
        summary = SyncSummary()
        targets = await self.repository.list_enabled_competitions(competitions)
        target_map = {int(item['id']): item for item in targets}
        coverage_by_season: dict[tuple[int, int], Mapping[str, Any]] = {}
        for competition_id in target_map:
            for season_row in await self.repository.list_competition_seasons(competition_id):
                coverage_by_season[(competition_id, int(season_row['season']))] = (
                    season_row.get('coverage_json') or {}
                )
        pending = await self.repository.list_pending_fixture_details(
            competition_ids=list(target_map), limit=limit
        )
        summary.competitions_processed = len(target_map)
        for batch in _chunks_by_competition(pending):
            competition_id = int(batch[0]['competition_id'])
            competition = target_map.get(competition_id)
            if not competition:
                continue
            ids = [int(row['api_fixture_id']) for row in batch]
            seasons = {int(row['api_fixture_id']): int(row['season']) for row in batch}
            stopped = await self._sync_detail_ids(
                competition=competition,
                fixture_ids=ids,
                seasons=seasons,
                coverage_by_fixture={
                    fixture_id: coverage_by_season.get(
                        (competition_id, season), {}
                    )
                    for fixture_id, season in seasons.items()
                },
                summary=summary,
            )
            if stopped:
                break
        return summary

    async def backfill_market_statistics(
        self,
        *,
        competitions: Sequence[str],
        max_fixtures: int,
        max_attempts: int = 3,
        force_singular_details: bool = True,
    ) -> SyncSummary:
        """Fill stored fixture team statistics without re-downloading calendars.

        Selection is idempotent (`statistics_downloaded = false`), prioritizes
        recent fixtures involving current teams, and permanently avoids rows
        that reached the explicit attempt ceiling. The API client's independent
        `RateLimitManager` remains the authoritative per-run request cap.
        """

        if not competitions:
            raise ValueError('At least one competition is required.')
        if max_fixtures < 1:
            raise ValueError('max_fixtures must be positive.')
        if max_attempts < 1:
            raise ValueError('max_attempts must be positive.')

        summary = SyncSummary()
        targets = await self.repository.list_enabled_competitions(competitions)
        found = {str(row['internal_code']) for row in targets}
        missing = sorted(set(competitions) - found)
        if missing:
            raise ValueError(f'Unknown or disabled competitions: {", ".join(missing)}')
        target_map = {int(item['id']): item for item in targets}
        summary.competitions_processed = len(target_map)

        coverage_by_season: dict[tuple[int, int], Mapping[str, Any]] = {}
        for competition_id in target_map:
            for season_row in await self.repository.list_competition_seasons(competition_id):
                coverage_by_season[(competition_id, int(season_row['season']))] = (
                    season_row.get('coverage_json') or {}
                )
        pending = await self.repository.list_pending_market_fixture_details(
            competition_ids=list(target_map),
            limit=max_fixtures,
            max_attempts=max_attempts,
        )
        if not pending:
            summary.messages.append(
                'No hay fixtures finalizados pendientes de estadísticas dentro de los límites.'
            )
            return summary

        priority_count = sum(bool(row.get('priority_current_team')) for row in pending)
        summary.messages.append(
            f'Seleccionados {len(pending)} fixtures; {priority_count} involucran equipos actuales.'
        )
        for batch in _chunks_by_competition(pending):
            competition_id = int(batch[0]['competition_id'])
            competition = target_map.get(competition_id)
            if competition is None:
                continue
            fixture_ids = [int(row['api_fixture_id']) for row in batch]
            seasons = {
                int(row['api_fixture_id']): int(row['season']) for row in batch
            }
            stopped = await self._sync_detail_ids(
                competition=competition,
                fixture_ids=fixture_ids,
                seasons=seasons,
                coverage_by_fixture={
                    fixture_id: coverage_by_season.get(
                        (competition_id, season), {}
                    )
                    for fixture_id, season in seasons.items()
                },
                summary=summary,
                required_components=frozenset({'statistics'}),
                force_singular=force_singular_details,
            )
            _update_rate_summary(summary, self.client)
            if stopped:
                break
        return summary

    def _stop_for_limit(self, summary: SyncSummary, exc: Exception) -> None:
        summary.stopped_safely = True
        summary.stop_reason = str(exc) or 'API request budget exhausted.'
        summary.messages.append(
            'Proceso detenido de forma segura por límite de solicitudes; ejecuta nuevamente mañana.'
        )
        _update_rate_summary(summary, self.client)


def _fixture_id_or_none(item: Mapping[str, Any]) -> int | None:
    try:
        return int(item['fixture']['id'])
    except (KeyError, TypeError, ValueError):
        return None


def _chunks_by_competition(rows: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row['competition_id']), []).append(row)
    batches: list[list[Mapping[str, Any]]] = []
    for group in grouped.values():
        for index in range(0, len(group), MAX_FIXTURE_IDS_PER_REQUEST):
            batches.append(group[index:index + MAX_FIXTURE_IDS_PER_REQUEST])
    return batches
