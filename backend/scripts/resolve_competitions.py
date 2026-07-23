from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.api_football_client import ApiFootballClient  # noqa: E402
from app.services.competition_resolver import CompetitionResolver  # noqa: E402
from app.services.rate_limit_manager import RateLimitExhaustedError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Resolve the ten configured competitions against API-Football.',
    )
    parser.add_argument(
        '--include-disabled',
        action='store_true',
        help='Resolve disabled entries too.',
    )
    parser.add_argument(
        '--no-persist',
        action='store_true',
        help='Validate resolution without writing it to Supabase.',
    )
    return parser


def _repository() -> Any:
    from app.services.supabase_repository import SupabaseRepository

    return SupabaseRepository()


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    repository = None if args.no_persist else _repository()
    client = ApiFootballClient(settings=settings, request_log_sink=repository)
    resolver = CompetitionResolver(
        client,
        settings=settings,
        resolution_sink=repository,
    )
    try:
        batch = await resolver.resolve_all(include_disabled=args.include_disabled)
    except RateLimitExhaustedError as exc:
        snapshot = exc.snapshot
        print(
            'Proceso detenido de forma segura: '
            f'{exc.reason}; solicitudes restantes: {snapshot.daily_remaining}'
        )
        return 2
    finally:
        await client.close()

    for item in batch.resolved:
        seasons = ', '.join(str(year) for year in item.available_seasons) or 'ninguna'
        print(
            f'[{item.internal_code}] {item.name} ({item.country}) '
            f'resuelta; temporadas: {seasons}'
        )
    for item in batch.unresolved:
        print(f'[{item.internal_code}] no resuelta: {item.reason}')
    print(
        f'Resumen: {len(batch.resolved)} resueltas, '
        f'{len(batch.unresolved)} no resueltas, '
        f'{client.rate_limit.snapshot.requests_this_run} solicitudes consumidas.'
    )
    return 0 if not batch.unresolved else 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    raise SystemExit(main())
