from app.core.config import BACKEND_ROOT, Settings


def test_modern_supabase_secret_takes_precedence_and_paths_are_backend_relative():
    settings = Settings(
        _env_file=None,
        supabase_url='https://example.supabase.co',
        supabase_secret_key='sb_secret_modern_0123456789',
        supabase_service_role_key='legacy_service_role_0123456789',
        model_root='./models',
        team_profile_root='./data/team_profiles',
    )

    assert settings.supabase_backend_key == 'sb_secret_modern_0123456789'
    assert settings.model_root == (BACKEND_ROOT / 'models').resolve()
    assert settings.team_profile_root == (BACKEND_ROOT / 'data/team_profiles').resolve()


def test_legacy_supabase_key_is_a_fallback():
    settings = Settings(
        _env_file=None,
        supabase_url='https://example.supabase.co',
        supabase_secret_key='REEMPLAZAR_SOLO_BACKEND',
        supabase_service_role_key='legacy_service_role_0123456789',
    )

    assert settings.supabase_backend_key == 'legacy_service_role_0123456789'
    assert settings.supabase_configured is True


def test_missing_and_placeholder_admin_tokens_are_never_configured():
    assert Settings(_env_file=None).admin_configured is False
    assert Settings(_env_file=None, admin_token='CAMBIAR_POR_UN_TOKEN_LARGO').admin_configured is False
    assert Settings(_env_file=None, admin_token='PLACEHOLDER_ADMIN_TOKEN_123').admin_configured is False


def test_secret_values_are_masked_in_settings_representation():
    secret = 'sb_secret_do_not_render_0123456789'
    settings = Settings(_env_file=None, supabase_secret_key=secret)

    assert secret not in repr(settings)


def test_postmatch_evaluation_defaults_are_bounded():
    settings = Settings(_env_file=None)

    assert settings.postmatch_lookback_days == 7
    assert settings.postmatch_max_matches == 100
    assert settings.postmatch_poll_interval_minutes == 30


def test_safe_retention_defaults_preserve_five_years_and_run_weekly():
    settings = Settings(_env_file=None)

    assert settings.retention_enabled is True
    assert settings.retention_dry_run is False
    assert settings.retention_raw_payload_days == 1825
    assert settings.retention_api_log_days == 90
    assert settings.retention_fixture_batch_size == 500
    assert settings.retention_api_log_batch_size == 5000
    assert settings.retention_max_batches == 10
    assert (settings.retention_weekday, settings.retention_hour, settings.retention_minute) == (
        6,
        3,
        30,
    )
