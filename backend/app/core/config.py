from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigurationError


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_PREFIXES = (
    'REEMPLAZAR',
    'CAMBIAR',
    'CHANGE_ME',
    'CHANGEME',
    'PLACEHOLDER',
    'EXAMPLE',
    'TODO',
    'YOUR_',
    'TU_',
)


def _secret_text(value: SecretStr | str | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    text = text.strip()
    return text or None


def _usable_secret(value: SecretStr | str | None, *, min_length: int = 10) -> str | None:
    text = _secret_text(value)
    if not text or len(text) < min_length:
        return None
    normalized = text.upper()
    if normalized.startswith(PLACEHOLDER_PREFIXES):
        return None
    return text


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / '.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    app_name: str = 'Football Predictor API'
    environment: str = 'development'
    host: str = '0.0.0.0'
    port: int = 8000
    cors_origins: str = 'http://localhost:3000'

    api_football_key: SecretStr | None = None
    api_football_base_url: str = 'https://v3.football.api-sports.io'
    api_daily_soft_limit: int = Field(default=90, ge=1)
    api_daily_safety_reserve: int = Field(default=15, ge=0)
    api_max_requests_per_run: int = Field(default=80, ge=1)
    api_delay_seconds: float = Field(default=7.0, ge=0)
    api_request_timeout_seconds: float = Field(default=25.0, gt=0)
    api_retry_max_attempts: int = Field(default=3, ge=1, le=3)
    api_retry_base_delay_seconds: float = Field(default=1.0, ge=0)
    api_retry_max_delay_seconds: float = Field(default=30.0, ge=0)
    api_timezone: str = 'America/Lima'
    upcoming_days: int = Field(default=30, ge=1, le=90)
    competitions_config_path: Path = Path('./app/config/competitions.yaml')

    supabase_url: str | None = None
    supabase_secret_key: SecretStr | None = None
    # Compatibilidad temporal con la clave JWT heredada.
    supabase_service_role_key: SecretStr | None = None
    admin_token: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = 'gpt-5.6-sol'
    openai_reasoning_effort: Literal[
        'none', 'low', 'medium', 'high', 'xhigh', 'max'
    ] = 'high'
    openai_request_timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    openai_max_output_tokens: int = Field(default=6_000, ge=2_000, le=32_000)
    ai_calibration_horizon_days: int = Field(default=14, ge=1, le=30)
    ai_calibration_max_per_cycle: int = Field(default=5, ge=1, le=25)
    ai_calibration_min_edge_bps: int = Field(default=200, ge=0, le=2_000)

    enable_scheduler: bool = False
    scheduler_run_on_startup: bool = False
    max_matches_per_scheduler_cycle: int = Field(default=5, ge=1, le=25)
    scheduler_horizon_days: int = Field(default=7, ge=1, le=30)
    scheduler_prediction_horizon_days: int = Field(default=14, ge=1, le=30)
    scheduler_daily_hour: int = Field(default=0, ge=0, le=23)
    scheduler_daily_minute: int = Field(default=5, ge=0, le=59)
    default_timezone: str = 'America/Lima'
    model_root: Path = Path('./models')
    team_profile_root: Path = Path('./data/team_profiles')

    @field_validator(
        'model_root',
        'team_profile_root',
        'competitions_config_path',
        mode='after',
    )
    @classmethod
    def resolve_backend_path(cls, value: Path) -> Path:
        return value.resolve() if value.is_absolute() else (BACKEND_ROOT / value).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(',') if item.strip()]

    @property
    def api_football_key_value(self) -> str | None:
        return _usable_secret(self.api_football_key)

    @property
    def supabase_backend_key(self) -> str | None:
        return _usable_secret(self.supabase_secret_key, min_length=16) or _usable_secret(
            self.supabase_service_role_key,
            min_length=16,
        )

    @property
    def admin_token_value(self) -> str | None:
        return _usable_secret(self.admin_token, min_length=16)

    @property
    def openai_api_key_value(self) -> str | None:
        return _usable_secret(self.openai_api_key, min_length=20)

    @property
    def api_football_configured(self) -> bool:
        return self.api_football_key_value is not None

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_backend_key)

    @property
    def admin_configured(self) -> bool:
        return self.admin_token_value is not None

    @property
    def openai_configured(self) -> bool:
        return self.openai_api_key_value is not None

    def require_api_football_key(self) -> str:
        value = self.api_football_key_value
        if value is None:
            raise ConfigurationError('API_FOOTBALL_KEY is missing or is a placeholder.')
        return value

    def require_supabase_credentials(self) -> tuple[str, str]:
        if not self.supabase_url or not self.supabase_backend_key:
            raise ConfigurationError('Supabase backend credentials are missing or invalid.')
        return self.supabase_url, self.supabase_backend_key

    def require_admin_token(self) -> str:
        value = self.admin_token_value
        if value is None:
            raise ConfigurationError('ADMIN_TOKEN is missing or is a placeholder.')
        return value

    def require_openai_api_key(self) -> str:
        value = self.openai_api_key_value
        if value is None:
            raise ConfigurationError('OPENAI_API_KEY is missing or is a placeholder.')
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
