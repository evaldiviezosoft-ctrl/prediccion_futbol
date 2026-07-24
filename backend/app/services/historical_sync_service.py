from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import logging
from typing import Any, Mapping, Protocol, Sequence

from app.core.errors import ProviderAccessRestrictionError, ProviderRateLimitError
from app.services.baseline_model_service import BASELINE_FINAL_STATUSES
from app.services.fixture_normalizer import (
    FixtureNormalizationError,
    NormalizedFixture,
    normalize_fixture,
)
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)
MAX_FIXTURE_IDS_PER_REQUEST = 20
MAX_TEAM_BACKFILL_TARGETS = 25
MAX_TEAM_HISTORY_FIXTURES = 100
MAX_TEAM_DETAIL_FIXTURES = 500


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

    async def fixtures_for_team(
        self,
        team: int,
        *,
        last: int | None = None,
        season: int | None = None,
        status: str | None = None,
        timezone_name: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def team_by_id(self, team: int) -> dict[str, Any] | None: ...

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
    teams_requested: int = 0
    teams_processed: int = 0
    team_metadata_updated: int = 0
    fixtures_discovered: int = 0
    fixtures_deduplicated: int = 0
    fixtures_skipped: int = 0
    fixtures_downloaded: int = 0
    fixtures_updated: int = 0
    details_skipped_existing: int = 0
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

    async def backfill_team_history(
        self,
        *,
        team_ids: Sequence[int],
        max_fixtures_per_team: int = 20,
        max_detail_fixtures: int = 20,
        force_singular_details: bool = False,
        season: int | None = None,
    ) -> SyncSummary:
        """Seed missing club history without downloading complete leagues.

        Each unique team costs one bounded `/fixtures?team=...&last=...`
        request. Only completed competitive matches with a score are stored.
        A domestic league missing from the global catalog gets a disabled,
        deterministic `api_<id>` competition row; this permits normalized
        storage without enabling a future full-league download. Detail calls
        are restricted to recent fixtures that do not already have normalized
        team statistics. The aggregate response may contain player rows, but
        this method never calls a player-specific endpoint.
        """

        unique_team_ids = _validated_team_ids(team_ids)
        if not 1 <= max_fixtures_per_team <= MAX_TEAM_HISTORY_FIXTURES:
            raise ValueError(
                f'max_fixtures_per_team must be between 1 and '
                f'{MAX_TEAM_HISTORY_FIXTURES}.'
            )
        if not 0 <= max_detail_fixtures <= MAX_TEAM_DETAIL_FIXTURES:
            raise ValueError(
                f'max_detail_fixtures must be between 0 and '
                f'{MAX_TEAM_DETAIL_FIXTURES}.'
            )
        if season is not None and not 2000 <= season <= 2100:
            raise ValueError('season must be between 2000 and 2100.')

        summary = SyncSummary(teams_requested=len(unique_team_ids))
        competitions_by_league: dict[int, Mapping[str, Any]] = {}
        fixtures_by_id: dict[
            int, tuple[NormalizedFixture, Mapping[str, Any]]
        ] = {}
        processed_competition_ids: set[int] = set()
        for team_id in unique_team_ids:
            try:
                history_query: dict[str, Any] = {
                    'timezone_name': self.timezone,
                }
                if season is None:
                    history_query['last'] = max_fixtures_per_team
                else:
                    history_query['season'] = season
                fixture_items = await self.client.fixtures_for_team(
                    team_id,
                    **history_query,
                )
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    self._stop_for_limit(summary, exc)
                    return summary
                summary.errors += 1
                logger.exception('Team history request failed for team %s.', team_id)
                summary.messages.append(
                    f'[equipo {team_id}] no se pudo descargar el historial: '
                    f'{type(exc).__name__}'
                )
                continue

            summary.teams_processed += 1
            summary.fixtures_discovered += len(fixture_items)
            if season is not None:
                fixture_items = sorted(
                    fixture_items,
                    key=_raw_team_fixture_sort_key,
                    reverse=True,
                )
            grouped: dict[int, list[NormalizedFixture]] = {}
            competition_for_group: dict[int, Mapping[str, Any]] = {}
            selected_for_team = 0
            for item in fixture_items:
                if not _is_usable_raw_team_history_fixture(item, team_id):
                    summary.fixtures_skipped += 1
                    continue
                if season is not None and selected_for_team >= max_fixtures_per_team:
                    summary.fixtures_skipped += 1
                    continue
                league_id = _provider_league_id_or_none(item)
                league = item.get('league')
                if (
                    league_id is None
                    or not isinstance(league, Mapping)
                    or _is_noncompetitive_league(league)
                ):
                    summary.fixtures_skipped += 1
                    continue
                competition = competitions_by_league.get(league_id)
                if competition is None:
                    try:
                        competition = (
                            await self.repository.ensure_targeted_competition(
                                league
                            )
                        )
                    except Exception:
                        summary.errors += 1
                        summary.fixtures_skipped += 1
                        logger.exception(
                            'Could not resolve targeted competition %s.',
                            league_id,
                        )
                        continue
                    competitions_by_league[league_id] = competition
                try:
                    normalized = normalize_fixture(
                        item,
                        competition_id=int(competition['id']),
                    )
                except FixtureNormalizationError as exc:
                    summary.errors += 1
                    summary.fixtures_skipped += 1
                    logger.warning(
                        'Skipped invalid team fixture for team %s: %s',
                        team_id,
                        exc,
                    )
                    continue
                if not _is_usable_team_history_fixture(normalized, team_id):
                    summary.fixtures_skipped += 1
                    continue
                selected_for_team += 1
                fixture_id = normalized.api_fixture_id
                if fixture_id in fixtures_by_id:
                    summary.fixtures_deduplicated += 1
                    continue
                fixtures_by_id[fixture_id] = (normalized, competition)
                competition_id = int(competition['id'])
                grouped.setdefault(competition_id, []).append(normalized)
                competition_for_group[competition_id] = competition

            for competition_id, normalized_group in grouped.items():
                competition = competition_for_group[competition_id]
                persisted = await self.repository.persist_fixtures_basic(
                    normalized_group,
                    competition=competition,
                )
                summary.fixtures_downloaded += len(normalized_group)
                summary.fixtures_updated += sum(persisted.values())
                processed_competition_ids.add(competition_id)
            summary.competitions_processed = len(processed_competition_ids)
            summary.messages.append(
                f'[equipo {team_id}] {len(fixture_items)} encontrados; '
                f'{sum(len(group) for group in grouped.values())} elegibles nuevos.'
            )
            _update_rate_summary(summary, self.client)

        summary.competitions_processed = len(processed_competition_ids)
        if not fixtures_by_id:
            summary.messages.append(
                'No se encontraron resultados finales utilizables en ligas competitivas.'
            )
            return summary
        if max_detail_fixtures == 0:
            summary.messages.append(
                'Se guardaron fixtures básicos; el límite de detalles es cero.'
            )
            return summary

        completed_statistics = (
            await self.repository.fixture_ids_with_team_statistics(
                list(fixtures_by_id)
            )
        )
        summary.details_skipped_existing = len(
            set(fixtures_by_id) & completed_statistics
        )
        pending = [
            value
            for fixture_id, value in fixtures_by_id.items()
            if fixture_id not in completed_statistics
        ]
        pending.sort(
            key=lambda value: _team_fixture_sort_key(value[0]),
            reverse=True,
        )
        selected = pending[:max_detail_fixtures]
        if len(pending) > len(selected):
            summary.messages.append(
                f'Detalles acotados a {len(selected)} de {len(pending)} fixtures '
                'pendientes, priorizando los más recientes.'
            )
        if not selected:
            summary.messages.append(
                'Todos los fixtures elegibles ya tienen estadísticas de equipo.'
            )
            return summary

        target_map = {
            int(competition['id']): competition
            for _, competition in selected
        }
        coverage_by_season: dict[tuple[int, int], Mapping[str, Any]] = {}
        for competition_id in target_map:
            season_rows = await self.repository.list_competition_seasons(
                competition_id
            )
            for season_row in season_rows:
                coverage_by_season[
                    (competition_id, int(season_row['season']))
                ] = season_row.get('coverage_json') or {}

        detail_rows = [
            {
                'competition_id': int(competition['id']),
                'season': int(normalized.fixture['season']),
                'api_fixture_id': normalized.api_fixture_id,
            }
            for normalized, competition in selected
        ]
        for batch in _chunks_by_competition(detail_rows):
            competition_id = int(batch[0]['competition_id'])
            competition = target_map[competition_id]
            fixture_ids = [int(row['api_fixture_id']) for row in batch]
            seasons = {
                int(row['api_fixture_id']): int(row['season'])
                for row in batch
            }
            stopped = await self._sync_detail_ids(
                competition=competition,
                fixture_ids=fixture_ids,
                seasons=seasons,
                coverage_by_fixture={
                    fixture_id: coverage_by_season.get(
                        (competition_id, season),
                        {},
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

    async def backfill_team_metadata(
        self,
        *,
        team_ids: Sequence[int],
    ) -> SyncSummary:
        """Optionally enrich selected teams through one `/teams?id=...` call each."""

        unique_team_ids = _validated_team_ids(team_ids)
        summary = SyncSummary(teams_requested=len(unique_team_ids))
        for team_id in unique_team_ids:
            try:
                payload = await self.client.team_by_id(team_id)
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    self._stop_for_limit(summary, exc)
                    return summary
                summary.errors += 1
                logger.exception('Team metadata request failed for team %s.', team_id)
                summary.messages.append(
                    f'[equipo {team_id}] metadatos no disponibles: '
                    f'{type(exc).__name__}'
                )
                continue
            if payload is None:
                summary.errors += 1
                summary.messages.append(
                    f'[equipo {team_id}] API-Football no devolvió metadatos.'
                )
                continue
            try:
                await self.repository.persist_team_metadata(payload)
            except Exception as exc:
                summary.errors += 1
                logger.exception('Could not persist metadata for team %s.', team_id)
                summary.messages.append(
                    f'[equipo {team_id}] no se pudieron guardar los metadatos: '
                    f'{type(exc).__name__}'
                )
                continue
            summary.teams_processed += 1
            summary.team_metadata_updated += 1
            _update_rate_summary(summary, self.client)
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


def _validated_team_ids(team_ids: Sequence[int]) -> list[int]:
    unique_team_ids: list[int] = []
    for value in team_ids:
        try:
            team_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError('team_ids must contain positive integers.') from exc
        if team_id < 1:
            raise ValueError('team_ids must contain positive integers.')
        if team_id not in unique_team_ids:
            unique_team_ids.append(team_id)
    if not unique_team_ids:
        raise ValueError('At least one team_id is required.')
    if len(unique_team_ids) > MAX_TEAM_BACKFILL_TARGETS:
        raise ValueError(
            f'At most {MAX_TEAM_BACKFILL_TARGETS} unique team IDs may be processed.'
        )
    return unique_team_ids


def _provider_league_id_or_none(item: Mapping[str, Any]) -> int | None:
    try:
        return int(item['league']['id'])
    except (KeyError, TypeError, ValueError):
        return None


def _is_usable_raw_team_history_fixture(
    item: Mapping[str, Any],
    team_id: int,
) -> bool:
    try:
        status = str(item['fixture']['status']['short']).upper()
        home_team_id = int(item['teams']['home']['id'])
        away_team_id = int(item['teams']['away']['id'])
        home_goals = item['goals']['home']
        away_goals = item['goals']['away']
    except (KeyError, TypeError, ValueError):
        return False
    if status not in BASELINE_FINAL_STATUSES:
        return False
    if team_id not in {home_team_id, away_team_id}:
        return False
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (home_goals, away_goals)
    )


def _is_noncompetitive_league(league: Mapping[str, Any]) -> bool:
    name = str(league.get('name') or '').casefold()
    return 'friendl' in name or 'amistoso' in name


def _is_usable_team_history_fixture(
    normalized: NormalizedFixture,
    team_id: int,
) -> bool:
    row = normalized.fixture
    if str(row.get('status_short') or '').upper() not in BASELINE_FINAL_STATUSES:
        return False
    if team_id not in {
        int(row.get('home_team_id') or 0),
        int(row.get('away_team_id') or 0),
    }:
        return False
    goals = (row.get('home_goals'), row.get('away_goals'))
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in goals
    )


def _team_fixture_sort_key(normalized: NormalizedFixture) -> tuple[int, int]:
    try:
        timestamp = int(normalized.fixture.get('timestamp') or 0)
    except (TypeError, ValueError):
        timestamp = 0
    return timestamp, normalized.api_fixture_id


def _raw_team_fixture_sort_key(item: Mapping[str, Any]) -> tuple[int, int]:
    try:
        timestamp = int(item['fixture'].get('timestamp') or 0)
        fixture_id = int(item['fixture'].get('id') or 0)
    except (KeyError, TypeError, ValueError):
        return 0, 0
    return timestamp, fixture_id


def _chunks_by_competition(rows: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row['competition_id']), []).append(row)
    batches: list[list[Mapping[str, Any]]] = []
    for group in grouped.values():
        for index in range(0, len(group), MAX_FIXTURE_IDS_PER_REQUEST):
            batches.append(group[index:index + MAX_FIXTURE_IDS_PER_REQUEST])
    return batches
