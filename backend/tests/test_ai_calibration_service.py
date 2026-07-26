from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.core.config import Settings
from app.routes import predictions as prediction_routes
from app.schemas.ai_calibration import (
    AICalibrationModelOutput,
    BetRecommendation,
    ProbabilityBps,
)
from app.services.ai_calibration_service import (
    build_ai_calibration_input,
    output_to_public_analysis,
    refresh_ai_calibration,
    validate_and_normalize_output,
)
from app.services import ai_calibration_service


def _source_rows() -> dict:
    return {
        'prediction': {
            'fixture_id': 901,
            'league_id': 71,
            'league_code': 'brazil_serie_a',
            'home_team_id': 10,
            'away_team_id': 20,
            'home_team_name': 'Home FC',
            'away_team_name': 'Away FC',
            'kickoff': '2026-07-28T22:00:00+00:00',
            'stage': 'prematch',
            'lineups_confirmed': False,
            'home_win_probability': 0.4,
            'draw_probability': 0.3,
            'away_win_probability': 0.3,
            'over25_probability': 0.54,
            'btts_probability': 0.51,
            'expected': {
                'home_goals': 1.4,
                'away_goals': 1.1,
                'home_corners': 5.1,
                'away_corners': 4.3,
                'home_shots': 12.0,
                'away_shots': 10.0,
                'home_shots_on_target': 4.2,
                'away_shots_on_target': 3.5,
                'not_allowed': 'drop-me',
            },
            'possible_scorers': [],
            'model_metadata': {
                'model_type': 'statistical_baseline',
                'trained_rows': 120,
                'goal_lines': [
                    {'line': 0.5, 'probability': 0.92},
                    {'line': 1.5, 'probability': 0.73},
                    {'line': 2.5, 'probability': 0.54},
                    {'line': 3.5, 'probability': 0.31},
                    {'line': 4.5, 'probability': 0.15},
                ],
                'raw_provider_response': {'secret': 'must-not-pass'},
            },
            'features_snapshot': {
                'sample_sizes': {'league_finished_matches': 120},
                'raw_payload': {'secret': 'must-not-pass'},
            },
            'published': True,
            'updated_at': '2026-07-25T10:00:00+00:00',
        },
        'fixture': {
            'id': 901,
            'league_id': 71,
            'season': 2026,
            'kickoff': '2026-07-28T22:00:00+00:00',
            'status_short': 'NS',
            'home_team_id': 10,
            'away_team_id': 20,
            'home_team_ref_id': 100,
            'away_team_ref_id': 200,
            'home_team_name': 'Home FC',
            'away_team_name': 'Away FC',
            'raw_payload': {'authorization': 'must-not-pass'},
        },
        'histories': {
            'home': [{
                'id': 801,
                'league_id': 71,
                'kickoff': '2026-07-20T20:00:00+00:00',
                'home_team_id': 10,
                'away_team_id': 30,
                'home_team_ref_id': 100,
                'away_team_ref_id': 300,
                'home_goals': 2,
                'away_goals': 1,
            }],
            'away': [{
                'id': 802,
                'league_id': 71,
                'kickoff': '2026-07-21T20:00:00+00:00',
                'home_team_id': 40,
                'away_team_id': 20,
                'home_team_ref_id': 400,
                'away_team_ref_id': 200,
                'home_goals': 0,
                'away_goals': 1,
            }],
        },
        'statistics': [
            {
                'fixture_id': 801,
                'team_id': 100,
                'corners': 6,
                'total_shots': 13,
                'shots_on_goal': 5,
            },
            {
                'fixture_id': 802,
                'team_id': 200,
                'corners': 4,
                'total_shots': 9,
                'shots_on_goal': 3,
            },
        ],
        'lineups': [],
        'lineup_players': [],
        'lineup_teams': [],
        'injuries': [],
        'odds': None,
        'optional_status': {'fixture_id': 901},
    }


def _range(minimum=0.0, maximum=1.0):
    return {
        'status': 'available',
        'min': minimum,
        'max': maximum,
        'evidence_keys': ['base_prediction'],
    }


def _model_output() -> AICalibrationModelOutput:
    metric = {
        'home': _range(),
        'away': _range(),
        'total': _range(0.0, 2.0),
    }
    return AICalibrationModelOutput.model_validate({
        'match_type': 'official',
        'base_probabilities_bps': {'home': 4000, 'draw': 3000, 'away': 3000},
        'adjusted_probabilities_bps': {
            'home': 4200,
            'draw': 2900,
            'away': 2900,
        },
        'adjustments': [{
            'factor': 'preparation',
            'benefited_side': 'home',
            'impact_bps': 200,
            'confidence': 'low',
            'evidence_keys': ['team_history_summary'],
            'explanation': 'La muestra reciente favorece levemente al local.',
        }],
        'preparation_comparison': {
            'advantage': 'home',
            'explanation': 'Ventaja leve con una muestra pequeña.',
            'evidence_keys': ['team_history_summary'],
        },
        'rotation_effect': {
            'home': {
                'estimated_performance_change_pct': None,
                'confidence': 'low',
                'explanation': 'No hay alineación confirmada.',
                'evidence_keys': ['fixture_metadata'],
            },
            'away': {
                'estimated_performance_change_pct': None,
                'confidence': 'low',
                'explanation': 'No hay alineación confirmada.',
                'evidence_keys': ['fixture_metadata'],
            },
        },
        'projections': {
            'goals': metric,
            'corners': metric,
            'shots': metric,
            'shots_on_target': metric,
        },
        'recommended_market': {
            'market': 'over_1_5',
            'confidence': 'medium',
            'justification': 'Es la línea con mayor respaldo estadístico.',
            'evidence_keys': ['base_prediction'],
        },
        'conservative_alternative': {
            'market': 'double_chance_home_draw',
            'confidence': 'low',
            'justification': 'Protege el empate.',
            'evidence_keys': ['base_prediction'],
        },
        'risks': ['Muestra reciente pequeña.'],
        'missing_data': ['lineups'],
        'possible_model_errors': ['Posible sobrepeso de la localía.'],
        'refresh_with_lineups': True,
        'data_quality': 'medium',
        'lineups_considered': False,
    })


def test_input_is_allowlisted_and_deterministic_projections_are_server_truth():
    context = build_ai_calibration_input(_source_rows())
    rendered = str(context)

    assert context['server_truth']['base_probabilities_bps'] == {
        'home': 4000,
        'draw': 3000,
        'away': 3000,
    }
    assert context['server_truth']['adjustment_cap_bps'] == 800
    assert context['server_truth']['allowed_projections']['goals']['home'] == {
        'status': 'available',
        'min': 0.4,
        'max': 2.9,
        'evidence_keys': ['base_prediction'],
    }
    assert 'raw_provider_response' not in rendered
    assert 'raw_payload' not in rendered
    assert 'authorization' not in rendered
    assert 'not_allowed' not in rendered


def test_server_overwrites_projection_ranges_and_calculates_fair_odds_without_market():
    context = build_ai_calibration_input(_source_rows())
    normalized = validate_and_normalize_output(
        _model_output(),
        context,
        min_edge_bps=200,
    )
    analysis = output_to_public_analysis(normalized, context)

    assert normalized.projections.model_dump(mode='json') == (
        context['server_truth']['allowed_projections']
    )
    assert analysis.recommended_market.market == 'over_1_5'
    assert analysis.recommended_market.market_data_available is False
    assert analysis.recommended_market.estimated_edge_percentage_points is None
    assert analysis.recommended_market.minimum_value_odds == pytest.approx(1.37)


def test_adjustment_cap_is_enforced_before_persistence():
    context = build_ai_calibration_input(_source_rows())
    invalid = _model_output().model_copy(update={
        'adjusted_probabilities_bps': ProbabilityBps(**{
            'home': 4900,
            'draw': 2600,
            'away': 2500,
        }),
    })

    with pytest.raises(ValueError, match='exceeded'):
        validate_and_normalize_output(invalid, context, min_edge_bps=200)


class FakeRepository:
    def __init__(self, source):
        self.source = deepcopy(source)
        self.rows = []
        self.updates = []

    async def ai_calibration_source_rows(self, fixture_id):
        assert fixture_id == 901
        return deepcopy(self.source)

    async def latest_ai_calibration(
        self,
        fixture_id,
        *,
        input_hash=None,
        model=None,
        reasoning_effort=None,
        prompt_version=None,
        schema_version=None,
    ):
        matches = [
            row for row in self.rows
            if row['fixture_id'] == fixture_id
            and (input_hash is None or row['input_hash'] == input_hash)
            and (model is None or row.get('model') == model)
            and (
                reasoning_effort is None
                or row.get('reasoning_effort') == reasoning_effort
            )
            and (
                prompt_version is None
                or row.get('prompt_version') == prompt_version
            )
            and (
                schema_version is None
                or row.get('schema_version') == schema_version
            )
        ]
        return deepcopy(matches[-1]) if matches else None

    async def published_ai_calibration(self, fixture_id):
        matches = [
            row for row in self.rows
            if row['fixture_id'] == fixture_id
            and row.get('status') == 'updated'
            and row.get('published')
        ]
        return deepcopy(matches[-1]) if matches else None

    async def insert_ai_calibration_attempt(self, row):
        stored = deepcopy(dict(row))
        stored['id'] = '00000000-0000-0000-0000-000000000901'
        self.rows.append(stored)
        return deepcopy(stored)

    async def claim_ai_calibration(self, calibration_id, *, started_at):
        row = self.rows[-1]
        assert row['id'] == calibration_id
        if row['status'] != 'pending':
            return None
        row.update({'status': 'processing', 'started_at': started_at})
        return deepcopy(row)

    async def recover_stale_ai_calibration(self, calibration_id, *, started_at):
        row = self.rows[-1]
        if (
            row['id'] != calibration_id
            or row['status'] != 'processing'
            or row['started_at'] != started_at
        ):
            return None
        row.update({'status': 'pending', 'started_at': None})
        return deepcopy(row)

    async def update_ai_calibration(self, calibration_id, changes):
        row = self.rows[-1]
        assert row['id'] == calibration_id
        row.update(deepcopy(dict(changes)))
        self.updates.append(deepcopy(dict(changes)))
        return deepcopy(row)

    async def publish_ai_calibration(self, calibration_id):
        row = self.rows[-1]
        assert row['id'] == calibration_id
        row['published'] = True
        row['published_at'] = row['generated_at']
        return deepcopy(row)


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id='resp_mock_901',
            output_parsed=self.parsed,
            usage=SimpleNamespace(
                input_tokens=500,
                output_tokens=200,
                total_tokens=700,
                input_tokens_details=SimpleNamespace(cached_tokens=100),
                output_tokens_details=SimpleNamespace(reasoning_tokens=80),
            ),
        )


def test_refresh_uses_responses_structured_output_without_tools_or_storage():
    repository = FakeRepository(_source_rows())
    responses = FakeResponses(_model_output())
    client = SimpleNamespace(responses=responses)
    settings = Settings(
        _env_file=None,
        openai_api_key='sk-test-only-012345678901234567890',
        openai_model='gpt-5.6-sol',
        openai_reasoning_effort='max',
    )

    envelope = asyncio.run(refresh_ai_calibration(
        901,
        db_client=object(),
        repository=repository,
        openai_client=client,
        settings=settings,
    ))

    assert envelope.status == 'updated'
    assert envelope.analysis is not None
    assert len(responses.calls) == 1
    request = responses.calls[0]
    assert request['model'] == 'gpt-5.6-sol'
    assert request['reasoning'] == {'effort': 'max'}
    assert request['max_output_tokens'] == 12_000
    assert request['store'] is False
    assert request['text_format'] is AICalibrationModelOutput
    assert 'tools' not in request
    assert 'tool_choice' not in request
    rendered_input = str(request['input'])
    assert 'sk-test-only' not in rendered_input
    assert repository.updates[-1]['status'] == 'updated'
    assert repository.updates[-1]['response_id'] == 'resp_mock_901'
    assert repository.updates[-1]['input_tokens'] == 500
    assert repository.rows[-1]['published'] is True


def test_timestamp_only_base_rewrite_reuses_calibration_without_provider_call():
    repository = FakeRepository(_source_rows())
    responses = FakeResponses(_model_output())
    settings = Settings(
        _env_file=None,
        openai_api_key='sk-test-only-012345678901234567890',
    )
    client = SimpleNamespace(responses=responses)

    first = asyncio.run(refresh_ai_calibration(
        901,
        db_client=object(),
        repository=repository,
        openai_client=client,
        settings=settings,
    ))
    repository.source['prediction']['updated_at'] = (
        '2026-07-25T10:30:00+00:00'
    )
    second = asyncio.run(refresh_ai_calibration(
        901,
        db_client=object(),
        repository=repository,
        openai_client=client,
        settings=settings,
    ))

    assert first.status == 'updated'
    assert second.status == 'updated'
    assert second.is_stale is False
    assert len(responses.calls) == 1
    assert repository.rows[-1]['base_prediction_updated_at'] == (
        '2026-07-25T10:30:00+00:00'
    )


def test_prompt_version_change_creates_one_new_provider_attempt():
    repository = FakeRepository(_source_rows())
    responses = FakeResponses(_model_output())
    settings = Settings(
        _env_file=None,
        openai_api_key='sk-test-only-012345678901234567890',
    )
    client = SimpleNamespace(responses=responses)

    first = asyncio.run(refresh_ai_calibration(
        901,
        db_client=object(),
        repository=repository,
        openai_client=client,
        settings=settings,
    ))
    repository.rows[-1]['prompt_version'] = 'football-calibrator-0.9'
    second = asyncio.run(refresh_ai_calibration(
        901,
        db_client=object(),
        repository=repository,
        openai_client=client,
        settings=settings,
    ))

    assert first.status == 'updated'
    assert second.status == 'updated'
    assert len(responses.calls) == 2
    assert len(repository.rows) == 2
    assert repository.rows[-1]['attempt_number'] == 2
    assert repository.rows[-1]['prompt_version'] == (
        ai_calibration_service.PROMPT_VERSION
    )


def test_no_bet_has_no_threshold_or_edge():
    context = build_ai_calibration_input(_source_rows())
    output = _model_output().model_copy(update={
        'recommended_market': BetRecommendation(
            market='no_bet',
            confidence='no_bet',
            justification='La calidad de datos no permite recomendar un mercado.',
            evidence_keys=[],
        ),
    })
    normalized = validate_and_normalize_output(
        output,
        context,
        min_edge_bps=200,
    )

    recommendation = output_to_public_analysis(
        normalized,
        context,
    ).recommended_market
    assert recommendation.market == 'no_bet'
    assert recommendation.minimum_value_odds is None
    assert recommendation.estimated_edge_percentage_points is None


def test_one_sided_profile_cannot_adjust_or_recommend_1x2_markets():
    source = _source_rows()
    source['prediction']['model_metadata']['single_team_profile'] = True
    source['prediction']['model_metadata']['known_profile_sides'] = ['home']
    context = build_ai_calibration_input(source)

    normalized = validate_and_normalize_output(
        _model_output().model_copy(update={
            'recommended_market': BetRecommendation(
                market='home_win',
                confidence='medium',
                justification='Selección 1X2 propuesta.',
                evidence_keys=['base_prediction'],
            ),
        }),
        context,
        min_edge_bps=200,
    )

    assert normalized.adjusted_probabilities_bps == (
        normalized.base_probabilities_bps
    )
    assert normalized.adjustments == []
    assert normalized.recommended_market.market == 'no_bet'
    assert 'home_win' not in context['eligible_markets']
    assert output_to_public_analysis(normalized, context).show_1x2 is False


def test_fair_odds_threshold_is_strictly_above_break_even_tick():
    context = build_ai_calibration_input(_source_rows())
    output = _model_output().model_copy(update={
        'adjusted_probabilities_bps': ProbabilityBps(
            home=5000,
            draw=2500,
            away=2500,
        ),
        'recommended_market': BetRecommendation(
            market='home_win',
            confidence='medium',
            justification='Mercado respaldado por la probabilidad ajustada.',
            evidence_keys=['base_prediction'],
        ),
    })

    recommendation = output_to_public_analysis(
        output,
        context,
    ).recommended_market
    assert recommendation.minimum_value_odds == 2.01


def test_stale_optional_snapshots_are_not_admitted_as_evidence():
    source = _source_rows()
    old = '2026-07-25T20:00:00+00:00'
    source['lineups'] = [{
        'id': 1,
        'fixture_id': 901,
        'team_id': 100,
        'formation': '4-3-3',
        'confirmed': True,
        'fetched_at': old,
    }]
    source['optional_status'] = {
        'fixture_id': 901,
        'injuries_last_fetched_at': old,
    }
    source['odds'] = {
        'fetched_at': old,
        'raw_json': {'response': []},
    }

    context = build_ai_calibration_input(
        source,
        now=datetime(2026, 7, 26, 3, 0, tzinfo=timezone.utc),
    )

    assert context['lineup_snapshot']['status'] == 'no_disponible'
    assert context['injury_snapshot']['status'] == 'no_disponible'
    assert context['odds_snapshot']['status'] == 'no_disponible'
    assert 'lineup_snapshot' not in context['available_evidence']
    assert 'injury_snapshot' not in context['available_evidence']
    assert 'odds_snapshot' not in context['available_evidence']


def _seed_processing_attempt(repository, settings, *, started_at):
    context = build_ai_calibration_input(repository.source)
    input_hash = ai_calibration_service._semantic_input_hash(context)
    repository.rows.append({
        'id': '00000000-0000-0000-0000-000000000901',
        'fixture_id': 901,
        'attempt_number': 1,
        'input_hash': input_hash,
        'status': 'processing',
        'model': settings.openai_model,
        'reasoning_effort': settings.openai_reasoning_effort,
        'prompt_version': ai_calibration_service.PROMPT_VERSION,
        'schema_version': ai_calibration_service.SCHEMA_VERSION,
        'started_at': started_at,
        'retry_after': None,
        'base_prediction_updated_at': (
            repository.source['prediction']['updated_at']
        ),
    })


def test_stale_processing_attempt_is_recovered_and_completed():
    repository = FakeRepository(_source_rows())
    responses = FakeResponses(_model_output())
    settings = Settings(
        _env_file=None,
        openai_api_key='sk-test-only-012345678901234567890',
        openai_request_timeout_seconds=60,
    )
    _seed_processing_attempt(
        repository,
        settings,
        started_at=(
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat(),
    )

    result = asyncio.run(refresh_ai_calibration(
        901,
        repository=repository,
        db_client=object(),
        openai_client=SimpleNamespace(responses=responses),
        settings=settings,
    ))

    assert result.status == 'updated'
    assert len(responses.calls) == 1


def test_recent_processing_attempt_does_not_duplicate_provider_call():
    repository = FakeRepository(_source_rows())
    responses = FakeResponses(_model_output())
    settings = Settings(
        _env_file=None,
        openai_api_key='sk-test-only-012345678901234567890',
        openai_request_timeout_seconds=60,
    )
    _seed_processing_attempt(
        repository,
        settings,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    result = asyncio.run(refresh_ai_calibration(
        901,
        repository=repository,
        db_client=object(),
        openai_client=SimpleNamespace(responses=responses),
        settings=settings,
    ))

    assert result.status == 'pending'
    assert responses.calls == []


def test_processing_attempt_accepts_timestamp_only_base_refresh():
    repository = FakeRepository(_source_rows())
    responses = FakeResponses(_model_output())
    settings = Settings(
        _env_file=None,
        openai_api_key='sk-test-only-012345678901234567890',
        openai_request_timeout_seconds=60,
    )
    _seed_processing_attempt(
        repository,
        settings,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    repository.source['prediction']['updated_at'] = (
        '2026-07-25T10:30:00+00:00'
    )

    result = asyncio.run(refresh_ai_calibration(
        901,
        repository=repository,
        db_client=object(),
        openai_client=SimpleNamespace(responses=responses),
        settings=settings,
    ))

    assert result.status == 'pending'
    assert responses.calls == []
    assert repository.rows[-1]['base_prediction_updated_at'] == (
        '2026-07-25T10:30:00+00:00'
    )


def _stored_updated_calibration(
    source: dict,
    *,
    attempt_number: int,
    published: bool,
    model_label: str,
) -> dict:
    context = build_ai_calibration_input(source)
    normalized = validate_and_normalize_output(
        _model_output(),
        context,
        min_edge_bps=200,
    )
    analysis = output_to_public_analysis(
        normalized,
        context,
    ).model_dump(mode='json')
    analysis['risks'] = [model_label]
    return {
        'id': f'00000000-0000-0000-0000-{attempt_number:012d}',
        'fixture_id': 901,
        'attempt_number': attempt_number,
        'input_hash': f'{attempt_number:064x}',
        'status': 'updated',
        'published': published,
        'model': 'gpt-5.6-sol',
        'reasoning_effort': 'max',
        'prompt_version': ai_calibration_service.PROMPT_VERSION,
        'schema_version': ai_calibration_service.SCHEMA_VERSION,
        'base_prediction_updated_at': source['prediction']['updated_at'],
        'generated_at': '2026-07-25T10:05:00+00:00',
        'analysis': analysis,
    }


def test_updated_unpublished_attempt_never_leaks_through_public_envelope():
    source = _source_rows()
    repository = FakeRepository(source)
    repository.rows.append(_stored_updated_calibration(
        source,
        attempt_number=1,
        published=False,
        model_label='NO PUBLICAR',
    ))

    result = asyncio.run(ai_calibration_service.get_ai_calibration_envelope(
        901,
        repository=repository,
        prediction=source['prediction'],
        settings=Settings(
            _env_file=None,
            openai_api_key='sk-test-only-012345678901234567890',
        ),
    ))

    assert result.status == 'pending'
    assert result.analysis is None


def test_unpublished_attempt_uses_last_published_calibration_as_fallback():
    source = _source_rows()
    repository = FakeRepository(source)
    repository.rows.extend([
        _stored_updated_calibration(
            source,
            attempt_number=1,
            published=True,
            model_label='Calibración publicada',
        ),
        _stored_updated_calibration(
            source,
            attempt_number=2,
            published=False,
            model_label='NO PUBLICAR',
        ),
    ])

    result = asyncio.run(ai_calibration_service.get_ai_calibration_envelope(
        901,
        repository=repository,
        prediction=source['prediction'],
        settings=Settings(
            _env_file=None,
            openai_api_key='sk-test-only-012345678901234567890',
        ),
    ))

    assert result.status == 'updated'
    assert result.analysis is not None
    assert result.analysis.risks == ['Calibración publicada']
    assert result.reason_code == 'calibration_publication_pending'
    assert result.is_stale is True


def test_published_calibration_from_old_prompt_is_marked_stale():
    source = _source_rows()
    repository = FakeRepository(source)
    calibration = _stored_updated_calibration(
        source,
        attempt_number=1,
        published=True,
        model_label='Versión anterior',
    )
    calibration['prompt_version'] = 'football-calibrator-0.9'
    repository.rows.append(calibration)

    result = asyncio.run(ai_calibration_service.get_ai_calibration_envelope(
        901,
        repository=repository,
        prediction=source['prediction'],
        settings=Settings(
            _env_file=None,
            openai_api_key='sk-test-only-012345678901234567890',
        ),
    ))

    assert result.status == 'updated'
    assert result.is_stale is True


def test_pending_analysis_get_enqueues_idempotent_background_refresh(monkeypatch):
    async def pending(_fixture_id):
        return ai_calibration_service.AICalibrationEnvelope(
            fixture_id=901,
            status='pending',
            retry_after_seconds=15,
            reason_code='calibration_pending',
            safe_message='En cola.',
        )

    monkeypatch.setattr(
        prediction_routes,
        'get_ai_calibration_envelope',
        pending,
    )
    monkeypatch.setattr(
        prediction_routes,
        'get_settings',
        lambda: SimpleNamespace(openai_configured=True),
    )
    background = BackgroundTasks()

    response = asyncio.run(prediction_routes.get_analysis(901, background))

    assert response.status == 'pending'
    assert len(background.tasks) == 1
    assert background.tasks[0].func is prediction_routes.refresh_ai_calibration
    assert background.tasks[0].args == (901,)


def test_developer_prompt_requires_neutral_spanish_user_facing_text():
    prompt = ai_calibration_service.DEVELOPER_PROMPT

    assert 'neutral Spanish' in prompt
    assert 'Keep schema keys and enum tokens exactly as defined' in prompt
