from __future__ import annotations

import argparse
import asyncio

from backend.scripts._sync_common import close_client, print_summary

from app.core.config import get_settings
from app.services.api_football_client import ApiFootballClient
from app.services.historical_sync_service import HistoricalSyncService
from app.services.rate_limit_manager import RateLimitManager
from app.services.supabase_repository import SupabaseRepository


TARGET_COMPETITIONS = ('brazil_serie_a', 'argentina_liga_profesional')
COMPETITION_ALIASES = {
    'brazil': 'brazil_serie_a',
    'argentina': 'argentina_liga_profesional',
    'brazil_serie_a': 'brazil_serie_a',
    'argentina_liga_profesional': 'argentina_liga_profesional',
}
MAX_FIXTURES_PER_PROVIDER_REQUEST = 20


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError('el valor debe ser mayor que cero')
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Completa estadísticas históricas ya almacenadas de Brasil y Argentina. '
            'Sin --execute solo genera un plan de lectura.'
        )
    )
    parser.add_argument(
        '--competition',
        action='append',
        dest='competitions',
        choices=tuple(COMPETITION_ALIASES),
        help='Por defecto procesa ambas competiciones permitidas.',
    )
    parser.add_argument(
        '--max-requests',
        type=_positive_int,
        required=True,
        help='Tope estricto de solicitudes HTTP reales, incluidos reintentos.',
    )
    parser.add_argument(
        '--max-fixtures',
        type=_positive_int,
        default=None,
        help='Tope adicional de fixtures candidatos (máximo 20 por solicitud).',
    )
    parser.add_argument(
        '--max-attempts',
        type=_positive_int,
        default=3,
        help='No vuelve a elegir un fixture después de esta cantidad de fallos.',
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Autoriza las llamadas API y los upserts. Sin esta bandera no escribe.',
    )
    parser.add_argument(
        '--allow-batches',
        action='store_true',
        help=(
            'Opt-in para usar hasta 20 IDs por solicitud. El plan actual no lo admite; '
            'por defecto cada solicitud procesa exactamente un fixture.'
        ),
    )
    return parser


def _fixture_limit(args: argparse.Namespace) -> int:
    fixtures_per_request = (
        MAX_FIXTURES_PER_PROVIDER_REQUEST if args.allow_batches else 1
    )
    request_capacity = int(args.max_requests) * fixtures_per_request
    configured = args.max_fixtures
    return min(int(configured), request_capacity) if configured else request_capacity


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    repository = SupabaseRepository()
    competitions = tuple(dict.fromkeys(
        COMPETITION_ALIASES[value]
        for value in (args.competitions or TARGET_COMPETITIONS)
    ))
    fixture_limit = _fixture_limit(args)

    if not args.execute:
        targets = await repository.list_enabled_competitions(competitions)
        found = {str(row['internal_code']) for row in targets}
        missing = sorted(set(competitions) - found)
        if missing:
            raise ValueError(f'Competiciones desconocidas o deshabilitadas: {", ".join(missing)}')
        pending = await repository.list_pending_market_fixture_details(
            competition_ids=[int(row['id']) for row in targets],
            limit=fixture_limit,
            max_attempts=int(args.max_attempts),
        )
        print_summary({
            'mode': 'plan_only',
            'competitions': list(competitions),
            'max_requests': int(args.max_requests),
            'max_fixtures': fixture_limit,
            'request_mode': 'batch_opt_in' if args.allow_batches else 'singular',
            'fixtures_selected': len(pending),
            'priority_current_teams': sum(
                bool(row.get('priority_current_team')) for row in pending
            ),
            'fixture_ids': [int(row['api_fixture_id']) for row in pending],
            'messages': [
                'Plan de solo lectura: no se construyó el cliente API y no se escribió en Supabase.'
            ],
        })
        return

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
            timezone=getattr(settings, 'api_timezone', settings.default_timezone),
        )
        summary = await service.backfill_market_statistics(
            competitions=competitions,
            max_fixtures=fixture_limit,
            max_attempts=int(args.max_attempts),
            force_singular_details=not args.allow_batches,
        )
        print_summary(summary)
    finally:
        await close_client(client)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()
