class BackendError(RuntimeError):
    """Base error with a stable, non-sensitive HTTP representation."""

    status_code = 500
    code = 'backend_error'
    public_detail = 'No se pudo completar la operación.'


class ConfigurationError(BackendError):
    status_code = 503
    code = 'configuration_unavailable'
    public_detail = 'El servicio todavía no está configurado por completo.'


class DatabaseError(BackendError):
    status_code = 503
    code = 'database_unavailable'
    public_detail = 'La base de datos no está disponible temporalmente.'


class ProviderError(BackendError):
    status_code = 502
    code = 'provider_error'
    public_detail = 'El proveedor de datos no respondió correctamente.'


class ProviderConfigurationError(ProviderError):
    status_code = 503
    code = 'provider_configuration_error'
    public_detail = 'El proveedor de datos no está configurado correctamente.'


class ProviderRateLimitError(ProviderError):
    status_code = 429
    code = 'provider_rate_limit'
    public_detail = 'El proveedor de datos alcanzó temporalmente su límite de solicitudes.'


class ProviderAccessRestrictionError(ProviderError):
    """The provider plan does not include the requested resource."""

    status_code = 422
    code = 'provider_access_restricted'
    public_detail = 'El plan del proveedor no permite consultar este recurso.'


class ProviderDateAccessError(ProviderAccessRestrictionError):
    """The provider plan does not include the requested fixture date."""

    code = 'provider_date_access_restricted'
    public_detail = 'El plan del proveedor no permite consultar la fecha solicitada.'


class FixtureNotFoundError(BackendError):
    status_code = 404
    code = 'fixture_not_found'
    public_detail = 'No se encontró el partido solicitado.'


class UnsupportedLeagueError(BackendError):
    status_code = 422
    code = 'unsupported_league'
    public_detail = 'La liga del partido no está habilitada en este MVP.'


class PredictionInputError(BackendError):
    status_code = 422
    code = 'prediction_input_error'
    public_detail = 'No hay datos suficientes para generar esta predicción.'
