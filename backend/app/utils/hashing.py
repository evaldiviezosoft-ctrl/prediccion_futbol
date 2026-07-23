from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


_SENSITIVE_KEY_PARTS = ('api_key', 'apikey', 'authorization', 'secret', 'token', 'x-apisports-key')
_REDACTED = '[REDACTED]'


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r'[^a-z0-9]+', '_', str(key).strip().lower()).strip('_')
    if normalized == 'key':
        return True
    return any(part.replace('-', '_') in normalized for part in _SENSITIVE_KEY_PARTS)


def sanitize_for_logging(value: Any) -> Any:
    """Return a JSON-safe value with credential-like fields removed."""

    if is_dataclass(value):
        value = asdict(value)
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _is_sensitive_key(key) else sanitize_for_logging(item)
            for key, item in value.items()
        }
    if isinstance(value, set | frozenset):
        normalized = [sanitize_for_logging(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [sanitize_for_logging(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        sanitize_for_logging(value),
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )


def sha256_json(value: Any) -> str:
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def request_hash(endpoint: str, parameters: Mapping[str, Any] | None = None) -> str:
    normalized_endpoint = '/' + endpoint.strip().lstrip('/')
    return sha256_json(
        {
            'endpoint': normalized_endpoint,
            'parameters': dict(parameters or {}),
        }
    )


def response_hash(payload: Any) -> str:
    return sha256_json(payload)
