from __future__ import annotations

import argparse
import asyncio

from backend.scripts._sync_common import close_client, print_summary

from app.core.config import get_settings
from app.services.api_football_client import ApiFootballClient
from app.services.historical_sync_service import HistoricalSyncService
from app.services.supabase_repository import SupabaseRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Sincroniza fixtures históricos desde API-Football.')
    parser.add_argument('--from-season', type=int, default=2021)
    parser.add_argument('--to-season', type=int, default=2026)
    parser.add_argument('--competition', action='append', dest='competitions')
    parser.add_argument(
        '--basic-only',
        action='store_true',
        help='Guarda listados básicos sin consumir solicitudes de detalle.',
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    repository = SupabaseRepository()
    client = ApiFootballClient(request_log_sink=repository)
    try:
        service = HistoricalSyncService(
            client,
            repository,
            timezone=getattr(settings, 'api_timezone', settings.default_timezone),
        )
        summary = await service.sync(
            from_season=args.from_season,
            to_season=args.to_season,
            competitions=args.competitions,
            include_details=not args.basic_only,
        )
        print_summary(summary)
    finally:
        await close_client(client)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()

