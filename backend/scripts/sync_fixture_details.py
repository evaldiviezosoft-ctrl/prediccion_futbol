from __future__ import annotations

import argparse
import asyncio

from backend.scripts._sync_common import close_client, print_summary

from app.core.config import get_settings
from app.services.api_football_client import ApiFootballClient
from app.services.historical_sync_service import HistoricalSyncService
from app.services.supabase_repository import SupabaseRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Continúa detalles de fixtures pendientes.')
    parser.add_argument('--resume', action='store_true', help='Compatibilidad explícita con el comando documentado.')
    parser.add_argument('--competition', action='append', dest='competitions')
    parser.add_argument('--limit', type=int, default=None, help='Máximo de fixtures a procesar en esta ejecución.')
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
        summary = await service.resume_missing_details(
            competitions=args.competitions,
            limit=args.limit,
        )
        print_summary(summary)
    finally:
        await close_client(client)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()

