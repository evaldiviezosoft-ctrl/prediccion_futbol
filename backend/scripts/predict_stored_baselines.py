from __future__ import annotations

import argparse
import asyncio
import json

from backend.scripts._sync_common import BACKEND_ROOT  # noqa: F401 - bootstraps app imports

from app.services.job_service import predict_stored_baselines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Genera predicciones Poisson/Empirical-Bayes usando exclusivamente '
            'fixtures que ya existen en Supabase.'
        )
    )
    parser.add_argument('--days', type=int, default=30, help='Horizonte DB-only, entre 1 y 30 dias.')
    parser.add_argument('--limit', type=int, default=25, help='Maximo de partidos, entre 1 y 100.')
    return parser


async def run(args: argparse.Namespace) -> None:
    summary = await predict_stored_baselines(
        horizon_days=args.days,
        max_matches=args.limit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


def main() -> None:
    asyncio.run(run(build_parser().parse_args()))


if __name__ == '__main__':
    main()
