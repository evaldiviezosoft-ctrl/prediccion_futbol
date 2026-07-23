from __future__ import annotations

import asyncio
import json

from backend.scripts._sync_common import BACKEND_ROOT  # noqa: F401 - bootstraps app imports

from app.services.supabase_repository import SupabaseRepository


async def run() -> None:
    progress = await SupabaseRepository().sync_progress()
    print(json.dumps(progress, indent=2, ensure_ascii=False, default=str))


def main() -> None:
    asyncio.run(run())


if __name__ == '__main__':
    main()

