from __future__ import annotations

import argparse
import asyncio

from backend.scripts._sync_common import close_client, print_summary

from app.core.config import get_settings
from app.services.api_football_client import ApiFootballClient
from app.services.historical_sync_service import (
    HistoricalSyncService,
    MAX_TEAM_BACKFILL_TARGETS,
)
from app.services.rate_limit_manager import RateLimitManager
from app.services.supabase_repository import SupabaseRepository


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError('el valor debe ser mayor que cero')
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Completa país y metadatos de equipos concretos mediante /teams?id=. '
            'No forma parte del backfill histórico salvo autorización explícita.'
        )
    )
    parser.add_argument(
        '--team-id',
        action='append',
        type=_positive_int,
        required=True,
        dest='team_ids',
    )
    parser.add_argument(
        '--max-requests',
        type=_positive_int,
        required=True,
        help='Tope estricto de solicitudes HTTP reales, incluidos reintentos.',
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Autoriza llamadas a API-Football y upserts en Supabase.',
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    team_ids = tuple(dict.fromkeys(int(value) for value in args.team_ids))
    if len(team_ids) > MAX_TEAM_BACKFILL_TARGETS:
        raise ValueError(
            f'No se pueden procesar más de {MAX_TEAM_BACKFILL_TARGETS} equipos.'
        )
    if not args.execute:
        print_summary({
            'mode': 'plan_only',
            'team_ids': list(team_ids),
            'metadata_requests_planned': len(team_ids),
            'max_requests': int(args.max_requests),
            'messages': [
                'Plan local: no se construyó el cliente API ni se escribió en Supabase.'
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
        summary = await service.backfill_team_metadata(team_ids=team_ids)
        print_summary(summary)
    finally:
        await close_client(client)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()
