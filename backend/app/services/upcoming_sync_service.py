from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from app.services.fixture_normalizer import (
    FixtureNormalizationError,
    UPCOMING_FIXTURE_STATUSES,
    normalize_fixture,
)
from app.services.historical_sync_service import (
    SyncSummary,
    _is_access_restriction,
    _is_rate_limit_error,
    _update_rate_summary,
)
from app.services.optional_fixture_sync_service import (
    OptionalFixtureSyncOptions,
    OptionalFixtureSyncService,
    OptionalUpcomingData,
)
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)


class UpcomingApiClient(Protocol):
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

    async def fixture_injuries(self, fixture_id: int) -> list[dict[str, Any]]: ...

    async def fixture_odds(self, fixture_id: int) -> dict[str, Any]: ...

    async def fixture_external_prediction(self, fixture_id: int) -> dict[str, Any]: ...

    async def fixture_lineups(self, fixture_id: int) -> list[dict[str, Any]]: ...

    async def fixtures_by_date(
        self,
        fixture_date: str,
        *,
        timezone_name: str | None = None,
    ) -> list[dict[str, Any]]: ...


class UpcomingSyncService:
    def __init__(
        self,
        client: UpcomingApiClient,
        repository: SupabaseRepository,
        *,
        timezone_name: str = 'America/Lima',
    ) -> None:
        self.client = client
        self.repository = repository
        self.timezone_name = timezone_name
        self.optional_sync = OptionalFixtureSyncService(client, repository)

    async def sync(
        self,
        *,
        days: int = 30,
        competitions: Sequence[str] | None = None,
        now: datetime | None = None,
        optional: OptionalFixtureSyncOptions | None = None,
    ) -> SyncSummary:
        if not 1 <= days <= 90:
            raise ValueError('days must be between 1 and 90.')
        local_zone = ZoneInfo(self.timezone_name)
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)
        date_from = clock.astimezone(local_zone).date()
        date_to = date_from + timedelta(days=days)
        summary = SyncSummary()
        optional = optional or OptionalUpcomingData()
        targets = await self.repository.list_enabled_competitions(competitions)
        if competitions:
            found = {str(row['internal_code']) for row in targets}
            missing = sorted(set(competitions) - found)
            if missing:
                raise ValueError(f'Unknown or disabled competitions: {", ".join(missing)}')

        contexts: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]] = []
        for competition in targets:
            seasons = await self.repository.list_competition_seasons(int(competition['id']))
            contexts.append((competition, _choose_current_season(seasons)))

        # Some free plans expose the current date window but reject season=2026.
        # One global date call is then both cheaper and more useful than ten
        # guaranteed season errors; results are filtered to configured leagues.
        if contexts and all(
            season_row is None or season_row.get('availability_status') == 'unavailable'
            for _competition, season_row in contexts
        ):
            summary.competitions_processed = len(contexts)
            await self._sync_by_calendar_date(
                targets=targets,
                date_from=date_from,
                date_to=date_to,
                clock=clock,
                optional=optional,
                summary=summary,
            )
            return summary

        for competition, season_row in contexts:
            summary.competitions_processed += 1
            if season_row is None:
                summary.errors += 1
                summary.messages.append(
                    f'[{competition["name"]}] no tiene una temporada resuelta para próximos partidos'
                )
                continue
            season = int(season_row['season'])
            if season_row.get('availability_status') == 'unavailable':
                summary.seasons_unavailable += 1
                continue
            api_league_id = competition.get('api_league_id')
            if api_league_id is None:
                summary.errors += 1
                continue
            try:
                items = await self.client.fixtures(
                    int(api_league_id),
                    season,
                    status='TBD-NS-PST',
                    timezone_name=self.timezone_name,
                    date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(),
                )
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    summary.stopped_safely = True
                    summary.stop_reason = str(exc) or 'API request budget exhausted.'
                    summary.messages.append(
                        'Proceso detenido de forma segura por límite de solicitudes; ejecuta nuevamente mañana.'
                    )
                    _update_rate_summary(summary, self.client)
                    break
                if _is_access_restriction(exc):
                    await self.repository.mark_season_availability(
                        int(competition['id']),
                        season,
                        'unavailable',
                        coverage=season_row.get('coverage_json') or {},
                    )
                    summary.seasons_unavailable += 1
                    summary.messages.append(
                        f'[{competition["name"]}][{season}] no disponible para este plan'
                    )
                    continue
                summary.errors += 1
                logger.exception('Upcoming fixture download failed for %s.', competition['internal_code'])
                continue

            normalized = []
            for item in items:
                try:
                    fixture = normalize_fixture(item, competition_id=int(competition['id']))
                except FixtureNormalizationError as exc:
                    summary.errors += 1
                    logger.warning('Skipped invalid upcoming fixture: %s', exc)
                    continue
                if fixture.fixture.get('status_short') not in UPCOMING_FIXTURE_STATUSES:
                    continue
                normalized.append(fixture)
            persisted = await self.repository.persist_fixtures_basic(
                normalized, competition=competition
            )
            await self.repository.mark_season_availability(
                int(competition['id']),
                season,
                'available',
                coverage=season_row.get('coverage_json') or {},
            )
            summary.seasons_available += 1
            summary.fixtures_downloaded += len(normalized)
            summary.fixtures_updated += sum(persisted.values())
            summary.messages.append(
                f'[{competition["name"]}][{season}] próximos partidos: {len(normalized)} encontrados'
            )
            if optional.enabled:
                try:
                    optional_result = await self.optional_sync.sync_many(
                        normalized,
                        options=optional,
                        now=clock.astimezone(timezone.utc),
                    )
                    summary.optional_downloaded += optional_result.downloaded
                    summary.optional_skipped += optional_result.skipped
                except Exception as exc:
                    if _is_rate_limit_error(exc):
                        summary.stopped_safely = True
                        summary.stop_reason = str(exc) or 'API request budget exhausted.'
                        summary.messages.append(
                            'Proceso detenido de forma segura por límite de solicitudes; ejecuta nuevamente mañana.'
                        )
                        _update_rate_summary(summary, self.client)
                        break
                    summary.errors += 1
                    logger.exception(
                        'Optional upcoming data failed for %s.', competition['internal_code']
                    )
            _update_rate_summary(summary, self.client)
        return summary

    async def _sync_by_calendar_date(
        self,
        *,
        targets: Sequence[Mapping[str, Any]],
        date_from: Any,
        date_to: Any,
        clock: datetime,
        optional: OptionalFixtureSyncOptions,
        summary: SyncSummary,
    ) -> None:
        competitions_by_league = {
            int(competition['api_league_id']): competition
            for competition in targets
            if competition.get('api_league_id') is not None
        }
        current_date = date_from
        while current_date <= date_to:
            try:
                items = await self.client.fixtures_by_date(
                    current_date.isoformat(),
                    timezone_name=self.timezone_name,
                )
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    summary.stopped_safely = True
                    summary.stop_reason = str(exc) or 'API request budget exhausted.'
                    summary.messages.append(
                        'Proceso detenido de forma segura por límite de solicitudes; '
                        'ejecuta nuevamente mañana.'
                    )
                    _update_rate_summary(summary, self.client)
                    return
                if _is_access_restriction(exc):
                    summary.messages.append(
                        f'Rango próximo truncado en {current_date.isoformat()} por el plan.'
                    )
                    _update_rate_summary(summary, self.client)
                    return
                summary.errors += 1
                logger.exception('Date-based upcoming fixture download failed.')
                return

            grouped: dict[int, list[Any]] = {}
            for item in items:
                try:
                    api_league_id = int(item['league']['id'])
                except (KeyError, TypeError, ValueError):
                    summary.errors += 1
                    continue
                competition = competitions_by_league.get(api_league_id)
                if competition is None:
                    continue
                try:
                    normalized = normalize_fixture(
                        item,
                        competition_id=int(competition['id']),
                    )
                except FixtureNormalizationError as exc:
                    summary.errors += 1
                    logger.warning('Skipped invalid date-based upcoming fixture: %s', exc)
                    continue
                if normalized.fixture.get('status_short') in UPCOMING_FIXTURE_STATUSES:
                    grouped.setdefault(api_league_id, []).append(normalized)

            for api_league_id, fixtures in grouped.items():
                competition = competitions_by_league[api_league_id]
                persisted = await self.repository.persist_fixtures_basic(
                    fixtures,
                    competition=competition,
                )
                summary.fixtures_downloaded += len(fixtures)
                summary.fixtures_updated += sum(persisted.values())
                summary.messages.append(
                    f'[{competition["name"]}][{current_date.isoformat()}] '
                    f'próximos partidos: {len(fixtures)} encontrados'
                )
                if optional.enabled:
                    try:
                        optional_result = await self.optional_sync.sync_many(
                            fixtures,
                            options=optional,
                            now=clock.astimezone(timezone.utc),
                        )
                        summary.optional_downloaded += optional_result.downloaded
                        summary.optional_skipped += optional_result.skipped
                    except Exception as exc:
                        if _is_rate_limit_error(exc):
                            summary.stopped_safely = True
                            summary.stop_reason = str(exc) or 'API request budget exhausted.'
                            summary.messages.append(
                                'Proceso detenido de forma segura por límite de solicitudes; '
                                'ejecuta nuevamente mañana.'
                            )
                            _update_rate_summary(summary, self.client)
                            return
                        summary.errors += 1
                        logger.exception(
                            'Optional upcoming data failed for %s.',
                            competition['internal_code'],
                        )
            _update_rate_summary(summary, self.client)
            current_date += timedelta(days=1)


def _choose_current_season(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if not rows:
        return None
    current = [row for row in rows if row.get('is_current')]
    candidates = current or [
        row for row in rows if row.get('availability_status') != 'unavailable'
    ]
    if not candidates:
        return max(rows, key=lambda row: int(row['season']))
    return max(candidates, key=lambda row: int(row['season']))
