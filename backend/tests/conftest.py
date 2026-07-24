import os

import pytest

# Unit tests must never inherit a developer's real credentials or start the scheduler.
for variable in (
    'API_FOOTBALL_KEY',
    'SUPABASE_URL',
    'SUPABASE_SECRET_KEY',
    'SUPABASE_SERVICE_ROLE_KEY',
    'ADMIN_TOKEN',
):
    os.environ[variable] = ''
os.environ['ENABLE_SCHEDULER'] = 'false'

from app.core.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.calendar_visibility import (
    local_profile_team_names,
    local_team_profile_catalog,
)
from app.services.feature_builder import load_profiles
from app.services.model_service import load_bundle


@pytest.fixture(autouse=True)
def clear_cached_dependencies():
    get_settings.cache_clear()
    get_supabase.cache_clear()
    local_profile_team_names.cache_clear()
    local_team_profile_catalog.cache_clear()
    load_profiles.cache_clear()
    load_bundle.cache_clear()
    yield
    get_settings.cache_clear()
    get_supabase.cache_clear()
    local_profile_team_names.cache_clear()
    local_team_profile_catalog.cache_clear()
    load_profiles.cache_clear()
    load_bundle.cache_clear()
