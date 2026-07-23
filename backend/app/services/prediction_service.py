from __future__ import annotations

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
from app.services.feature_builder import build_features
from app.services.fixture_service import LEAGUE_ID_TO_CODE, upsert_fixture_item
from app.services.model_service import predict
from app.services.odds_parser import parse_opening_odds
from app.services.scorer_service import possible_scorers
from app.services.supabase_repository import SupabaseRepository


logger = logging.getLogger(__name__)


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


def _persist_prediction(supabase: Any, record: dict[str, Any]) -> None:
    try:
        supabase.table('predictions').upsert(record, on_conflict='fixture_id').execute()
        supabase.table('prediction_versions').insert({
            'fixture_id': record['fixture_id'],
            'stage': record['stage'],
            'payload': record,
        }).execute()
    except Exception as exc:
        raise DatabaseError('Could not persist the prediction.') from exc


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
    _persist_prediction(supabase, record)
    return record


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
