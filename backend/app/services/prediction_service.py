from __future__ import annotations

from collections import Counter
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.errors import (
    DatabaseError,
    FixtureNotFoundError,
    PredictionInputError,
    ProviderAccessRestrictionError,
    ProviderError,
    UnsupportedLeagueError,
)
from app.db.supabase_client import get_supabase
from app.services.api_football_client import ApiFootballClient
from app.services.baseline_model_service import (
    BASELINE_FINAL_STATUSES,
    BASELINE_LEAGUES,
    BASELINE_UPCOMING_STATUSES,
    predict_empirical_bayes_poisson,
)
from app.services.baseline_market_service import (
    DOMESTIC_LEAGUE_IDS,
    REFERENCE_STATISTICS_LEAGUE_ID,
    choose_team_history_sources,
    estimate_player_candidates,
    estimate_team_statistics,
    public_history_sources,
)
from app.services.calendar_prediction_service import (
    CALENDAR_PREDICTION_LEAGUES,
    GLOBAL_TEAM_HISTORY_PRIOR_CODE,
    build_calendar_profile_prediction,
)
from app.services.calendar_visibility import LocalTeamProfile, local_team_profile
from app.services.feature_builder import build_features
from app.services.fixture_service import (
    CALENDAR_ONLY_LEAGUE_IDS,
    LEAGUE_ID_TO_CODE,
    upsert_fixture_item,
)
from app.services.model_service import predict
from app.services.odds_parser import parse_opening_odds
from app.services.scorer_service import possible_scorers
from app.services.supabase_repository import SupabaseRepository
from app.services.team_history_profile import build_team_history_profile


logger = logging.getLogger(__name__)

_PREDICTION_SEMANTIC_FIELDS = (
    'fixture_id',
    'league_id',
    'league_code',
    'home_team_id',
    'away_team_id',
    'home_team_name',
    'away_team_name',
    'kickoff',
    'stage',
    'lineups_confirmed',
    'home_win_probability',
    'draw_probability',
    'away_win_probability',
    'over25_probability',
    'btts_probability',
    'expected',
    'likely_scores',
    'possible_scorers',
    'model_metadata',
    'features_snapshot',
    'published',
)


def _first(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get('response') or []
    if not response:
        raise FixtureNotFoundError('API-Football did not return the requested fixture.')
    return response[0]


async def _fixture_payload(api: Any, fixture_id: int) -> dict[str, Any]:
    details = getattr(api, 'fixture_details', None)
    if callable(details):
        return {'response': await details([fixture_id])}
    # Compatibility for injected legacy clients used by integrations/tests.
    return await api.fixture(fixture_id)


async def _lineups_payload(api: Any, fixture_id: int) -> dict[str, Any]:
    lineups = getattr(api, 'fixture_lineups', None)
    if callable(lineups):
        return {'response': await lineups(fixture_id)}
    # Compatibility for injected legacy clients used by integrations/tests.
    return await api.lineups(fixture_id)


def _kickoff(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace('Z', '+00:00')
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _prediction_stage(kickoff: datetime) -> str:
    minutes_until = (kickoff - datetime.now(timezone.utc)).total_seconds() / 60
    if 0 <= minutes_until <= 90:
        return 'waiting_lineups'
    if minutes_until <= 1440:
        return 'prematch'
    return 'initial'


def _same_prediction(
    stored: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    for field in _PREDICTION_SEMANTIC_FIELDS:
        stored_value = stored.get(field)
        candidate_value = candidate.get(field)
        if field == 'kickoff':
            try:
                if _kickoff(stored_value) != _kickoff(candidate_value):
                    return False
                continue
            except (TypeError, ValueError):
                pass
        if stored_value != candidate_value:
            return False
    return True


def _persist_prediction(
    supabase: Any,
    record: dict[str, Any],
    *,
    preserve_if_unchanged: bool = False,
) -> dict[str, Any]:
    try:
        stored: dict[str, Any] | None = None
        if preserve_if_unchanged or record.get('lineups_confirmed') is False:
            response = (
                supabase.table('predictions')
                .select(','.join((*_PREDICTION_SEMANTIC_FIELDS, 'updated_at')))
                .eq('fixture_id', record['fixture_id'])
                .limit(1)
                .execute()
            )
            rows = response.data or []
            stored = dict(rows[0]) if rows else None
        # Confirmation is monotonic for a fixture. A later statistical refresh
        # or a transient empty provider response must never revert the final
        # lineup state and accidentally unlock another AI recalibration.
        if stored and stored.get('lineups_confirmed') is True:
            record = {
                **record,
                'lineups_confirmed': True,
                'stage': 'lineups_confirmed',
            }
        if preserve_if_unchanged and stored and _same_prediction(stored, record):
            return stored
        supabase.table('predictions').upsert(record, on_conflict='fixture_id').execute()
        supabase.table('prediction_versions').insert({
            'fixture_id': record['fixture_id'],
            'stage': record['stage'],
            'payload': record,
        }).execute()
    except Exception as exc:
        raise DatabaseError('Could not persist the prediction.') from exc
    return record


async def _refresh_statistical_baseline(
    *,
    fixture_id: int,
    fixture_row: dict[str, Any],
    repository: SupabaseRepository,
    supabase: Any,
) -> dict[str, Any]:
    try:
        league_id = int(fixture_row['league_id'])
        home_team_id = int(fixture_row['home_team_id'])
        away_team_id = int(fixture_row['away_team_id'])
        home_team_name = str(fixture_row['home_team_name'])
        away_team_name = str(fixture_row['away_team_name'])
        kickoff = _kickoff(fixture_row.get('kickoff') or fixture_row['fixture_date_utc'])
        status_short = str(fixture_row['status_short'] or '').upper()
    except (KeyError, TypeError, ValueError) as exc:
        raise PredictionInputError('The stored fixture is incomplete for prediction.') from exc
    if status_short not in BASELINE_UPCOMING_STATUSES or kickoff <= datetime.now(timezone.utc):
        raise PredictionInputError('The stored fixture is no longer upcoming.')

    try:
        historical_rows = await repository.historical_finished_fixtures_before(
            league_id=league_id,
            kickoff=kickoff.isoformat(),
            statuses=BASELINE_FINAL_STATUSES,
        )
    except Exception as exc:
        raise DatabaseError('Could not read historical fixtures for prediction.') from exc
    result = predict_empirical_bayes_poisson(
        league_id=league_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        target_kickoff=kickoff,
        historical_rows=historical_rows,
    )

    # Detailed statistics are optional and are read only from already stored,
    # finished fixtures. Include the three domestic leagues so cup teams can
    # use their own league history and so a clearly labelled Peru reference
    # prior remains available when the selected source has no detail coverage.
    market_league_ids = set(DOMESTIC_LEAGUE_IDS) | {league_id}
    if market_league_ids == {league_id}:
        market_fixture_rows = historical_rows
    else:
        try:
            market_fixture_rows = await repository.historical_finished_fixtures_before_many(
                league_ids=market_league_ids,
                kickoff=kickoff.isoformat(),
                statuses=BASELINE_FINAL_STATUSES,
            )
        except Exception as exc:
            raise DatabaseError('Could not read historical market fixtures.') from exc

    home_team_ref_id = int(fixture_row.get('home_team_ref_id') or home_team_id)
    away_team_ref_id = int(fixture_row.get('away_team_ref_id') or away_team_id)
    sources, eligible_market_fixtures = choose_team_history_sources(
        target_league_id=league_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team_ref_id=home_team_ref_id,
        away_team_ref_id=away_team_ref_id,
        target_kickoff=kickoff,
        historical_fixture_rows=market_fixture_rows,
    )
    statistics_league_ids = {
        REFERENCE_STATISTICS_LEAGUE_ID,
        *(int(source['source_league_id']) for source in sources.values()),
    }
    market_fixture_ids = {
        int(row['_fixture_id'])
        for row in eligible_market_fixtures
        if int(row['_league_id']) in statistics_league_ids
    }
    player_fixture_ids = {
        int(fixture_id)
        for source in sources.values()
        for fixture_id in source['_team_fixture_ids']
    }
    try:
        team_statistics_rows = await repository.team_statistics_for_fixtures(
            market_fixture_ids
        )
        player_statistics_rows = await repository.player_statistics_for_fixtures(
            fixture_ids=player_fixture_ids,
            team_ids={home_team_ref_id, away_team_ref_id},
        )
        players_by_id = await repository.players_by_ids(
            row['player_id']
            for row in player_statistics_rows
            if row.get('player_id') is not None
        )
    except Exception as exc:
        raise DatabaseError('Could not read stored historical statistics.') from exc

    market_expected, market_metadata = estimate_team_statistics(
        sources=sources,
        eligible_fixture_rows=eligible_market_fixtures,
        team_statistics_rows=team_statistics_rows,
    )
    expected = {**result['expected'], **market_expected}
    scorers, assistants, player_metadata = estimate_player_candidates(
        sources=sources,
        target_kickoff=kickoff,
        expected_goals=expected,
        player_statistics_rows=player_statistics_rows,
        players_by_id=players_by_id,
        team_names={'home': home_team_name, 'away': away_team_name},
    )
    model_metadata = {
        **result['model'],
        'goal_lines': result['goal_lines'],
        'possible_assistants': assistants,
        'market_statistics': market_metadata,
        'player_candidates': player_metadata,
        'history_sources': public_history_sources(sources),
    }
    league = BASELINE_LEAGUES[league_id]
    record = {
        'fixture_id': fixture_id,
        'league_id': league_id,
        'league_code': league.code,
        'home_team_id': home_team_id,
        'away_team_id': away_team_id,
        'home_team_name': home_team_name,
        'away_team_name': away_team_name,
        'kickoff': kickoff.isoformat(),
        'stage': _prediction_stage(kickoff),
        'lineups_confirmed': False,
        'home_win_probability': result['probabilities']['home_win'],
        'draw_probability': result['probabilities']['draw'],
        'away_win_probability': result['probabilities']['away_win'],
        'over25_probability': result['probabilities']['over_2_5'],
        'btts_probability': result['probabilities']['btts'],
        'expected': expected,
        'likely_scores': [],
        'possible_scorers': scorers,
        'model_metadata': model_metadata,
        'features_snapshot': result['features'],
        'published': True,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    return _persist_prediction(
        supabase,
        record,
        preserve_if_unchanged=True,
    )


def _calendar_player_history_sources(
    *,
    fixture_row: dict[str, Any],
    projection: dict[str, Any],
    target_kickoff: datetime,
    historical_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    known_sides = set(projection['model']['known_profile_sides'])
    eligible: list[dict[str, Any]] = []
    for value in historical_rows:
        row = dict(value)
        try:
            league_id = int(row['league_id'])
            home_team_id = int(row['home_team_id'])
            away_team_id = int(row['away_team_id'])
            kickoff = _kickoff(row.get('kickoff') or row['fixture_date_utc'])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            str(row.get('status_short') or '').upper()
            not in BASELINE_FINAL_STATUSES
            or league_id == 667
            or row.get('home_goals') is None
            or row.get('away_goals') is None
            or kickoff >= target_kickoff
        ):
            continue
        fixture_id = row.get('id') or row.get('api_fixture_id')
        if fixture_id is None:
            continue
        row.update({
            '_fixture_id': int(fixture_id),
            '_league_id': league_id,
            '_kickoff': kickoff,
            '_home_team_id': home_team_id,
            '_away_team_id': away_team_id,
            '_home_team_ref_id': row.get('home_team_ref_id'),
            '_away_team_ref_id': row.get('away_team_ref_id'),
        })
        eligible.append(row)
    eligible.sort(key=lambda row: (row['_kickoff'], row['_fixture_id']))

    sources: dict[str, dict[str, Any]] = {}
    for side in ('home', 'away'):
        api_team_id = int(fixture_row[f'{side}_team_id'])
        profile = projection['features']['profiles'][side]
        profile_history = profile.get('history')
        history_team_ref_id = (
            profile_history.get('surrogate_team_id')
            if isinstance(profile_history, dict)
            else None
        )
        if side not in known_sides:
            team_ref_id = int(
                fixture_row.get(f'{side}_team_ref_id')
                or history_team_ref_id
                or api_team_id
            )
            sources[side] = {
                'team_api_id': api_team_id,
                'team_ref_id': team_ref_id,
                'venue': side,
                'source_kind': 'neutral_prior_only',
                'source_league_id': int(fixture_row['league_id']),
                'eligible_league_fixtures': 0,
                'eligible_team_matches': 0,
                '_source_fixture_ids': (),
                '_team_fixture_ids': (),
                '_last_team_kickoff': None,
            }
            continue

        team_rows = [
            row
            for row in eligible
            if api_team_id in {
                row['_home_team_id'],
                row['_away_team_id'],
            }
        ]
        inferred_team_ref_ids = {
            int(team_ref_id)
            for row in team_rows
            for team_ref_id in (
                row.get('_home_team_ref_id')
                if row['_home_team_id'] == api_team_id
                else row.get('_away_team_ref_id'),
            )
            if team_ref_id is not None
        }
        inferred_team_ref_id = (
            next(iter(inferred_team_ref_ids))
            if len(inferred_team_ref_ids) == 1
            else None
        )
        team_ref_id = int(
            fixture_row.get(f'{side}_team_ref_id')
            or history_team_ref_id
            or inferred_team_ref_id
            or api_team_id
        )
        counts = Counter(row['_league_id'] for row in team_rows)
        preferred_league_id = (
            profile.get('source_league_id') or profile.get('league_id')
        )
        if counts:
            source_league_id = min(
                counts,
                key=lambda league_id: (
                    -counts[league_id],
                    league_id != preferred_league_id,
                    league_id,
                ),
            )
            selected = team_rows
            source_kind = (
                f'{profile.get("source_kind") or "local_profile"}'
                '_with_stored_history'
            )
        else:
            source_league_id = int(
                preferred_league_id or fixture_row['league_id']
            )
            selected = []
            source_kind = 'local_profile_only'
        fixture_ids = tuple(row['_fixture_id'] for row in selected)
        sources[side] = {
            'team_api_id': api_team_id,
            'team_ref_id': team_ref_id,
            'venue': side,
            'source_kind': source_kind,
            'source_league_id': source_league_id,
            'eligible_league_fixtures': sum(
                row['_league_id'] == source_league_id for row in eligible
            ),
            'eligible_team_matches': len(selected),
            '_source_fixture_ids': fixture_ids,
            '_team_fixture_ids': fixture_ids,
            '_last_team_kickoff': (
                selected[-1]['_kickoff'].isoformat() if selected else None
            ),
        }
    return sources


async def _calendar_history_profile_overrides(
    *,
    fixture_row: dict[str, Any],
    target_kickoff: datetime,
    repository: SupabaseRepository,
) -> tuple[
    dict[str, LocalTeamProfile],
    dict[str, list[dict[str, Any]]],
]:
    """Build missing-side profiles from narrowly loaded Supabase history."""

    local_profiles = {
        side: local_team_profile(fixture_row.get(f'{side}_team_name'))
        for side in ('home', 'away')
    }
    sides = ('home', 'away')
    missing_sides = [
        side for side, profile in local_profiles.items() if profile is None
    ]
    histories: list[list[dict[str, Any]]] = []
    for side in sides:
        histories.append(
            await repository.historical_finished_fixtures_for_team(
            api_team_id=int(fixture_row[f'{side}_team_id']),
            kickoff=target_kickoff.isoformat(),
            statuses=BASELINE_FINAL_STATUSES,
            limit=100,
        )
        )
    history_by_side = {
        side: [
            dict(row)
            for row in rows
            if int(row.get('league_id') or 0) != 667
        ]
        for side, rows in zip(sides, histories, strict=True)
    }
    fixture_ids = {
        int(row['id'])
        for side in missing_sides
        for row in history_by_side[side]
        if row.get('id') is not None
    }
    statistics_rows = (
        await repository.team_statistics_for_fixtures(fixture_ids)
        if fixture_ids
        else []
    )

    overrides: dict[str, LocalTeamProfile] = {}
    for side in missing_sides:
        profile_values = build_team_history_profile(
            api_team_id=int(fixture_row[f'{side}_team_id']),
            team_name=str(fixture_row[f'{side}_team_name']),
            team_ref_id=(
                int(fixture_row[f'{side}_team_ref_id'])
                if fixture_row.get(f'{side}_team_ref_id') is not None
                else None
            ),
            fixture_rows=history_by_side[side],
            team_statistics_rows=statistics_rows,
            cutoff=target_kickoff,
        )
        if profile_values is None:
            continue
        overrides[side] = LocalTeamProfile(
            league_code=GLOBAL_TEAM_HISTORY_PRIOR_CODE,
            profile_name=str(fixture_row[f'{side}_team_name']),
            values=profile_values,
        )
    return overrides, history_by_side


async def _refresh_calendar_profile_fallback(
    *,
    fixture_id: int,
    fixture_row: dict[str, Any],
    repository: SupabaseRepository,
    supabase: Any,
) -> dict[str, Any]:
    try:
        league_id = int(fixture_row['league_id'])
        home_team_id = int(fixture_row['home_team_id'])
        away_team_id = int(fixture_row['away_team_id'])
        home_team_name = str(fixture_row['home_team_name'])
        away_team_name = str(fixture_row['away_team_name'])
        kickoff = _kickoff(
            fixture_row.get('kickoff') or fixture_row['fixture_date_utc']
        )
        status_short = str(fixture_row['status_short'] or '').upper()
    except (KeyError, TypeError, ValueError) as exc:
        raise PredictionInputError(
            'The stored fixture is incomplete for prediction.'
        ) from exc
    if (
        league_id not in CALENDAR_ONLY_LEAGUE_IDS
        or status_short not in BASELINE_UPCOMING_STATUSES
        or kickoff <= datetime.now(timezone.utc)
    ):
        raise PredictionInputError('The stored fixture is no longer upcoming.')

    try:
        profile_overrides, targeted_history = (
            await _calendar_history_profile_overrides(
                fixture_row=fixture_row,
                target_kickoff=kickoff,
                repository=repository,
            )
        )
        projection = build_calendar_profile_prediction(
            league_id=league_id,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
            profile_overrides=profile_overrides,
        )
        historical_by_id = {
            int(row.get('id') or row.get('api_fixture_id')): row
            for row in (
                row
                for rows in targeted_history.values()
                for row in rows
            )
            if row.get('id') is not None
            or row.get('api_fixture_id') is not None
        }
        historical_rows = list(historical_by_id.values())
        sources = _calendar_player_history_sources(
            fixture_row=fixture_row,
            projection=projection,
            target_kickoff=kickoff,
            historical_rows=historical_rows,
        )
        known_sides = set(projection['model']['known_profile_sides'])
        known_team_ref_ids = {
            int(sources[side]['team_ref_id']) for side in known_sides
        }
        player_fixture_ids = {
            int(history_fixture_id)
            for side in known_sides
            for history_fixture_id in sources[side]['_team_fixture_ids']
        }
        player_statistics_rows = (
            await repository.player_statistics_for_fixtures(
                fixture_ids=player_fixture_ids,
                team_ids=known_team_ref_ids,
            )
            if player_fixture_ids
            else []
        )
        players_by_id = await repository.players_by_ids(
            row['player_id']
            for row in player_statistics_rows
            if row.get('player_id') is not None
        )
    except PredictionInputError:
        raise
    except Exception as exc:
        raise DatabaseError(
            'Could not read stored calendar fallback history.'
        ) from exc

    scorers, assistants, player_metadata = estimate_player_candidates(
        sources=sources,
        target_kickoff=kickoff,
        expected_goals=projection['expected'],
        player_statistics_rows=player_statistics_rows,
        players_by_id=players_by_id,
        team_names={'home': home_team_name, 'away': away_team_name},
    )
    known_team_ids = {
        int(sources[side]['team_api_id'])
        for side in projection['model']['known_profile_sides']
    }
    scorers = [
        player for player in scorers
        if int(player['team_id']) in known_team_ids
    ]
    assistants = [
        player for player in assistants
        if int(player['team_id']) in known_team_ids
    ]

    league_code, _league_name = CALENDAR_PREDICTION_LEAGUES[league_id]
    model_metadata = {
        **projection['model'],
        'goal_lines': projection['goal_lines'],
        'possible_assistants': assistants,
        'player_candidates': player_metadata,
        'history_sources': public_history_sources(sources),
        'cutoff_rule': 'stored status FT/AET/PEN and kickoff < target kickoff',
        'cutoff_kickoff': kickoff.isoformat(),
    }
    record = {
        'fixture_id': fixture_id,
        'league_id': league_id,
        'league_code': league_code,
        'home_team_id': home_team_id,
        'away_team_id': away_team_id,
        'home_team_name': home_team_name,
        'away_team_name': away_team_name,
        'kickoff': kickoff.isoformat(),
        'stage': _prediction_stage(kickoff),
        'lineups_confirmed': False,
        'home_win_probability': projection['probabilities']['home_win'],
        'draw_probability': projection['probabilities']['draw'],
        'away_win_probability': projection['probabilities']['away_win'],
        'over25_probability': projection['probabilities']['over_2_5'],
        'btts_probability': projection['probabilities']['btts'],
        'expected': projection['expected'],
        'likely_scores': [],
        'possible_scorers': scorers,
        'model_metadata': model_metadata,
        'features_snapshot': projection['features'],
        'published': True,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    return _persist_prediction(
        supabase,
        record,
        preserve_if_unchanged=True,
    )


async def refresh_prediction(
    fixture_id: int,
    *,
    api_client: ApiFootballClient | None = None,
    db_client: Any | None = None,
    odds_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    supabase = db_client if db_client is not None else get_supabase()
    repository = SupabaseRepository(client=supabase)
    try:
        stored_fixture = await repository.prediction_fixture(fixture_id)
    except Exception as exc:
        raise DatabaseError('Could not read the fixture for prediction.') from exc
    if stored_fixture is None:
        raise FixtureNotFoundError('The requested fixture is not stored.')
    try:
        stored_league_id = int(stored_fixture['league_id'])
    except (KeyError, TypeError, ValueError) as exc:
        raise PredictionInputError('The stored fixture has no valid league.') from exc
    if stored_league_id in BASELINE_LEAGUES:
        return await _refresh_statistical_baseline(
            fixture_id=fixture_id,
            fixture_row=stored_fixture,
            repository=repository,
            supabase=supabase,
        )
    if stored_league_id in CALENDAR_ONLY_LEAGUE_IDS:
        return await _refresh_calendar_profile_fallback(
            fixture_id=fixture_id,
            fixture_row=stored_fixture,
            repository=repository,
            supabase=supabase,
        )

    owns_api = api_client is None
    api = api_client or ApiFootballClient(request_log_sink=repository)
    try:
        fixture_payload = await _fixture_payload(api, fixture_id)
        item = _first(fixture_payload)
        try:
            fixture = item['fixture']
            league = item['league']
            teams = item['teams']
            league_id = int(league['id'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError('API-Football returned an incomplete fixture payload.') from exc

        league_code = LEAGUE_ID_TO_CODE.get(league_id)
        if not league_code:
            raise UnsupportedLeagueError(f'League {league_id} is not enabled.')
        try:
            returned_fixture_id = int(fixture['id'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError('API-Football returned an invalid fixture id.') from exc
        if returned_fixture_id != fixture_id:
            raise ProviderError('API-Football returned a different fixture id.')

        fixture_row = upsert_fixture_item(item, supabase)

        kickoff = _kickoff(fixture_row['kickoff'])
        now = datetime.now(timezone.utc)
        minutes_until = (kickoff.astimezone(timezone.utc) - now).total_seconds() / 60

        # Odds are optional and may only be supplied explicitly by a caller
        # that already fetched them. Prediction refresh never spends a hidden
        # provider request on the /odds endpoint.
        odds = parse_opening_odds(odds_payload or {'response': []})

        lineups_payload: dict[str, Any] = {'response': []}
        lineups_confirmed = False
        stage = 'initial'
        if minutes_until <= 1440:
            stage = 'prematch'
        if 0 <= minutes_until <= 90:
            try:
                lineups_payload = await _lineups_payload(api, fixture_id)
            except ProviderAccessRestrictionError:
                logger.info(
                    'Optional lineups unavailable for fixture %s due to provider plan.',
                    fixture_id,
                )
                lineups_payload = {'response': []}
            lineups_confirmed = len(lineups_payload.get('response', [])) >= 2
            stage = 'lineups_confirmed' if lineups_confirmed else 'waiting_lineups'

        try:
            features = build_features(
                league_code=league_code,
                kickoff=kickoff,
                home_name=teams['home']['name'],
                away_name=teams['away']['name'],
                odds=odds,
            )
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise PredictionInputError('A team profile or model input is unavailable.') from exc
        result = predict(league_code, features)
        scorers = possible_scorers(lineups_payload)
        model_metadata = {
            **result['model'],
            'goal_lines': result.get('goal_lines', []),
            # There is no evidenced assistant model for the legacy bundle yet.
            'possible_assistants': [],
        }

        record = {
            'fixture_id': fixture_id,
            'league_id': league_id,
            'league_code': league_code,
            'home_team_id': int(teams['home']['id']),
            'away_team_id': int(teams['away']['id']),
            'home_team_name': teams['home']['name'],
            'away_team_name': teams['away']['name'],
            'kickoff': fixture['date'],
            'stage': stage,
            'lineups_confirmed': lineups_confirmed,
            'home_win_probability': result['probabilities']['home_win'],
            'draw_probability': result['probabilities']['draw'],
            'away_win_probability': result['probabilities']['away_win'],
            'over25_probability': result['probabilities']['over_2_5'],
            'btts_probability': result['probabilities']['btts'],
            'expected': result['expected'],
            'likely_scores': [],
            'possible_scorers': scorers,
            'model_metadata': model_metadata,
            'features_snapshot': features,
            'published': True,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

        _persist_prediction(supabase, record)
        return record
    finally:
        if owns_api:
            await api.close()
