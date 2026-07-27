from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Mapping, Sequence

from app.core.errors import ProviderAccessRestrictionError
from app.db.supabase_client import get_supabase
from app.services.api_football_client import ApiFootballClient
from app.services.fixture_normalizer import normalize_fixture
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)

FINAL_STATUSES = frozenset({'FT', 'AET', 'PEN'})
VOID_STATUSES = frozenset({'ABD', 'AWD', 'CANC', 'WO'})
MARKET_STAT_COLUMNS = {
    'corners': 'corners',
    'yellow_cards': 'yellow_cards',
    'shots': 'total_shots',
    'shots_on_target': 'shots_on_goal',
}
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MAX_MATCHES = 100
PROVIDER_FIXTURE_DETAIL_BATCH_SIZE = 20
_DETAIL_ACCESS_BLOCKED_UTC_DATE: str | None = None
_DATE_ACCESS_BLOCKED_UTC_DATE: str | None = None
_DETAIL_ATTEMPTED_IDS_BY_UTC_DATE: dict[str, set[int]] = {}


def reset_detail_access_cache() -> None:
    """Clear daily provider capability/attempt caches (primarily for tests)."""

    global _DATE_ACCESS_BLOCKED_UTC_DATE, _DETAIL_ACCESS_BLOCKED_UTC_DATE
    _DETAIL_ACCESS_BLOCKED_UTC_DATE = None
    _DATE_ACCESS_BLOCKED_UTC_DATE = None
    _DETAIL_ATTEMPTED_IDS_BY_UTC_DATE.clear()


def _details_are_blocked(current_date: str) -> bool:
    return _DETAIL_ACCESS_BLOCKED_UTC_DATE == current_date


def _block_details_for_date(current_date: str) -> None:
    global _DETAIL_ACCESS_BLOCKED_UTC_DATE
    _DETAIL_ACCESS_BLOCKED_UTC_DATE = current_date


def _date_endpoint_is_blocked(current_date: str) -> bool:
    return _DATE_ACCESS_BLOCKED_UTC_DATE == current_date


def _block_date_endpoint_for_date(current_date: str) -> None:
    global _DATE_ACCESS_BLOCKED_UTC_DATE
    _DATE_ACCESS_BLOCKED_UTC_DATE = current_date


def _detail_attempted_ids(current_date: str) -> set[int]:
    """Return today's attempted IDs and discard stale in-memory quota state."""

    stale_dates = [
        value
        for value in _DETAIL_ATTEMPTED_IDS_BY_UTC_DATE
        if value != current_date
    ]
    for stale_date in stale_dates:
        _DETAIL_ATTEMPTED_IDS_BY_UTC_DATE.pop(stale_date, None)
    return _DETAIL_ATTEMPTED_IDS_BY_UTC_DATE.setdefault(
        current_date,
        set(),
    )


def _is_access_restriction(exc: Exception) -> bool:
    return isinstance(exc, ProviderAccessRestrictionError) or type(exc).__name__ in {
        'ApiFootballAccessRestrictionError',
        'ApiFootballDateAccessError',
        'SeasonUnavailableError',
    }


def _as_utc(value: Any) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fixture_id(payload: Mapping[str, Any]) -> int | None:
    fixture = payload.get('fixture')
    if not isinstance(fixture, Mapping):
        return None
    try:
        value = int(fixture.get('id'))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _forecast_snapshot(
    version: Mapping[str, Any],
) -> dict[str, Any] | None:
    payload = version.get('payload')
    if not isinstance(payload, Mapping):
        return None
    forecast = payload.get('market_forecast')
    if not isinstance(forecast, Mapping):
        return None
    version_name = str(forecast.get('version') or '').strip()
    markets = forecast.get('markets')
    if not version_name or not isinstance(markets, list):
        return None
    return dict(forecast)


def _sum_stat(
    rows: Sequence[Mapping[str, Any]],
    column: str,
) -> float | None:
    if len(rows) != 2:
        return None
    values: list[float] = []
    for row in rows:
        raw = row.get(column)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        values.append(value)
    return sum(values)


def actual_market_values(
    fixture: Mapping[str, Any],
    team_statistics: Sequence[Mapping[str, Any]],
) -> dict[str, float | None]:
    """Return regulation-time totals that can be settled without guessing."""

    status = str(fixture.get('status_short') or '').upper()
    if status in {'AET', 'PEN'}:
        home_goals = fixture.get('fulltime_home')
        away_goals = fixture.get('fulltime_away')
    else:
        home_goals = fixture.get('home_goals')
        away_goals = fixture.get('away_goals')
    try:
        goals = (
            float(home_goals) + float(away_goals)
            if home_goals is not None and away_goals is not None
            else None
        )
    except (TypeError, ValueError):
        goals = None

    actual: dict[str, float | None] = {'goals': goals}
    for market, column in MARKET_STAT_COLUMNS.items():
        # API-Football's aggregate team statistics may include extra time.
        # Those values cannot safely settle a regulation-time market.
        actual[market] = (
            None
            if status in {'AET', 'PEN'}
            else _sum_stat(team_statistics, column)
        )
    return actual


def evaluate_market_forecast(
    *,
    fixture_id: int,
    prediction_version_id: str,
    forecast: Mapping[str, Any],
    actual: Mapping[str, float | None],
    fixture_status: str,
    evaluated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Settle every recommended line from one immutable forecast snapshot."""

    forecast_version = str(forecast.get('version') or '').strip()
    markets = forecast.get('markets')
    if not forecast_version or not isinstance(markets, list):
        raise ValueError('A versioned market forecast is required.')

    status = fixture_status.upper()
    results: list[dict[str, Any]] = []
    has_pending = False
    for market_row in markets:
        if not isinstance(market_row, Mapping):
            continue
        market = str(market_row.get('category') or '')
        scope = str(market_row.get('scope') or 'match_total')
        if market not in {'goals', *MARKET_STAT_COLUMNS}:
            continue
        lines = market_row.get('lines')
        if not isinstance(lines, list):
            continue
        actual_value = actual.get(market)
        for line_row in lines:
            if not isinstance(line_row, Mapping):
                continue
            direction = str(line_row.get('selection') or '').lower()
            if direction not in {'over', 'under'}:
                continue
            try:
                line = float(line_row['line'])
                probability = float(line_row['selection_probability'])
            except (KeyError, TypeError, ValueError):
                continue
            if line < 0 or not 0 <= probability <= 1:
                continue

            reason_code: str | None = None
            settled_at: str | None = evaluated_at
            if status in VOID_STATUSES:
                outcome = 'void'
                reason_code = 'fixture_void'
            elif status in {'AET', 'PEN'} and market != 'goals':
                outcome = 'void'
                reason_code = 'extra_time_not_separable'
            elif actual_value is None:
                outcome = 'pending'
                reason_code = 'final_statistics_missing'
                settled_at = None
                has_pending = True
            elif actual_value == line:
                outcome = 'push'
            elif direction == 'over':
                outcome = 'won' if actual_value > line else 'lost'
            else:
                outcome = 'won' if actual_value < line else 'lost'

            results.append({
                'fixture_id': fixture_id,
                'prediction_version_id': prediction_version_id,
                'forecast_version': forecast_version,
                'market': market,
                'scope': scope,
                'period': 'full_time',
                'line': line,
                'direction': direction,
                'probability': probability,
                'actual_value': actual_value,
                'outcome': outcome,
                'reason_code': reason_code,
                'evaluated_at': settled_at,
            })

    scored = sum(row['outcome'] in {'won', 'lost'} for row in results)
    correct = sum(row['outcome'] == 'won' for row in results)
    evaluation_status = (
        'void'
        if status in VOID_STATUSES
        else 'partial'
        if has_pending
        else 'completed'
    )
    summary = {
        'fixture_id': fixture_id,
        'prediction_version_id': prediction_version_id,
        'forecast_version': forecast_version,
        'status': evaluation_status,
        'actual': {
            market: value
            for market, value in actual.items()
            if value is not None
        },
        'scored_selections': scored,
        'correct_selections': correct,
        'accuracy': round(correct / scored, 6) if scored else None,
        'evaluated_at': evaluated_at,
    }
    return summary, results


def _provider_fixture_payloads(value: Any) -> list[Mapping[str, Any]]:
    """Normalize current and legacy injected API-client response shapes."""

    if isinstance(value, Mapping):
        value = value.get('response') or []
    if not isinstance(value, list):
        raise ValueError('The provider returned an invalid fixtures response.')
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError('The provider returned an invalid fixture payload.')
    return value


def _provider_fixture_status(payload: Mapping[str, Any]) -> str:
    fixture = payload.get('fixture')
    if not isinstance(fixture, Mapping):
        return ''
    status = fixture.get('status')
    if not isinstance(status, Mapping):
        return ''
    return str(status.get('short') or '').upper()


async def _persist_provider_fixture(
    *,
    payload: Mapping[str, Any],
    repository: SupabaseRepository,
    candidate_by_id: Mapping[int, Mapping[str, Any]],
    competitions: Mapping[int, Mapping[str, Any]],
    details: bool,
) -> bool:
    fixture_id = _fixture_id(payload)
    candidate = candidate_by_id.get(fixture_id or -1)
    if fixture_id is None or candidate is None:
        return False
    try:
        competition_id = int(candidate['competition_id'])
    except (KeyError, TypeError, ValueError):
        return False
    competition = competitions.get(competition_id)
    if competition is None:
        return False
    try:
        normalized = normalize_fixture(
            payload,
            competition_id=competition_id,
        )
        await repository.persist_fixture(
            normalized,
            competition=competition,
            details=details,
            coverage=(
                {
                    'fixtures': {
                        'statistics_fixtures': True,
                    },
                }
                if details
                else None
            ),
        )
    except Exception:
        logger.exception(
            'Could not persist post-match fixture %s (%s payload).',
            fixture_id,
            'detailed' if details else 'basic',
        )
        return False
    return True


async def _persist_detail_payloads(
    *,
    payloads: Sequence[Mapping[str, Any]],
    requested_ids: Sequence[int],
    repository: SupabaseRepository,
    candidate_by_id: Mapping[int, Mapping[str, Any]],
    competitions: Mapping[int, Mapping[str, Any]],
) -> tuple[int, int]:
    """Persist detail responses one at a time and account for missing rows."""

    requested = set(requested_ids)
    seen: set[int] = set()
    persisted = 0
    errors = 0
    for payload in payloads:
        fixture_id = _fixture_id(payload)
        if fixture_id is None or fixture_id not in requested or fixture_id in seen:
            errors += 1
            continue
        seen.add(fixture_id)
        if await _persist_provider_fixture(
            payload=payload,
            repository=repository,
            candidate_by_id=candidate_by_id,
            competitions=competitions,
            details=True,
        ):
            persisted += 1
        else:
            errors += 1
    errors += len(requested - seen)
    return persisted, errors


async def _refresh_current_utc_fixtures(
    *,
    api: Any,
    repository: SupabaseRepository,
    candidates: Sequence[Mapping[str, Any]],
    clock: datetime,
) -> dict[str, int]:
    """Refresh scores for today and catch up missing final statistics.

    The date endpoint is the source of final score/status. Detailed statistics
    are an optional second pass that may include historical final fixtures.
    Historical calendar days are deliberately not queried because the free
    provider plan rejects those dates.
    """

    counters = {
        'provider_fixture_requests': 0,
        'provider_date_requests': 0,
        'provider_detail_requests': 0,
        'basic_refreshed': 0,
        'details_refreshed': 0,
        'refresh_errors': 0,
    }
    current_date = clock.astimezone(timezone.utc).date()
    current_date_text = current_date.isoformat()
    current_candidates: list[Mapping[str, Any]] = []
    for candidate in candidates:
        try:
            kickoff = _as_utc(
                candidate.get('kickoff')
                or candidate.get('fixture_date_utc')
            )
        except (TypeError, ValueError):
            counters['refresh_errors'] += 1
            continue
        if kickoff.date() == current_date:
            current_candidates.append(candidate)
    if not candidates:
        return counters

    candidate_by_id = {
        int(candidate['id']): candidate
        for candidate in candidates
    }
    current_candidate_ids = {
        int(candidate['id'])
        for candidate in current_candidates
    }
    competition_ids = sorted({
        int(candidate['competition_id'])
        for candidate in candidates
        if candidate.get('competition_id') is not None
    })
    competitions = await repository.get_competitions_by_ids(competition_ids)

    # A stored final status is enough to make the optional statistics request,
    # even if the calendar response happens to omit that fixture.
    detail_ids: set[int] = {
        fixture_id
        for fixture_id, candidate in candidate_by_id.items()
        if str(candidate.get('status_short') or '').upper() in FINAL_STATUSES
    }

    date_payloads: list[Mapping[str, Any]] = []
    if (
        current_candidates
        and not _date_endpoint_is_blocked(current_date_text)
    ):
        counters['provider_fixture_requests'] += 1
        counters['provider_date_requests'] += 1
        try:
            raw_date_payloads = await api.fixtures_by_date(
                current_date_text,
                timezone_name='UTC',
            )
            date_payloads = _provider_fixture_payloads(raw_date_payloads)
        except Exception as exc:
            counters['refresh_errors'] += 1
            if _is_access_restriction(exc):
                _block_date_endpoint_for_date(current_date_text)
                logger.error(
                    'Current UTC fixture-date access is restricted: %s',
                    type(exc).__name__,
                )
            else:
                logger.error(
                    'Current UTC fixture-date refresh failed: %s',
                    type(exc).__name__,
                )

    for payload in date_payloads:
        fixture_id = _fixture_id(payload)
        if (
            fixture_id is None
            or fixture_id not in current_candidate_ids
        ):
            # The calendar endpoint returns every fixture on the date. Fixtures
            # without a published prediction are intentionally ignored.
            continue
        if await _persist_provider_fixture(
            payload=payload,
            repository=repository,
            candidate_by_id=candidate_by_id,
            competitions=competitions,
            details=False,
        ):
            counters['basic_refreshed'] += 1
        else:
            counters['refresh_errors'] += 1
        if _provider_fixture_status(payload) in FINAL_STATUSES:
            detail_ids.add(fixture_id)

    attempted_detail_ids = _detail_attempted_ids(current_date_text)
    requested_ids = sorted(detail_ids - attempted_detail_ids)
    if not requested_ids:
        return counters
    if _details_are_blocked(current_date_text):
        return counters

    for offset in range(
        0,
        len(requested_ids),
        PROVIDER_FIXTURE_DETAIL_BATCH_SIZE,
    ):
        batch_ids = requested_ids[
            offset:offset + PROVIDER_FIXTURE_DETAIL_BATCH_SIZE
        ]
        counters['provider_fixture_requests'] += 1
        counters['provider_detail_requests'] += 1
        try:
            raw_details = await api.fixture_details(batch_ids)
            detail_payloads = _provider_fixture_payloads(raw_details)
            attempted_detail_ids.update(batch_ids)
        except Exception as exc:
            counters['refresh_errors'] += 1
            if not _is_access_restriction(exc) or len(batch_ids) == 1:
                if _is_access_restriction(exc):
                    _block_details_for_date(current_date_text)
                logger.error(
                    'Post-match fixture-detail refresh failed: %s',
                    type(exc).__name__,
                )
                return counters

            # The batch restriction may be parameter-specific. Probe one
            # fixture only; if that is also restricted, cache the capability
            # failure and do not spend one request per match.
            probe_id = batch_ids[0]
            counters['provider_fixture_requests'] += 1
            counters['provider_detail_requests'] += 1
            try:
                raw_probe = await api.fixture_details([probe_id])
                probe_payloads = _provider_fixture_payloads(raw_probe)
                attempted_detail_ids.add(probe_id)
            except Exception as probe_exc:
                counters['refresh_errors'] += 1
                if _is_access_restriction(probe_exc):
                    _block_details_for_date(current_date_text)
                logger.error(
                    'Post-match fixture-detail capability probe failed: %s',
                    type(probe_exc).__name__,
                )
                return counters

            persisted, errors = await _persist_detail_payloads(
                payloads=probe_payloads,
                requested_ids=[probe_id],
                repository=repository,
                candidate_by_id=candidate_by_id,
                competitions=competitions,
            )
            counters['details_refreshed'] += persisted
            counters['refresh_errors'] += errors

            # The single-ID endpoint works while the multi-ID form does not.
            # Continue all remaining fixtures one at a time, persisting each
            # response before issuing the next request.
            remaining_ids = requested_ids[offset + 1:]
            for fixture_id in remaining_ids:
                counters['provider_fixture_requests'] += 1
                counters['provider_detail_requests'] += 1
                try:
                    raw_single = await api.fixture_details([fixture_id])
                    single_payloads = _provider_fixture_payloads(raw_single)
                    attempted_detail_ids.add(fixture_id)
                except Exception as single_exc:
                    counters['refresh_errors'] += 1
                    if _is_access_restriction(single_exc):
                        _block_details_for_date(current_date_text)
                        logger.error(
                            'Post-match fixture-detail access failed for %s: %s',
                            fixture_id,
                            type(single_exc).__name__,
                        )
                        return counters
                    logger.error(
                        'Post-match fixture-detail refresh failed for %s: %s',
                        fixture_id,
                        type(single_exc).__name__,
                    )
                    continue
                persisted, errors = await _persist_detail_payloads(
                    payloads=single_payloads,
                    requested_ids=[fixture_id],
                    repository=repository,
                    candidate_by_id=candidate_by_id,
                    competitions=competitions,
                )
                counters['details_refreshed'] += persisted
                counters['refresh_errors'] += errors
            return counters

        persisted, errors = await _persist_detail_payloads(
            payloads=detail_payloads,
            requested_ids=batch_ids,
            repository=repository,
            candidate_by_id=candidate_by_id,
            competitions=competitions,
        )
        counters['details_refreshed'] += persisted
        counters['refresh_errors'] += errors
    return counters


async def sync_and_evaluate_published_predictions(
    *,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_matches: int = DEFAULT_MAX_MATCHES,
    api_client: Any | None = None,
    db_client: Any | None = None,
) -> dict[str, Any]:
    """Refresh only predicted past fixtures, then measure deterministic picks."""

    if not 1 <= lookback_days <= 30:
        raise ValueError('lookback_days must be between 1 and 30.')
    if not 1 <= max_matches <= 100:
        raise ValueError('max_matches must be between 1 and 100.')
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)
    starts_at = clock - timedelta(days=lookback_days)
    # Poll every fixture whose kickoff has already occurred. Waiting two hours
    # risks losing access when the provider's free-plan UTC day rolls over.
    # Settlement itself still requires a final/void fixture status below.
    ends_at = clock
    database = db_client if db_client is not None else get_supabase()
    repository = SupabaseRepository(client=database)
    owns_api = api_client is None
    api = api_client or ApiFootballClient(request_log_sink=repository)

    candidates = await repository.prediction_evaluation_candidates(
        starts_at=starts_at,
        ends_at=ends_at,
        limit=max_matches,
    )
    fixture_ids = [int(row['id']) for row in candidates]
    initial_statistics = (
        await repository.team_statistics_for_fixtures(fixture_ids)
        if fixture_ids
        else []
    )
    statistics_by_fixture: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in initial_statistics:
        statistics_by_fixture[int(row['fixture_id'])].append(dict(row))

    needs_refresh = [
        row
        for row in candidates
        if (
            str(row.get('status_short') or '').upper()
            not in FINAL_STATUSES | VOID_STATUSES
            or len(statistics_by_fixture.get(int(row['id']), [])) != 2
        )
    ]
    refresh_counters = {
        'provider_fixture_requests': 0,
        'provider_date_requests': 0,
        'provider_detail_requests': 0,
        'basic_refreshed': 0,
        'details_refreshed': 0,
        'refresh_errors': 0,
    }
    try:
        if needs_refresh:
            try:
                refresh_counters = await _refresh_current_utc_fixtures(
                    api=api,
                    repository=repository,
                    candidates=needs_refresh,
                    clock=clock,
                )
            except Exception as exc:
                refresh_counters['refresh_errors'] += 1
                logger.error(
                    'Post-match fixture refresh failed: %s',
                    type(exc).__name__,
                )
    finally:
        if owns_api:
            await api.close()

    # Re-read after provider persistence so settlement never uses stale status.
    candidates = await repository.prediction_evaluation_candidates(
        starts_at=starts_at,
        ends_at=ends_at,
        limit=max_matches,
    )
    fixture_ids = [int(row['id']) for row in candidates]
    statistics_rows = (
        await repository.team_statistics_for_fixtures(fixture_ids)
        if fixture_ids
        else []
    )
    statistics_by_fixture.clear()
    for row in statistics_rows:
        statistics_by_fixture[int(row['fixture_id'])].append(dict(row))

    evaluated = 0
    partial = 0
    void = 0
    legacy_unscored = 0
    for fixture in candidates:
        fixture_id = int(fixture['id'])
        status = str(fixture.get('status_short') or '').upper()
        if status not in FINAL_STATUSES | VOID_STATUSES:
            continue
        if status in VOID_STATUSES:
            current_forecast = fixture.get('prediction_market_forecast')
            forecast_version = (
                str(current_forecast.get('version') or '')
                if isinstance(current_forecast, Mapping)
                else ''
            )
            await repository.save_prediction_evaluation(
                {
                    'fixture_id': fixture_id,
                    'prediction_version_id': None,
                    'forecast_version': forecast_version or 'not_applicable',
                    'status': 'void',
                    'actual': {},
                    'scored_selections': 0,
                    'correct_selections': 0,
                    'accuracy': None,
                    'evaluated_at': clock.isoformat(),
                },
                [],
            )
            void += 1
            continue
        kickoff = _as_utc(
            fixture.get('kickoff') or fixture.get('fixture_date_utc')
        )
        version = await repository.latest_prediction_version_before_kickoff(
            fixture_id,
            kickoff,
        )
        forecast = _forecast_snapshot(version or {})
        evaluated_at = clock.isoformat()
        if version is None or forecast is None:
            await repository.save_prediction_evaluation(
                {
                    'fixture_id': fixture_id,
                    'prediction_version_id': (
                        version.get('id') if version else None
                    ),
                    'forecast_version': 'legacy_without_snapshot',
                    'status': 'legacy_unscored',
                    'actual': {},
                    'scored_selections': 0,
                    'correct_selections': 0,
                    'accuracy': None,
                    'evaluated_at': evaluated_at,
                },
                [],
            )
            legacy_unscored += 1
            continue
        actual = actual_market_values(
            fixture,
            statistics_by_fixture.get(fixture_id, []),
        )
        summary, results = evaluate_market_forecast(
            fixture_id=fixture_id,
            prediction_version_id=str(version['id']),
            forecast=forecast,
            actual=actual,
            fixture_status=status,
            evaluated_at=evaluated_at,
        )
        await repository.save_prediction_evaluation(summary, results)
        evaluated += summary['status'] == 'completed'
        partial += summary['status'] == 'partial'
        void += summary['status'] == 'void'

    return {
        'candidates': len(candidates),
        **refresh_counters,
        'evaluated': evaluated,
        'partial': partial,
        'void': void,
        'legacy_unscored': legacy_unscored,
    }
