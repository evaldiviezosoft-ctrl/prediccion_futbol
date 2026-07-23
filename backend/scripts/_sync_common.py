from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def print_summary(summary: Any) -> None:
    data = summary.to_dict() if hasattr(summary, 'to_dict') else dict(summary)
    for message in data.pop('messages', []):
        print(message)
    print('\nResumen:')
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


async def close_client(client: Any) -> None:
    close = getattr(client, 'close', None)
    if close is not None:
        result = close()
        if hasattr(result, '__await__'):
            await result

