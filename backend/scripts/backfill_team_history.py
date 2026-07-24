from __future__ import annotations

import argparse
import asyncio

from backend.scripts._sync_common import close_client, print_summary

from app.core.config import get_settings
from app.services.api_football_client import ApiFootballClient
from app.services.historical_sync_service import (
    HistoricalSyncService,
    MAX_FIXTURE_IDS_PER_REQUEST,
    MAX_TEAM_BACKFILL_TARGETS,
    MAX_TEAM_DETAIL_FIXTURES,
    MAX_TEAM_HISTORY_FIXTURES,
)
from app.services.rate_limit_manager import RateLimitManager
from app.services.supabase_repository import SupabaseRepository


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError('el valor debe ser mayor que cero')
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('el valor no puede ser negativo')
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Carga historial reciente por team_id sin descargar ligas completas. '
            'Sin --execute solo muestra el presupuesto del plan.'
        )
    )
    parser.add_argument(
        '--team-id',
        action='append',
        type=_positive_int,
        required=True,
        dest='team_ids',
        help='ID de API-Football; puede repetirse para incluir varios clubes.',
    )
    parser.add_argument(
        '--max-requests',
        type=_positive_int,
        required=True,
        help='Tope estricto de solicitudes HTTP reales, incluidos reintentos.',
    )
    parser.add_argument(
        '--max-fixtures-per-team',
        type=_positive_int,
        default=20,
        help='Cantidad reciente solicitada por club (máximo 100).',
    )
    parser.add_argument(
        '--season',
        type=_positive_int,
        default=None,
        help=(
            'Temporada concreta para planes que no permiten el parámetro last; '
            'los resultados se acotan localmente a --max-fixtures-per-team.'
        ),
    )
    parser.add_argument(
        '--max-detail-fixtures',
        type=_nonnegative_int,
        default=None,
        help=(
            'Tope de fixtures con estadísticas; además queda limitado por '
            '--max-requests.'
        ),
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Autoriza llamadas a API-Football y upserts en Supabase.',
    )
    parser.add_argument(
        '--with-team-metadata',
        action='store_true',
        help=(
            'Antes del historial, consulta /teams?id=... para cada club y '
            'completa país y otros metadatos. Reserva una solicitud adicional '
            'por equipo y solo se ejecuta junto con --execute.'
        ),
    )
    parser.add_argument(
        '--force-singular-details',
        action='store_true',
        help=(
            'Desactiva lotes de detalles. Por defecto prueba hasta 20 IDs y '
            'cambia automáticamente a solicitudes individuales si el plan los rechaza.'
        ),
    )
    return parser


def _unique_team_ids(args: argparse.Namespace) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(value) for value in args.team_ids))


def _detail_limit(args: argparse.Namespace) -> int:
    team_count = len(_unique_team_ids(args))
    base_requests_per_team = 2 if args.with_team_metadata else 1
    remaining_request_capacity = max(
        0,
        int(args.max_requests) - (team_count * base_requests_per_team),
    )
    fixtures_per_request = (
        1 if args.force_singular_details else MAX_FIXTURE_IDS_PER_REQUEST
    )
    quota_bound = remaining_request_capacity * fixtures_per_request
    requested = args.max_detail_fixtures
    if requested is not None:
        quota_bound = min(quota_bound, int(requested))
    return min(quota_bound, MAX_TEAM_DETAIL_FIXTURES)


def _validate_bounds(args: argparse.Namespace) -> None:
    team_count = len(_unique_team_ids(args))
    if team_count > MAX_TEAM_BACKFILL_TARGETS:
        raise ValueError(
            f'No se pueden procesar más de {MAX_TEAM_BACKFILL_TARGETS} equipos.'
        )
    if int(args.max_fixtures_per_team) > MAX_TEAM_HISTORY_FIXTURES:
        raise ValueError(
            f'--max-fixtures-per-team no puede superar '
            f'{MAX_TEAM_HISTORY_FIXTURES}.'
        )
    if (
        args.max_detail_fixtures is not None
        and int(args.max_detail_fixtures) > MAX_TEAM_DETAIL_FIXTURES
    ):
        raise ValueError(
            f'--max-detail-fixtures no puede superar '
            f'{MAX_TEAM_DETAIL_FIXTURES}.'
        )
    minimum_requests = team_count * 2
    if args.with_team_metadata and int(args.max_requests) < minimum_requests:
        raise ValueError(
            '--with-team-metadata requiere al menos dos solicitudes nominales '
            f'por equipo; usa --max-requests {minimum_requests} o más.'
        )


def _combined_summary(metadata_summary: object, history_summary: object) -> dict:
    def summary_data(value: object) -> dict:
        if hasattr(value, 'to_dict'):
            return dict(value.to_dict())
        return dict(value)

    metadata = summary_data(metadata_summary)
    history = summary_data(history_summary)
    metadata_messages = metadata.pop('messages', [])
    history_messages = history.pop('messages', [])
    return {
        'mode': 'executed_with_team_metadata',
        'metadata': metadata,
        'history': history,
        'messages': [
            'La metadata se solicitó antes del historial para priorizar país y '
            'la identidad del club.',
            *metadata_messages,
            *history_messages,
        ],
    }


async def run(args: argparse.Namespace) -> None:
    _validate_bounds(args)
    team_ids = _unique_team_ids(args)
    detail_limit = _detail_limit(args)
    if not args.execute:
        metadata_requests = len(team_ids) if args.with_team_metadata else 0
        print_summary({
            'mode': 'plan_only',
            'team_ids': list(team_ids),
            'team_fixture_requests': len(team_ids),
            'metadata_requests_planned': metadata_requests,
            'minimum_requests_planned': len(team_ids) + metadata_requests,
            'max_requests': int(args.max_requests),
            'max_fixtures_per_team': int(args.max_fixtures_per_team),
            'season': args.season,
            'max_detail_fixtures': detail_limit,
            'request_mode': (
                'singular_forced'
                if args.force_singular_details
                else 'batch_with_automatic_singular_fallback'
            ),
            'messages': [
                'Plan local: no se construyó el cliente API ni se escribió en Supabase.',
                (
                    'Cada team_id consume al menos una solicitud; reintentos HTTP '
                    'también cuentan contra el tope.'
                ),
                (
                    'El país solo se intenta completar cuando se incluye '
                    '--with-team-metadata; el proveedor puede no informar ese '
                    'campo para algún club.'
                ),
            ],
        })
        return

    settings = get_settings()
    repository = SupabaseRepository()
    rate_limit = RateLimitManager(
        daily_safety_reserve=settings.api_daily_safety_reserve,
        max_requests_per_run=int(args.max_requests),
    )
    client = ApiFootballClient(
        settings=settings,
        rate_limit=rate_limit,
        request_log_sink=repository,
    )
    try:
        service = HistoricalSyncService(
            client,
            repository,
            timezone=getattr(
                settings,
                'api_timezone',
                settings.default_timezone,
            ),
        )
        metadata_summary = None
        if args.with_team_metadata:
            metadata_summary = await service.backfill_team_metadata(
                team_ids=team_ids,
            )
        history_summary = await service.backfill_team_history(
            team_ids=team_ids,
            max_fixtures_per_team=int(args.max_fixtures_per_team),
            max_detail_fixtures=detail_limit,
            force_singular_details=bool(args.force_singular_details),
            season=args.season,
        )
        if metadata_summary is None:
            print_summary(history_summary)
        else:
            print_summary(_combined_summary(metadata_summary, history_summary))
    finally:
        await close_client(client)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()
