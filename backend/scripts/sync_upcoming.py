from __future__ import annotations

import argparse
import asyncio

from backend.scripts._sync_common import close_client, print_summary

from app.core.config import get_settings
from app.services.api_football_client import ApiFootballClient
from app.services.supabase_repository import SupabaseRepository
from app.services.upcoming_sync_service import OptionalUpcomingData, UpcomingSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Sincroniza próximos partidos desde API-Football.')
    parser.add_argument('--days', type=int, default=None)
    parser.add_argument('--competition', action='append', dest='competitions')
    parser.add_argument('--with-injuries', action='store_true')
    parser.add_argument('--with-odds', action='store_true')
    parser.add_argument('--with-external-predictions', action='store_true')
    parser.add_argument('--with-lineups', action='store_true')
    return parser


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    repository = SupabaseRepository()
    client = ApiFootballClient(request_log_sink=repository)
    try:
        service = UpcomingSyncService(
            client,
            repository,
            timezone_name=getattr(settings, 'api_timezone', settings.default_timezone),
        )
        summary = await service.sync(
            days=args.days or getattr(settings, 'upcoming_days', 30),
            competitions=args.competitions,
            optional=OptionalUpcomingData(
                injuries=args.with_injuries,
                odds=args.with_odds,
                external_predictions=args.with_external_predictions,
                lineups=args.with_lineups,
            ),
        )
        print_summary(summary)
    finally:
        await close_client(client)


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()
