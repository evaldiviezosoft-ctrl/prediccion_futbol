from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.supabase_client import get_supabase
from app.services.feature_builder import load_profiles
from app.services.fixture_service import LEAGUE_ID_TO_CODE
from app.services.fixture_service import validate_timezone

router = APIRouter(tags=['health'])


@router.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@router.get('/health/live')
def live() -> dict[str, str]:
    return {'status': 'alive'}


def _artifacts_ready() -> tuple[bool, bool]:
    settings = get_settings()
    models_ready = True
    profiles_ready = True
    for league_code in LEAGUE_ID_TO_CODE.values():
        model_path = settings.model_root / league_code / 'model_bundle.joblib'
        try:
            model_exists = model_path.is_file() and model_path.stat().st_size > 0
        except OSError:
            model_exists = False
        if not model_exists:
            models_ready = False
        try:
            profiles = load_profiles(league_code)
            if not profiles:
                profiles_ready = False
        except Exception:
            profiles_ready = False
    return models_ready, profiles_ready


@router.get('/health/ready')
def ready() -> JSONResponse:
    settings = get_settings()
    models_ready, profiles_ready = _artifacts_ready()
    database_ready = False
    if settings.supabase_configured:
        try:
            get_supabase().table('leagues').select('id').limit(1).execute()
            database_ready = True
        except Exception:
            database_ready = False
    try:
        validate_timezone(settings.default_timezone)
        timezone_ready = True
    except Exception:
        timezone_ready = False

    checks = {
        'api_football_configured': settings.api_football_configured,
        'supabase_configured': settings.supabase_configured,
        'admin_configured': settings.admin_configured,
        'timezone': timezone_ready,
        'database': database_ready,
        'models': models_ready,
        'team_profiles': profiles_ready,
    }
    is_ready = all(checks.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={'status': 'ready' if is_ready else 'not_ready', 'checks': checks},
    )
