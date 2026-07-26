from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from app.db.supabase_client import get_supabase
from app.services.fixture_normalizer import (
    FINAL_FIXTURE_STATUSES,
    NormalizedFixture,
    response_hash,
)


POSTGREST_PAGE_SIZE = 1000
POSTGREST_IN_FILTER_CHUNK_SIZE = 250
PLAYED_FIXTURE_STATUSES = frozenset({'FT', 'AET', 'PEN'})
SYNC_FROM_SEASON = 2021
SYNC_TO_SEASON = 2026


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, 'model_dump'):
        return dict(value.model_dump(mode='json'))
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f'Expected a mapping, dataclass, or Pydantic model; got {type(value).__name__}.')


def _response_rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, 'data', None)
    if data is None and isinstance(response, Mapping):
        data = response.get('data')
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, Mapping)]
    if isinstance(data, Mapping):
        return [dict(data)]
    return []


def should_apply_fixture_update(existing_status: Any, incoming_status: Any) -> bool:
    existing = str(existing_status or '').strip().upper()
    incoming = str(incoming_status or '').strip().upper()
    return not (existing in FINAL_FIXTURE_STATUSES and incoming not in FINAL_FIXTURE_STATUSES)


def _apply_cup_aggregate_scores(rows: Sequence[dict[str, Any]]) -> None:
    """Fill aggregate scores when both finished legs are present in one season batch."""

    by_tie: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get('competition_id'),
            row.get('season'),
            row.get('round'),
            row.get('home_team_id'),
            row.get('away_team_id'),
        )
        by_tie[key] = row

    for row in rows:
        reverse_key = (
            row.get('competition_id'),
            row.get('season'),
            row.get('round'),
            row.get('away_team_id'),
            row.get('home_team_id'),
        )
        reverse = by_tie.get(reverse_key)
        if reverse is None or reverse.get('api_fixture_id') == row.get('api_fixture_id'):
            continue
        if (
            str(row.get('status_short') or '').upper() not in FINAL_FIXTURE_STATUSES
            or str(reverse.get('status_short') or '').upper()
            not in FINAL_FIXTURE_STATUSES
        ):
            continue
        scores = (
            row.get('home_goals'),
            row.get('away_goals'),
            reverse.get('home_goals'),
            reverse.get('away_goals'),
        )
        if not all(isinstance(score, int) and score >= 0 for score in scores):
            continue
        home_goals, away_goals, reverse_home, reverse_away = scores
        row['aggregate_home'] = home_goals + reverse_away
        row['aggregate_away'] = away_goals + reverse_home
        if not row.get('leg'):
            row_date = str(row.get('fixture_date_utc') or '')
            reverse_date = str(reverse.get('fixture_date_utc') or '')
            if row_date and reverse_date and row_date != reverse_date:
                row['leg'] = 'first' if row_date < reverse_date else 'second'


class SupabaseRepository:
    """Async facade over the synchronous supabase-py query builder.

    Every database call is built and executed inside ``asyncio.to_thread`` so
    FastAPI's event loop is never blocked by the synchronous client.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client if client is not None else get_supabase()

    async def _upsert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        on_conflict: str,
        select: str | None = None,
        ignore_duplicates: bool = False,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] | list[dict[str, Any]]
        if isinstance(rows, Mapping):
            payload = dict(rows)
        else:
            payload = [dict(row) for row in rows]
            if not payload:
                return []

        def execute() -> list[dict[str, Any]]:
            query = self._client.table(table).upsert(
                payload,
                on_conflict=on_conflict,
                default_to_null=False,
                ignore_duplicates=ignore_duplicates,
            )
            if select:
                query = query.select(select)
            return _response_rows(query.execute())

        return await asyncio.to_thread(execute)

    async def _insert(
        self,
        table: str,
        rows: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        payload = dict(rows) if isinstance(rows, Mapping) else [dict(row) for row in rows]

        def execute() -> list[dict[str, Any]]:
            return _response_rows(self._client.table(table).insert(payload).execute())

        return await asyncio.to_thread(execute)

    async def _update(
        self,
        table: str,
        changes: Mapping[str, Any],
        *,
        equals: Mapping[str, Any],
        in_values: Mapping[str, Iterable[Any]] | None = None,
        select: str = '*',
    ) -> list[dict[str, Any]]:
        filters = {key: list(values) for key, values in (in_values or {}).items()}
        if any(not values for values in filters.values()):
            return []

        def execute() -> list[dict[str, Any]]:
            query = self._client.table(table).update(dict(changes))
            for column, value in equals.items():
                query = query.eq(column, value)
            for column, values in filters.items():
                query = query.in_(column, values)
            if select:
                query = query.select(select)
            return _response_rows(query.execute())

        return await asyncio.to_thread(execute)

    async def _select(
        self,
        table: str,
        *,
        columns: str = '*',
        equals: Mapping[str, Any] | None = None,
        in_values: Mapping[str, Iterable[Any]] | None = None,
        is_values: Mapping[str, Any] | None = None,
        gte_values: Mapping[str, Any] | None = None,
        lte_values: Mapping[str, Any] | None = None,
        lt_values: Mapping[str, Any] | None = None,
        order_by: tuple[str, bool] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        equals = dict(equals or {})
        in_values = {key: list(values) for key, values in (in_values or {}).items()}
        is_values = dict(is_values or {})
        gte_values = dict(gte_values or {})
        lte_values = dict(lte_values or {})
        lt_values = dict(lt_values or {})

        if any(not values for values in in_values.values()):
            return []

        def build_query() -> Any:
            query = self._client.table(table).select(columns)
            for column, value in equals.items():
                query = query.eq(column, value)
            for column, values in in_values.items():
                query = query.in_(column, values)
            for column, value in is_values.items():
                query = query.is_(column, value)
            for column, value in gte_values.items():
                query = query.gte(column, value)
            for column, value in lte_values.items():
                query = query.lte(column, value)
            for column, value in lt_values.items():
                query = query.lt(column, value)
            if order_by:
                query = query.order(order_by[0], desc=order_by[1])
            return query

        def execute() -> list[dict[str, Any]]:
            if limit is not None:
                return _response_rows(build_query().limit(limit).execute())

            rows: list[dict[str, Any]] = []
            offset = 0
            while True:
                query = build_query().range(
                    offset,
                    offset + POSTGREST_PAGE_SIZE - 1,
                )
                page = _response_rows(query.execute())
                rows.extend(page)
                if len(page) < POSTGREST_PAGE_SIZE:
                    return rows
                offset += len(page)

        return await asyncio.to_thread(execute)

    async def prediction_fixture(self, fixture_id: int) -> dict[str, Any] | None:
        rows = await self._select(
            'fixtures',
            columns=(
                'id,league_id,competition_id,season,kickoff,fixture_date_utc,'
                'status_short,home_team_id,away_team_id,home_team_ref_id,away_team_ref_id,'
                'home_team_name,away_team_name'
            ),
            equals={'id': int(fixture_id)},
            limit=1,
        )
        return rows[0] if rows else None

    async def historical_finished_fixtures_before(
        self,
        *,
        league_id: int,
        kickoff: str,
        statuses: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Read score-bearing history with a strict pre-kickoff database cutoff."""

        return await self._select(
            'fixtures',
            columns=(
                'id,league_id,season,kickoff,fixture_date_utc,status_short,'
                'home_team_id,away_team_id,home_goals,away_goals'
            ),
            equals={'league_id': int(league_id)},
            in_values={'status_short': tuple(sorted(statuses))},
            lt_values={'kickoff': kickoff},
            order_by=('kickoff', False),
        )

    async def historical_finished_fixtures_before_many(
        self,
        *,
        league_ids: Iterable[int],
        kickoff: str,
        statuses: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Read several leagues in one paginated, strictly pre-kickoff query."""

        return await self._select(
            'fixtures',
            columns=(
                'id,league_id,season,kickoff,fixture_date_utc,status_short,'
                'home_team_id,away_team_id,home_team_ref_id,away_team_ref_id,'
                'home_goals,away_goals'
            ),
            in_values={
                'league_id': tuple(sorted({int(value) for value in league_ids})),
                'status_short': tuple(sorted(statuses)),
            },
            lt_values={'kickoff': kickoff},
            order_by=('kickoff', False),
        )

    async def team_by_api_id(self, api_team_id: int) -> dict[str, Any] | None:
        if api_team_id < 1:
            raise ValueError('api_team_id must be positive')
        rows = await self._select(
            'teams',
            columns='id,api_team_id,name,code,country,founded,national,logo_url',
            equals={'api_team_id': int(api_team_id)},
            limit=1,
        )
        return rows[0] if rows else None

    async def historical_finished_fixtures_for_team(
        self,
        *,
        api_team_id: int,
        kickoff: str,
        statuses: Iterable[str] = PLAYED_FIXTURE_STATUSES,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read a club's stored history across configured and targeted leagues."""

        if api_team_id < 1:
            raise ValueError('api_team_id must be positive')
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be between 1 and 1000')
        columns = (
            'id,league_id,season,kickoff,fixture_date_utc,status_short,'
            'home_team_id,away_team_id,home_team_ref_id,away_team_ref_id,'
            'home_goals,away_goals'
        )
        common = {
            'columns': columns,
            'in_values': {'status_short': tuple(sorted(statuses))},
            'lt_values': {'kickoff': kickoff},
            'order_by': ('kickoff', True),
            'limit': int(limit),
        }
        # The Supabase client is synchronous underneath and reuses one httpx
        # connection pool. Concurrent ``to_thread`` reads against that shared
        # client can fail intermittently on Windows with WSAEWOULDBLOCK.
        home_rows = await self._select(
            'fixtures',
            equals={'home_team_id': int(api_team_id)},
            **common,
        )
        away_rows = await self._select(
            'fixtures',
            equals={'away_team_id': int(api_team_id)},
            **common,
        )
        merged = {
            int(row['id']): row
            for row in [*home_rows, *away_rows]
            if row.get('id') is not None
        }

        def kickoff_value(row: Mapping[str, Any]) -> float:
            value = row.get('kickoff') or row.get('fixture_date_utc')
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except (TypeError, ValueError):
                return 0.0

        return sorted(
            merged.values(),
            key=lambda row: (kickoff_value(row), int(row['id'])),
            reverse=True,
        )[:limit]

    async def team_statistics_for_fixtures(
        self,
        fixture_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        ids = sorted({int(value) for value in fixture_ids})
        rows: list[dict[str, Any]] = []
        for index in range(0, len(ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            rows.extend(await self._select(
                'fixture_team_statistics',
                columns='fixture_id,team_id,is_home,corners,total_shots,shots_on_goal',
                in_values={'fixture_id': ids[index:index + POSTGREST_IN_FILTER_CHUNK_SIZE]},
                order_by=('fixture_id', False),
            ))
        return rows

    async def player_statistics_for_fixtures(
        self,
        *,
        fixture_ids: Iterable[int],
        team_ids: Iterable[int],
    ) -> list[dict[str, Any]]:
        ids = sorted({int(value) for value in fixture_ids})
        teams = sorted({int(value) for value in team_ids})
        if not teams:
            return []
        rows: list[dict[str, Any]] = []
        for index in range(0, len(ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            rows.extend(await self._select(
                'fixture_player_statistics',
                columns=(
                    'fixture_id,player_id,team_id,starter,substitute,minutes,goals,assists'
                ),
                in_values={
                    'fixture_id': ids[index:index + POSTGREST_IN_FILTER_CHUNK_SIZE],
                    'team_id': teams,
                },
                order_by=('fixture_id', False),
            ))
        return rows

    async def players_by_ids(
        self,
        player_ids: Iterable[int],
    ) -> dict[int, dict[str, Any]]:
        ids = sorted({int(value) for value in player_ids})
        rows: list[dict[str, Any]] = []
        for index in range(0, len(ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            rows.extend(await self._select(
                'players',
                columns='id,api_player_id,name,photo_url',
                in_values={'id': ids[index:index + POSTGREST_IN_FILTER_CHUNK_SIZE]},
                order_by=('id', False),
            ))
        return {int(row['id']): row for row in rows}

    async def stored_upcoming_fixtures(
        self,
        *,
        league_ids: Iterable[int],
        start_kickoff: str,
        end_kickoff: str,
        statuses: Iterable[str],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._select(
            'fixtures',
            columns=(
                'id,league_id,kickoff,status_short,home_team_id,away_team_id,'
                'home_team_name,away_team_name'
            ),
            in_values={
                'league_id': tuple(sorted(int(value) for value in league_ids)),
                'status_short': tuple(sorted(statuses)),
            },
            gte_values={'kickoff': start_kickoff},
            lte_values={'kickoff': end_kickoff},
            order_by=('kickoff', False),
            limit=None if limit is None else int(limit),
        )

    async def published_prediction_fixture_ids(
        self,
        fixture_ids: Iterable[int],
    ) -> set[int]:
        """Return fixtures that already have a published prediction."""

        ids = sorted({int(value) for value in fixture_ids})
        published: set[int] = set()
        for index in range(0, len(ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            rows = await self._select(
                'predictions',
                columns='fixture_id',
                equals={'published': True},
                in_values={
                    'fixture_id': ids[
                        index:index + POSTGREST_IN_FILTER_CHUNK_SIZE
                    ],
                },
            )
            published.update(
                int(row['fixture_id'])
                for row in rows
                if row.get('fixture_id') is not None
            )
        return published

    async def published_prediction(
        self,
        fixture_id: int,
    ) -> dict[str, Any] | None:
        rows = await self._select(
            'predictions',
            columns=(
                'fixture_id,league_id,league_code,home_team_id,away_team_id,'
                'home_team_name,away_team_name,kickoff,stage,lineups_confirmed,'
                'home_win_probability,draw_probability,away_win_probability,'
                'over25_probability,btts_probability,expected,possible_scorers,'
                'model_metadata,features_snapshot,published,updated_at'
            ),
            equals={'fixture_id': int(fixture_id), 'published': True},
            limit=1,
        )
        return rows[0] if rows else None

    async def ai_calibration_source_rows(
        self,
        fixture_id: int,
        *,
        history_limit: int = 12,
    ) -> dict[str, Any] | None:
        """Load only evidence explicitly admitted to the AI calibration input.

        Raw provider payloads and headers are deliberately excluded here. Odds
        are the sole exception at rest, and the service immediately reduces
        their latest stored payload to a numeric market snapshot.
        """

        prediction = await self.published_prediction(fixture_id)
        if prediction is None:
            return None
        fixture = await self.prediction_fixture(fixture_id)
        if fixture is None:
            return None
        kickoff = str(prediction['kickoff'])
        histories: dict[str, list[dict[str, Any]]] = {}
        for side in ('home', 'away'):
            histories[side] = await self.historical_finished_fixtures_for_team(
                api_team_id=int(prediction[f'{side}_team_id']),
                kickoff=kickoff,
                limit=history_limit,
            )
        fixture_ids = {
            int(row['id'])
            for rows in histories.values()
            for row in rows
            if row.get('id') is not None
        }
        statistics = (
            await self.team_statistics_for_fixtures(fixture_ids)
            if fixture_ids
            else []
        )
        lineups = await self._select(
            'lineups',
            columns='id,fixture_id,team_id,formation,confirmed,fetched_at',
            equals={'fixture_id': int(fixture_id)},
        )
        lineup_ids = [
            int(row['id']) for row in lineups if row.get('id') is not None
        ]
        lineup_players = (
            await self._select(
                'lineup_players',
                columns=(
                    'lineup_id,lineup_order,api_player_id,player_name,number,'
                    'position,starter,substitute'
                ),
                in_values={'lineup_id': lineup_ids},
                order_by=('lineup_order', False),
            )
            if lineup_ids
            else []
        )
        internal_team_ids = {
            int(row['team_id'])
            for row in lineups
            if row.get('team_id') is not None
        }
        lineup_teams = (
            await self._select(
                'teams',
                columns='id,api_team_id,name,country',
                in_values={'id': internal_team_ids},
            )
            if internal_team_ids
            else []
        )
        injuries = await self._select(
            'fixture_injuries',
            columns=(
                'api_team_id,api_player_id,injury_type,reason,fetched_at,active'
            ),
            equals={'fixture_id': int(fixture_id), 'active': True},
        )
        odds = await self._select(
            'fixture_odds_snapshots',
            columns='fetched_at,raw_json',
            equals={'fixture_id': int(fixture_id)},
            order_by=('fetched_at', True),
            limit=1,
        )
        optional_status = await self.optional_sync_status(fixture_id)
        return {
            'prediction': prediction,
            'fixture': fixture,
            'histories': histories,
            'statistics': statistics,
            'lineups': lineups,
            'lineup_players': lineup_players,
            'lineup_teams': lineup_teams,
            'injuries': injuries,
            'odds': odds[0] if odds else None,
            'optional_status': optional_status,
        }

    async def latest_ai_calibration(
        self,
        fixture_id: int,
        *,
        input_hash: str | None = None,
    ) -> dict[str, Any] | None:
        equals: dict[str, Any] = {'fixture_id': int(fixture_id)}
        if input_hash is not None:
            equals['input_hash'] = input_hash
        rows = await self._select(
            'prediction_calibrations',
            equals=equals,
            order_by=('attempt_number', True),
            limit=1,
        )
        return rows[0] if rows else None

    async def published_ai_calibration(
        self,
        fixture_id: int,
    ) -> dict[str, Any] | None:
        rows = await self._select(
            'prediction_calibrations',
            equals={
                'fixture_id': int(fixture_id),
                'status': 'updated',
                'published': True,
            },
            order_by=('attempt_number', True),
            limit=1,
        )
        return rows[0] if rows else None

    async def insert_ai_calibration_attempt(
        self,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Insert an immutable attempt identity, replaying on idempotency key."""

        inserted = await self._upsert(
            'prediction_calibrations',
            row,
            on_conflict='idempotency_key',
            select='*',
            ignore_duplicates=True,
        )
        if inserted:
            return inserted[0]
        existing = await self._select(
            'prediction_calibrations',
            equals={'idempotency_key': str(row['idempotency_key'])},
            limit=1,
        )
        if not existing:
            raise RuntimeError('Could not create or recover AI calibration attempt.')
        return existing[0]

    async def claim_ai_calibration(
        self,
        calibration_id: str,
        *,
        started_at: str,
    ) -> dict[str, Any] | None:
        rows = await self._update(
            'prediction_calibrations',
            {
                'status': 'processing',
                'started_at': started_at,
                'retry_after': None,
                'safe_message': None,
                'reason_code': None,
                'safe_error_message': None,
            },
            equals={'id': calibration_id, 'status': 'pending'},
        )
        return rows[0] if rows else None

    async def update_ai_calibration(
        self,
        calibration_id: str,
        changes: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        rows = await self._update(
            'prediction_calibrations',
            changes,
            equals={'id': calibration_id},
        )
        return rows[0] if rows else None

    async def recover_stale_ai_calibration(
        self,
        calibration_id: str,
        *,
        started_at: str,
    ) -> dict[str, Any] | None:
        rows = await self._update(
            'prediction_calibrations',
            {
                'status': 'pending',
                'started_at': None,
                'completed_at': None,
                'generated_at': None,
                'retry_after': None,
                'reason_code': 'stale_processing_recovered',
                'safe_message': (
                    'La calibración interrumpida se reintentará automáticamente.'
                ),
                'safe_error_message': None,
            },
            equals={
                'id': calibration_id,
                'status': 'processing',
                'started_at': started_at,
            },
        )
        return rows[0] if rows else None

    async def publish_ai_calibration(
        self,
        calibration_id: str,
    ) -> dict[str, Any]:
        """Atomically replace the currently published attempt for a fixture."""

        def execute() -> list[dict[str, Any]]:
            response = self._client.rpc(
                'publish_prediction_calibration',
                {'p_calibration_id': calibration_id},
            ).execute()
            return _response_rows(response)

        rows = await asyncio.to_thread(execute)
        if not rows:
            raise RuntimeError('AI calibration publication returned no row.')
        return rows[0]

    async def ai_calibration_candidates(
        self,
        *,
        starts_at: str,
        ends_at: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        predictions = await self._select(
            'predictions',
            columns='fixture_id,kickoff,updated_at',
            equals={'published': True},
            gte_values={'kickoff': starts_at},
            lte_values={'kickoff': ends_at},
            order_by=('kickoff', False),
            limit=None,
        )
        fixture_ids = [
            int(row['fixture_id'])
            for row in predictions
            if row.get('fixture_id') is not None
        ]
        attempts: list[dict[str, Any]] = []
        for index in range(0, len(fixture_ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            attempts.extend(await self._select(
                'prediction_calibrations',
                columns=(
                    'fixture_id,attempt_number,status,retry_after,'
                    'base_prediction_updated_at'
                ),
                in_values={
                    'fixture_id': fixture_ids[
                        index:index + POSTGREST_IN_FILTER_CHUNK_SIZE
                    ]
                },
            ))
        latest: dict[int, dict[str, Any]] = {}
        for row in attempts:
            fixture_id = int(row['fixture_id'])
            if (
                fixture_id not in latest
                or int(row['attempt_number'])
                > int(latest[fixture_id]['attempt_number'])
            ):
                latest[fixture_id] = row
        now = datetime.now(timezone.utc)

        def retry_due(row: Mapping[str, Any]) -> bool:
            value = row.get('retry_after')
            if not value:
                return True
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            except (TypeError, ValueError):
                return True
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc) <= now

        never_calibrated = []
        refresh_candidates = []
        for prediction in predictions:
            fixture_id = int(prediction['fixture_id'])
            attempt = latest.get(fixture_id)
            if attempt is None:
                never_calibrated.append(prediction)
                continue
            if str(attempt.get('base_prediction_updated_at')) != str(
                prediction.get('updated_at')
            ):
                refresh_candidates.append(prediction)
                continue
            if attempt.get('status') == 'pending' and retry_due(attempt):
                refresh_candidates.append(prediction)
        return [
            *never_calibrated,
            *refresh_candidates,
        ][:int(limit)]

    async def ensure_legacy_league(self, competition: Mapping[str, Any]) -> None:
        api_league_id = competition.get('api_league_id')
        if api_league_id is None:
            return
        existing = await self._select(
            'leagues', columns='id,code', equals={'id': int(api_league_id)}, limit=1
        )
        legacy_codes = {39: 'E0', 61: 'F1', 78: 'D1', 135: 'I1', 140: 'SP1'}
        code = (
            str(existing[0]['code'])
            if existing
            else legacy_codes.get(
                int(api_league_id),
                str(competition.get('internal_code') or f'league_{api_league_id}'),
            )
        )
        await self._upsert(
            'leagues',
            {
                'id': int(api_league_id),
                'code': code,
                'name': str(competition.get('name') or competition.get('expected_name') or code),
                'country': str(competition.get('country') or 'International'),
                'enabled': bool(competition.get('enabled', True)),
            },
            on_conflict='id',
        )

    async def ensure_targeted_competition(
        self,
        league: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Resolve the minimal competition row carried by a fixture payload.

        Targeted team history often belongs to a domestic league that is not in
        the global synchronization catalog. Such rows are disabled by default:
        they can own normalized fixtures but cannot accidentally make a later
        generic sync download the complete league.
        """

        try:
            api_league_id = int(league['id'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Fixture league must include a positive API ID.') from exc
        if api_league_id < 1:
            raise ValueError('Fixture league must include a positive API ID.')
        name = str(league.get('name') or '').strip()
        if not name:
            raise ValueError('Fixture league must include a name.')

        existing = await self._select(
            'competitions',
            equals={'api_league_id': api_league_id},
            limit=1,
        )
        if existing:
            return existing[0]

        provider_type = str(league.get('type') or '').strip().lower()
        competition_type = (
            provider_type if provider_type in {'league', 'cup'} else 'league'
        )
        country = (
            str(league.get('country') or 'International').strip()
            or 'International'
        )
        row = {
            'api_league_id': api_league_id,
            'internal_code': f'api_{api_league_id}',
            'name': name,
            'country': country,
            'competition_type': competition_type,
            'logo_url': league.get('logo'),
            'enabled': False,
            'last_synced_at': _utc_now(),
        }
        rows = await self._upsert(
            'competitions',
            row,
            on_conflict='api_league_id',
            select='*',
        )
        if rows:
            return rows[0]
        selected = await self._select(
            'competitions',
            equals={'api_league_id': api_league_id},
            limit=1,
        )
        if not selected:
            raise RuntimeError(
                f'Targeted competition api_{api_league_id} was not returned after upsert.'
            )
        return selected[0]

    async def persist_team_metadata(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, int | None]:
        """Upsert optional `/teams?id=...` metadata without requiring a migration."""

        team = payload.get('team')
        if not isinstance(team, Mapping):
            raise ValueError('Team metadata payload has no team object.')
        try:
            api_team_id = int(team['id'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Team metadata payload has no positive team ID.') from exc
        name = str(team.get('name') or '').strip()
        if api_team_id < 1 or not name:
            raise ValueError('Team metadata payload has an invalid identity.')

        def optional_text(value: Any) -> str | None:
            normalized = str(value or '').strip()
            return normalized or None

        try:
            founded = int(team['founded']) if team.get('founded') is not None else None
        except (TypeError, ValueError):
            founded = None
        if founded is not None and not 1800 <= founded <= 2100:
            founded = None
        team_row = {
            key: value
            for key, value in {
                'api_team_id': api_team_id,
                'name': name,
                'code': optional_text(team.get('code')),
                'country': optional_text(team.get('country')),
                'founded': founded,
                'national': (
                    team.get('national')
                    if isinstance(team.get('national'), bool)
                    else None
                ),
                'logo_url': optional_text(team.get('logo')),
            }.items()
            if value is not None
        }
        stored_teams = await self._upsert(
            'teams',
            team_row,
            on_conflict='api_team_id',
            select='id',
        )

        api_venue_id: int | None = None
        venue = payload.get('venue')
        if isinstance(venue, Mapping) and venue.get('id') is not None:
            try:
                candidate_venue_id = int(venue['id'])
            except (TypeError, ValueError):
                candidate_venue_id = 0
            if candidate_venue_id > 0:
                api_venue_id = candidate_venue_id
                try:
                    capacity = (
                        int(venue['capacity'])
                        if venue.get('capacity') is not None
                        else None
                    )
                except (TypeError, ValueError):
                    capacity = None
                if capacity is not None and capacity < 0:
                    capacity = None
                venue_row = {
                    key: value
                    for key, value in {
                        'api_venue_id': api_venue_id,
                        'name': optional_text(venue.get('name')),
                        'city': optional_text(venue.get('city')),
                        'address': optional_text(venue.get('address')),
                        'capacity': capacity,
                        'surface': optional_text(venue.get('surface')),
                        'image_url': optional_text(venue.get('image')),
                    }.items()
                    if value is not None
                }
                await self._upsert(
                    'venues',
                    venue_row,
                    on_conflict='api_venue_id',
                )
        return {
            'api_team_id': api_team_id,
            'team_ref_id': (
                int(stored_teams[0]['id'])
                if stored_teams and stored_teams[0].get('id') is not None
                else None
            ),
            'api_venue_id': api_venue_id,
        }

    async def upsert_competition_resolution(self, resolution: Any) -> dict[str, Any]:
        data = _as_dict(resolution)
        api_league_id = data.get('api_league_id', data.get('resolved_api_league_id'))
        internal_code = data.get('internal_code')
        if not internal_code:
            raise ValueError('A competition resolution must include internal_code.')
        competition_row = {
            'api_league_id': int(api_league_id) if api_league_id is not None else None,
            'internal_code': str(internal_code),
            'name': str(data.get('name') or data.get('expected_name') or internal_code),
            'country': str(data.get('country') or 'International'),
            'competition_type': str(data.get('competition_type') or data.get('type') or 'league'),
            'logo_url': data.get('logo_url', data.get('logo')),
            'enabled': bool(data.get('enabled', True)),
            'last_synced_at': _utc_now(),
        }
        await self.ensure_legacy_league(competition_row)
        rows = await self._upsert(
            'competitions',
            competition_row,
            on_conflict='internal_code',
            select='*',
        )
        if rows:
            stored = rows[0]
        else:
            selected = await self._select(
                'competitions', equals={'internal_code': str(internal_code)}, limit=1
            )
            if not selected:
                raise RuntimeError(f'Competition {internal_code!r} was not returned after upsert.')
            stored = selected[0]

        provider_seasons: dict[int, dict[str, Any]] = {}
        seasons = data.get('seasons') or data.get('available_seasons') or []
        for season_value in seasons:
            season_data = (
                _as_dict(season_value)
                if not isinstance(season_value, int)
                else {'year': season_value}
            )
            year_value = season_data.get('season', season_data.get('year'))
            if year_value is None:
                continue
            year = int(year_value)
            if SYNC_FROM_SEASON <= year <= SYNC_TO_SEASON:
                provider_seasons[year] = season_data

        for year in range(SYNC_FROM_SEASON, SYNC_TO_SEASON + 1):
            season_data = provider_seasons.get(year)
            is_available = season_data is not None
            season_data = season_data or {}
            coverage = season_data.get('coverage') or season_data.get('coverage_json')
            await self._upsert(
                'competition_seasons',
                {
                    'competition_id': stored['id'],
                    'season': year,
                    'start_date': season_data.get('start_date', season_data.get('start')),
                    'end_date': season_data.get('end_date', season_data.get('end')),
                    'is_current': bool(season_data.get('is_current', season_data.get('current', False))),
                    'coverage_json': coverage or {},
                    'availability_status': season_data.get('availability_status')
                    or ('available' if is_available else 'unavailable'),
                    'last_synced_at': _utc_now(),
                },
                on_conflict='competition_id,season',
            )
        return stored

    async def list_enabled_competitions(
        self,
        internal_codes: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        filters = {'enabled': True}
        in_values = {'internal_code': internal_codes} if internal_codes else None
        return await self._select(
            'competitions',
            equals=filters,
            in_values=in_values,
            order_by=('internal_code', False),
        )

    async def list_competition_seasons(
        self,
        competition_id: int,
        *,
        from_season: int | None = None,
        to_season: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self._select(
            'competition_seasons',
            equals={'competition_id': competition_id},
            order_by=('season', True),
        )
        return [
            row
            for row in rows
            if (from_season is None or int(row['season']) >= from_season)
            and (to_season is None or int(row['season']) <= to_season)
        ]

    async def mark_season_availability(
        self,
        competition_id: int,
        season: int,
        status: str,
        *,
        coverage: Mapping[str, Any] | None = None,
    ) -> None:
        existing = await self._select(
            'competition_seasons',
            equals={'competition_id': competition_id, 'season': season},
            limit=1,
        )
        row = dict(existing[0]) if existing else {
            'competition_id': competition_id,
            'season': season,
            'is_current': False,
            'coverage_json': {},
        }
        for read_only in ('id', 'created_at', 'updated_at'):
            row.pop(read_only, None)
        row['availability_status'] = status
        row['last_synced_at'] = _utc_now()
        if coverage is not None:
            row['coverage_json'] = dict(coverage)
        await self._upsert(
            'competition_seasons', row, on_conflict='competition_id,season'
        )

    async def _id_map(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        api_column: str,
    ) -> dict[int, int]:
        if not rows:
            return {}
        deduplicated = {
            int(row[api_column]): {
                key: value for key, value in row.items() if value is not None
            }
            for row in rows
            if row.get(api_column) is not None
        }
        if not deduplicated:
            return {}
        returned = await self._upsert(
            table,
            list(deduplicated.values()),
            on_conflict=api_column,
            select=f'id,{api_column}',
        )
        if len(returned) < len(deduplicated):
            returned = await self._select(
                table,
                columns=f'id,{api_column}',
                in_values={api_column: list(deduplicated)},
            )
        return {
            int(row[api_column]): int(row['id'])
            for row in returned
            if row.get(api_column) is not None and row.get('id') is not None
        }

    async def _upsert_sync_status(
        self,
        competition_id: int,
        season: int,
        api_fixture_id: int,
        changes: Mapping[str, Any],
    ) -> None:
        key = {
            'competition_id': competition_id,
            'season': season,
            'api_fixture_id': api_fixture_id,
        }
        existing = await self._select('api_sync_status', equals=key, limit=1)
        row: dict[str, Any] = {
            **key,
            'fixture_basic_downloaded': False,
            'fixture_details_downloaded': False,
            'statistics_downloaded': False,
            'players_downloaded': False,
            'lineups_downloaded': False,
            'events_downloaded': False,
            'attempts': 0,
        }
        if existing:
            row.update(existing[0])
        for read_only in ('id', 'created_at', 'updated_at'):
            row.pop(read_only, None)
        row.update(changes)
        await self._upsert(
            'api_sync_status', row, on_conflict='competition_id,season,api_fixture_id'
        )

    async def mark_sync_error(
        self,
        competition_id: int,
        season: int,
        api_fixture_id: int,
        error: str,
        *,
        retry_after: str | None = None,
    ) -> None:
        existing = await self._select(
            'api_sync_status',
            equals={
                'competition_id': competition_id,
                'season': season,
                'api_fixture_id': api_fixture_id,
            },
            limit=1,
        )
        attempts = int(existing[0].get('attempts') or 0) + 1 if existing else 1
        await self._upsert_sync_status(
            competition_id,
            season,
            api_fixture_id,
            {
                'attempts': attempts,
                'last_attempt_at': _utc_now(),
                'last_error': error[:1000],
                'retry_after': retry_after,
            },
        )

    async def mark_sync_component_pending(
        self,
        competition_id: int,
        season: int,
        api_fixture_id: int,
        component: str,
        error: str,
        *,
        retry_after: str | None = None,
    ) -> None:
        """Reset one downloaded flag and record a bounded retry attempt."""

        component_columns = {
            'statistics': 'statistics_downloaded',
            'players': 'players_downloaded',
            'lineups': 'lineups_downloaded',
            'events': 'events_downloaded',
        }
        column = component_columns.get(str(component).strip().lower())
        if column is None:
            raise ValueError(f'Unsupported sync component: {component}')
        key = {
            'competition_id': int(competition_id),
            'season': int(season),
            'api_fixture_id': int(api_fixture_id),
        }
        existing = await self._select('api_sync_status', equals=key, limit=1)
        attempts = int(existing[0].get('attempts') or 0) + 1 if existing else 1
        await self._upsert_sync_status(
            key['competition_id'],
            key['season'],
            key['api_fixture_id'],
            {
                column: False,
                'fixture_details_downloaded': False,
                'completed_at': None,
                'attempts': attempts,
                'last_attempt_at': _utc_now(),
                'last_error': str(error)[:1000],
                'retry_after': retry_after,
            },
        )

    async def persist_fixture(
        self,
        normalized: NormalizedFixture,
        *,
        competition: Mapping[str, Any],
        details: bool,
        coverage: Mapping[str, Any] | None = None,
    ) -> bool:
        """Upsert a normalized fixture and return whether its row was updated."""
        persisted = await self.persist_fixtures_basic([normalized], competition=competition)
        should_update = persisted[normalized.api_fixture_id]
        team_ids = await self._id_map('teams', normalized.teams, api_column='api_team_id')
        if details and should_update:
            return await self._persist_fixture_details(
                normalized, team_ids=team_ids, coverage=coverage or {}
            )
        return should_update

    async def persist_fixtures_basic(
        self,
        fixtures: Sequence[NormalizedFixture],
        *,
        competition: Mapping[str, Any],
    ) -> dict[int, bool]:
        """Persist a basic fixture list in bounded database batches.

        The return value maps API fixture IDs to whether the incoming row was
        applied. A terminal existing fixture maps to ``False`` when an upcoming
        payload attempts to replace it.
        """

        if not fixtures:
            return {}
        await self.ensure_legacy_league(competition)
        team_rows = [team for item in fixtures for team in item.teams]
        venue_rows = [item.venue for item in fixtures if item.venue]
        team_ids = await self._id_map('teams', team_rows, api_column='api_team_id')
        venue_ids = await self._id_map('venues', venue_rows, api_column='api_venue_id')
        fixture_ids = [item.api_fixture_id for item in fixtures]
        existing_rows = await self._select(
            'fixtures',
            columns='*',
            in_values={'api_fixture_id': fixture_ids},
        )
        existing = {int(row['api_fixture_id']): row for row in existing_rows}
        result: dict[int, bool] = {}
        upserts: list[dict[str, Any]] = []
        sync_rows: list[dict[str, Any]] = []
        existing_sync_rows = await self._select(
            'api_sync_status',
            in_values={'api_fixture_id': fixture_ids},
        )
        existing_sync = {
            (
                int(row['competition_id']),
                int(row['season']),
                int(row['api_fixture_id']),
            ): row
            for row in existing_sync_rows
        }
        now = _utc_now()

        for normalized in fixtures:
            row = dict(normalized.fixture)
            home_api_id, away_api_id = int(row['home_team_id']), int(row['away_team_id'])
            if home_api_id not in team_ids or away_api_id not in team_ids:
                raise RuntimeError('Could not resolve both fixture teams in Supabase.')
            row['home_team_ref_id'] = team_ids[home_api_id]
            row['away_team_ref_id'] = team_ids[away_api_id]
            winner_api_id = row.get('winner_team_id')
            row['winner_team_ref_id'] = (
                team_ids.get(int(winner_api_id)) if winner_api_id is not None else None
            )
            api_venue_id = normalized.venue.get('api_venue_id') if normalized.venue else None
            row['venue_id'] = venue_ids.get(int(api_venue_id)) if api_venue_id else None
            old_status = str(existing.get(normalized.api_fixture_id, {}).get('status_short') or '').upper()
            incoming_status = str(row.get('status_short') or '').upper()
            should_update = should_apply_fixture_update(old_status, incoming_status)
            result[normalized.api_fixture_id] = should_update
            if should_update:
                old_row = existing.get(normalized.api_fixture_id, {})
                row = {
                    key: (old_row.get(key) if value is None and old_row.get(key) is not None else value)
                    for key, value in row.items()
                }
                upserts.append(row)

            key = (int(row['competition_id']), int(row['season']), normalized.api_fixture_id)
            status = {
                'competition_id': key[0],
                'season': key[1],
                'api_fixture_id': key[2],
                'fixture_basic_downloaded': False,
                'fixture_details_downloaded': False,
                'statistics_downloaded': False,
                'players_downloaded': False,
                'lineups_downloaded': False,
                'events_downloaded': False,
                'attempts': 0,
            }
            status.update(existing_sync.get(key, {}))
            for read_only in ('id', 'created_at', 'updated_at'):
                status.pop(read_only, None)
            status.update({
                'fixture_basic_downloaded': True,
                'last_attempt_at': now,
                'last_error': None,
                'response_hash': response_hash(row.get('raw_json') or row.get('raw_payload')),
            })
            sync_rows.append(status)

        if str(competition.get('competition_type') or '').lower() == 'cup':
            _apply_cup_aggregate_scores(upserts)

        for index in range(0, len(upserts), 100):
            await self._upsert(
                'fixtures', upserts[index:index + 100], on_conflict='api_fixture_id'
            )
        for index in range(0, len(sync_rows), 100):
            await self._upsert(
                'api_sync_status',
                sync_rows[index:index + 100],
                on_conflict='competition_id,season,api_fixture_id',
            )
        return result

    async def _persist_fixture_details(
        self,
        normalized: NormalizedFixture,
        *,
        team_ids: Mapping[int, int],
        coverage: Mapping[str, Any],
    ) -> bool:
        fixture_id = normalized.api_fixture_id
        if normalized.events:
            await self._upsert(
                'fixture_events', normalized.events, on_conflict='fixture_id,event_order'
            )

        team_stats = []
        for value in normalized.team_statistics:
            row = dict(value)
            api_team_id = int(row['team_id'])
            if api_team_id in team_ids:
                row['team_id'] = team_ids[api_team_id]
                team_stats.append(row)
        if team_stats:
            await self._upsert(
                'fixture_team_statistics',
                team_stats,
                on_conflict='fixture_id,team_id',
            )

        player_ids = await self._id_map(
            'players', normalized.players, api_column='api_player_id'
        )
        player_stats = []
        for value in normalized.player_statistics:
            row = dict(value)
            api_player_id, api_team_id = int(row['player_id']), int(row['team_id'])
            if api_player_id in player_ids and api_team_id in team_ids:
                row['player_id'] = player_ids[api_player_id]
                row['team_id'] = team_ids[api_team_id]
                player_stats.append(row)
        if player_stats:
            await self._upsert(
                'fixture_player_statistics',
                player_stats,
                on_conflict='fixture_id,player_id,team_id',
            )

        lineup_ids: dict[str, int] = {}
        for value in normalized.lineups:
            row = dict(value)
            lineup_key = str(row.pop('lineup_key'))
            api_team_id = int(row['team_id'])
            if api_team_id not in team_ids:
                continue
            row['team_id'] = team_ids[api_team_id]
            returned = await self._upsert(
                'lineups', row, on_conflict='fixture_id,team_id', select='id'
            )
            if returned:
                lineup_ids[lineup_key] = int(returned[0]['id'])
        lineup_players = []
        for value in normalized.lineup_players:
            row = dict(value)
            lineup_key = str(row.pop('lineup_key'))
            if lineup_key not in lineup_ids:
                continue
            api_player_id = int(row['player_id'])
            row['api_player_id'] = api_player_id
            row['lineup_id'] = lineup_ids[lineup_key]
            row['player_id'] = player_ids.get(api_player_id)
            lineup_players.append(row)
        if lineup_players:
            await self._upsert(
                'lineup_players', lineup_players, on_conflict='lineup_id,lineup_order'
            )

        present = normalized.components_present
        fixture_coverage = coverage.get('fixtures') if isinstance(coverage.get('fixtures'), Mapping) else {}
        coverage_keys = {
            'events': 'events',
            'statistics': 'statistics_fixtures',
            'lineups': 'lineups',
            'players': 'statistics_players',
        }
        missing = [name for name, available in present.items() if not available]
        missing_expected = [
            name
            for name in missing
            if fixture_coverage.get(coverage_keys[name]) is True
        ]
        complete = not missing_expected
        await self._upsert_sync_status(
            int(normalized.fixture['competition_id']),
            int(normalized.fixture['season']),
            fixture_id,
            {
                'fixture_details_downloaded': complete,
                'statistics_downloaded': bool(present.get('statistics')),
                'players_downloaded': bool(present.get('players')),
                'lineups_downloaded': bool(present.get('lineups')),
                'events_downloaded': bool(present.get('events')),
                'completed_at': _utc_now() if complete else None,
                'last_attempt_at': _utc_now(),
                'last_error': (
                    f'Components unavailable in provider response: {", ".join(missing)}'
                    if missing else None
                ),
                'response_hash': response_hash(
                    normalized.fixture.get('raw_json') or normalized.fixture.get('raw_payload')
                ),
            },
        )
        return complete

    async def completed_fixture_ids(
        self,
        competition_id: int,
        season: int,
    ) -> set[int]:
        rows = await self._select(
            'api_sync_status',
            columns='api_fixture_id',
            equals={
                'competition_id': competition_id,
                'season': season,
                'fixture_details_downloaded': True,
            },
        )
        return {int(row['api_fixture_id']) for row in rows}

    async def fixture_ids_with_team_statistics(
        self,
        fixture_ids: Sequence[int],
    ) -> set[int]:
        """Return fixtures that already have usable normalized team statistics.

        Reading the normalized table is deliberately stricter than trusting a
        provider payload flag: an empty ``statistics: []`` response does not
        suppress a future retry.
        """

        ids = sorted({int(value) for value in fixture_ids if int(value) > 0})
        completed: set[int] = set()
        for index in range(0, len(ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            rows = await self._select(
                'fixture_team_statistics',
                columns='fixture_id',
                in_values={
                    'fixture_id': ids[
                        index:index + POSTGREST_IN_FILTER_CHUNK_SIZE
                    ]
                },
            )
            completed.update(
                int(row['fixture_id'])
                for row in rows
                if row.get('fixture_id') is not None
            )
        return completed

    async def list_pending_fixture_details(
        self,
        *,
        competition_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._select(
            'api_sync_status',
            columns='competition_id,season,api_fixture_id,attempts,retry_after',
            equals={'fixture_basic_downloaded': True, 'fixture_details_downloaded': False},
            in_values={'competition_id': competition_ids} if competition_ids else None,
            # PostgreSQL ASC sorts non-null errors before nulls, so transient
            # failures are retried before untouched rows on the next resume.
            order_by=('last_error', False),
            limit=limit,
        )

    async def list_pending_market_fixture_details(
        self,
        *,
        competition_ids: Sequence[int],
        limit: int,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return missing team-stat detail work in a deterministic safe order.

        Rows with downloaded statistics are idempotently excluded. Candidates
        involving teams seen during the last 370 days are ranked first, then by
        newest kickoff. Retry deferrals and the per-fixture attempt ceiling are
        enforced before the caller can spend a provider request.
        """

        ids = sorted({int(value) for value in competition_ids})
        if not ids:
            return []
        if limit < 1:
            raise ValueError('limit must be positive')
        if max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)
        clock = clock.astimezone(timezone.utc)

        statuses = await self._select(
            'api_sync_status',
            columns=(
                'competition_id,season,api_fixture_id,attempts,last_attempt_at,'
                'last_error,retry_after,statistics_downloaded'
            ),
            equals={
                'fixture_basic_downloaded': True,
                'statistics_downloaded': False,
            },
            in_values={'competition_id': ids},
            order_by=('api_fixture_id', True),
        )

        eligible_statuses: list[dict[str, Any]] = []
        for row in statuses:
            if int(row.get('attempts') or 0) >= max_attempts:
                continue
            retry_after = row.get('retry_after')
            if retry_after:
                try:
                    parsed_retry = datetime.fromisoformat(
                        str(retry_after).replace('Z', '+00:00')
                    )
                    if parsed_retry.tzinfo is None:
                        parsed_retry = parsed_retry.replace(tzinfo=timezone.utc)
                    if parsed_retry.astimezone(timezone.utc) > clock:
                        continue
                except (TypeError, ValueError):
                    # Invalid retry metadata must not permanently hide work.
                    pass
            eligible_statuses.append(dict(row))
        if not eligible_statuses:
            return []

        fixture_ids = sorted({int(row['api_fixture_id']) for row in eligible_statuses})
        fixture_rows: list[dict[str, Any]] = []
        for index in range(0, len(fixture_ids), POSTGREST_IN_FILTER_CHUNK_SIZE):
            fixture_rows.extend(await self._select(
                'fixtures',
                columns=(
                    'id,api_fixture_id,competition_id,season,fixture_date_utc,kickoff,'
                    'status_short,home_team_id,away_team_id'
                ),
                in_values={
                    'id': fixture_ids[index:index + POSTGREST_IN_FILTER_CHUNK_SIZE]
                },
                order_by=('id', True),
            ))

        recent_rows = await self._select(
            'fixtures',
            columns='competition_id,home_team_id,away_team_id,fixture_date_utc',
            in_values={'competition_id': ids},
            gte_values={
                'fixture_date_utc': (clock - timedelta(days=370)).isoformat()
            },
            order_by=('fixture_date_utc', True),
        )
        current_team_ids = {
            int(team_id)
            for row in recent_rows
            for team_id in (row.get('home_team_id'), row.get('away_team_id'))
            if team_id is not None
        }
        fixtures_by_id = {
            int(row.get('api_fixture_id') or row['id']): dict(row)
            for row in fixture_rows
        }

        candidates: list[dict[str, Any]] = []
        for status in eligible_statuses:
            fixture_id = int(status['api_fixture_id'])
            fixture = fixtures_by_id.get(fixture_id)
            if not fixture:
                continue
            if str(fixture.get('status_short') or '').upper() not in PLAYED_FIXTURE_STATUSES:
                continue
            home_team_id = fixture.get('home_team_id')
            away_team_id = fixture.get('away_team_id')
            priority_current_team = any(
                team_id is not None and int(team_id) in current_team_ids
                for team_id in (home_team_id, away_team_id)
            )
            value = {**status, **fixture}
            value['api_fixture_id'] = fixture_id
            value['priority_current_team'] = priority_current_team
            candidates.append(value)

        def kickoff_timestamp(row: Mapping[str, Any]) -> float:
            value = row.get('fixture_date_utc') or row.get('kickoff')
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except (TypeError, ValueError):
                return 0.0

        candidates.sort(key=lambda row: (
            not bool(row['priority_current_team']),
            -int(row.get('season') or 0),
            -kickoff_timestamp(row),
            -int(row['api_fixture_id']),
            int(row.get('attempts') or 0),
        ))
        return candidates[:limit]

    async def get_competitions_by_ids(
        self, competition_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        rows = await self._select(
            'competitions', in_values={'id': competition_ids}
        )
        return {int(row['id']): row for row in rows}

    async def log_api_request(self, record: Any) -> None:
        safe = _as_dict(record)
        safe.pop('api_key', None)
        safe.pop('x-apisports-key', None)
        await self._insert('api_request_logs', safe)

    async def list_api_request_logs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError('limit must be between 1 and 500')
        return await self._select(
            'api_request_logs',
            columns=(
                'id,endpoint,parameters_json,requested_at,response_status,results_count,'
                'daily_limit,daily_remaining,minute_limit,minute_remaining,duration_ms,'
                'error_message,request_hash'
            ),
            order_by=('requested_at', True),
            limit=limit,
        )

    async def latest_api_rate_limit(self) -> dict[str, Any] | None:
        rows = await self._select(
            'api_request_logs',
            columns=(
                'requested_at,daily_limit,daily_remaining,minute_limit,minute_remaining'
            ),
            order_by=('requested_at', True),
            limit=100,
        )
        return next(
            (row for row in rows if row.get('daily_remaining') is not None),
            None,
        )

    async def sync_progress(self) -> dict[str, Any]:
        statuses = await self._select(
            'api_sync_status',
            gte_values={'season': SYNC_FROM_SEASON},
            lte_values={'season': SYNC_TO_SEASON},
            order_by=('id', False),
        )
        seasons = await self._select(
            'competition_seasons',
            gte_values={'season': SYNC_FROM_SEASON},
            lte_values={'season': SYNC_TO_SEASON},
            order_by=('id', False),
        )
        logs = await self._select(
            'api_request_logs',
            order_by=('requested_at', True),
            limit=1,
        )
        complete = sum(bool(row.get('fixture_details_downloaded')) for row in statuses)
        statistics_complete = sum(bool(row.get('statistics_downloaded')) for row in statuses)
        unavailable = sum(row.get('availability_status') == 'unavailable' for row in seasons)
        latest = logs[0] if logs else {}
        return {
            'seasons_total': len(seasons),
            'seasons_available': sum(
                row.get('availability_status') == 'available' for row in seasons
            ),
            'seasons_unavailable': unavailable,
            'fixtures_basic_downloaded': sum(
                bool(row.get('fixture_basic_downloaded')) for row in statuses
            ),
            'fixtures_details_downloaded': complete,
            'fixtures_details_pending': len(statuses) - complete,
            'statistics_complete': statistics_complete,
            'statistics_incomplete': len(statuses) - statistics_complete,
            'errors': sum(bool(row.get('last_error')) for row in statuses),
            'daily_limit': latest.get('daily_limit'),
            'daily_remaining': latest.get('daily_remaining'),
            'last_request_at': latest.get('requested_at'),
        }

    async def optional_sync_status(self, fixture_id: int) -> dict[str, Any]:
        rows = await self._select(
            'fixture_optional_sync_status', equals={'fixture_id': fixture_id}, limit=1
        )
        return rows[0] if rows else {'fixture_id': fixture_id}

    async def list_optional_fixture_candidates(
        self,
        *,
        starts_at: datetime,
        ends_at: datetime,
        fixture_ids: Sequence[int] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List scheduled fixtures eligible for an explicit optional-data run."""

        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=timezone.utc)
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        starts_at = starts_at.astimezone(timezone.utc)
        ends_at = ends_at.astimezone(timezone.utc)
        if ends_at < starts_at:
            raise ValueError('ends_at must not be before starts_at.')
        if not 1 <= limit <= 10_000:
            raise ValueError('limit must be between 1 and 10000.')

        filters: dict[str, Iterable[Any]] = {
            'status_short': ('NS', 'TBD', 'PST'),
        }
        if fixture_ids is not None:
            ids = list(dict.fromkeys(int(value) for value in fixture_ids))
            if any(value < 1 for value in ids):
                raise ValueError('fixture_ids must be positive.')
            filters['id'] = ids
        return await self._select(
            'fixtures',
            columns=(
                'id,api_fixture_id,competition_id,season,fixture_date_utc,kickoff,'
                'status_short,raw_json,raw_payload'
            ),
            in_values=filters,
            gte_values={'fixture_date_utc': starts_at.isoformat()},
            lte_values={'fixture_date_utc': ends_at.isoformat()},
            order_by=('fixture_date_utc', False),
            limit=limit,
        )

    async def update_optional_sync_status(
        self,
        fixture_id: int,
        changes: Mapping[str, Any],
    ) -> None:
        row = await self.optional_sync_status(fixture_id)
        for read_only in ('created_at', 'updated_at'):
            row.pop(read_only, None)
        row.update(changes)
        row['fixture_id'] = fixture_id
        await self._upsert(
            'fixture_optional_sync_status', row, on_conflict='fixture_id'
        )

    async def persist_injuries(
        self,
        fixture_id: int,
        injuries: Sequence[Mapping[str, Any]],
        *,
        fetched_at: str,
    ) -> None:
        team_rows: list[dict[str, Any]] = []
        player_rows: list[dict[str, Any]] = []
        for item in injuries:
            team = item.get('team') if isinstance(item.get('team'), Mapping) else {}
            player = item.get('player') if isinstance(item.get('player'), Mapping) else {}
            if team.get('id') is not None and team.get('name'):
                team_rows.append({
                    'api_team_id': int(team['id']),
                    'name': str(team['name']),
                    'logo_url': team.get('logo'),
                })
            if player.get('id') is not None and player.get('name'):
                player_rows.append({
                    'api_player_id': int(player['id']),
                    'name': str(player['name']),
                    'photo_url': player.get('photo'),
                })
        team_ids = await self._id_map('teams', team_rows, api_column='api_team_id')
        player_ids = await self._id_map('players', player_rows, api_column='api_player_id')
        rows_by_source_key: dict[str, dict[str, Any]] = {}
        for item in injuries:
            team = item.get('team') if isinstance(item.get('team'), Mapping) else {}
            player = item.get('player') if isinstance(item.get('player'), Mapping) else {}
            api_team_id = int(team['id']) if team.get('id') is not None else None
            api_player_id = int(player['id']) if player.get('id') is not None else None
            injury_type = player.get('type') or item.get('type')
            reason = player.get('reason') or item.get('reason')
            identity = json.dumps(
                [
                    fixture_id,
                    api_team_id,
                    api_player_id,
                    None if api_player_id is not None else injury_type,
                    None if api_player_id is not None else reason,
                ],
                separators=(',', ':'),
                ensure_ascii=False,
            )
            source_key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
            rows_by_source_key[source_key] = {
                'fixture_id': fixture_id,
                'source_key': source_key,
                'team_id': team_ids.get(api_team_id) if api_team_id is not None else None,
                'player_id': player_ids.get(api_player_id) if api_player_id is not None else None,
                'api_team_id': api_team_id,
                'api_player_id': api_player_id,
                'injury_type': injury_type,
                'reason': reason,
                'fetched_at': fetched_at,
                'raw_json': dict(item),
            }
        current_source_keys = set(rows_by_source_key)
        existing_rows = await self._select(
            'fixture_injuries',
            columns='fixture_id,source_key,active',
            equals={'fixture_id': fixture_id},
        )
        stale_rows = [
            {
                'fixture_id': fixture_id,
                'source_key': str(row['source_key']),
                'active': False,
            }
            for row in existing_rows
            if row.get('source_key') not in current_source_keys
            and bool(row.get('active', True))
        ]
        if stale_rows:
            await self._upsert(
                'fixture_injuries',
                stale_rows,
                on_conflict='fixture_id,source_key',
            )

        rows = [dict(row, active=True) for row in rows_by_source_key.values()]
        if rows:
            await self._upsert(
                'fixture_injuries', rows, on_conflict='fixture_id,source_key'
            )
        await self.update_optional_sync_status(
            fixture_id,
            {
                'injuries_last_fetched_at': fetched_at,
                'injuries_next_allowed_at': _iso_after(fetched_at, hours=4),
            },
        )

    async def persist_odds_snapshot(
        self,
        fixture_id: int,
        payload: Mapping[str, Any],
        *,
        fetched_at: str,
    ) -> None:
        digest = response_hash(payload)
        await self._upsert(
            'fixture_odds_snapshots',
            {
                'fixture_id': fixture_id,
                'fetched_at': fetched_at,
                'response_hash': digest,
                'raw_json': dict(payload),
            },
            on_conflict='fixture_id,response_hash',
            ignore_duplicates=True,
        )
        await self.update_optional_sync_status(
            fixture_id, {'odds_last_fetched_at': fetched_at}
        )

    async def persist_external_prediction(
        self,
        fixture_id: int,
        payload: Mapping[str, Any],
        *,
        fetched_at: str,
    ) -> None:
        digest = response_hash(payload)
        await self._upsert(
            'fixture_external_predictions',
            {
                'fixture_id': fixture_id,
                'provider': 'api_football',
                'fetched_at': fetched_at,
                'response_hash': digest,
                'raw_json': dict(payload),
            },
            on_conflict='fixture_id,provider,response_hash',
            ignore_duplicates=True,
        )
        await self.update_optional_sync_status(
            fixture_id, {'external_prediction_last_fetched_at': fetched_at}
        )

    async def persist_fixture_lineups(
        self,
        normalized: NormalizedFixture,
        *,
        fetched_at: str,
        next_retry_at: str | None,
        confirmed: bool,
    ) -> None:
        team_ids = await self._id_map('teams', normalized.teams, api_column='api_team_id')
        player_ids = await self._id_map('players', normalized.players, api_column='api_player_id')
        await self._persist_lineup_rows(
            normalized,
            team_ids=team_ids,
            player_ids=player_ids,
            fetched_at=fetched_at,
        )
        await self.update_optional_sync_status(
            normalized.api_fixture_id,
            {
                'lineups_last_fetched_at': fetched_at,
                'lineups_next_retry_at': None if confirmed else next_retry_at,
                'lineups_confirmed_at': fetched_at if confirmed else None,
            },
        )
        await self._upsert_sync_status(
            int(normalized.fixture['competition_id']),
            int(normalized.fixture['season']),
            normalized.api_fixture_id,
            {
                'lineups_downloaded': bool(normalized.lineups),
                'lineups_next_retry_at': None if confirmed else next_retry_at,
            },
        )

    async def _persist_lineup_rows(
        self,
        normalized: NormalizedFixture,
        *,
        team_ids: Mapping[int, int],
        player_ids: Mapping[int, int],
        fetched_at: str | None = None,
    ) -> None:
        lineup_ids: dict[str, int] = {}
        for value in normalized.lineups:
            row = dict(value)
            lineup_key = str(row.pop('lineup_key'))
            api_team_id = int(row['team_id'])
            if api_team_id not in team_ids:
                continue
            row['team_id'] = team_ids[api_team_id]
            if fetched_at is not None:
                row['fetched_at'] = fetched_at
            returned = await self._upsert(
                'lineups', row, on_conflict='fixture_id,team_id', select='id'
            )
            if not returned:
                returned = await self._select(
                    'lineups',
                    columns='id',
                    equals={'fixture_id': normalized.api_fixture_id, 'team_id': row['team_id']},
                    limit=1,
                )
            if returned:
                lineup_ids[lineup_key] = int(returned[0]['id'])
        lineup_players = []
        for value in normalized.lineup_players:
            row = dict(value)
            lineup_key = str(row.pop('lineup_key'))
            if lineup_key not in lineup_ids:
                continue
            api_player_id = int(row['player_id'])
            row['api_player_id'] = api_player_id
            row['lineup_id'] = lineup_ids[lineup_key]
            row['player_id'] = player_ids.get(api_player_id)
            lineup_players.append(row)
        if lineup_players:
            await self._upsert(
                'lineup_players', lineup_players, on_conflict='lineup_id,lineup_order'
            )


def _iso_after(value: str, *, hours: int = 0, minutes: int = 0) -> str:
    from datetime import timedelta

    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (parsed + timedelta(hours=hours, minutes=minutes)).isoformat()
