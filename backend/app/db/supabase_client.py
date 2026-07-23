from functools import lru_cache
from supabase import Client, create_client
from app.core.config import get_settings
from app.core.errors import ConfigurationError


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()
    url, key = settings.require_supabase_credentials()
    try:
        return create_client(url, key)
    except Exception as exc:
        raise ConfigurationError('Supabase client configuration is invalid.') from exc
