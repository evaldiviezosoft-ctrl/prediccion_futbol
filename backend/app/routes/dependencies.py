import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings
from app.core.errors import ConfigurationError


def require_admin(x_admin_token: str = Header(default='')) -> None:
    try:
        expected = get_settings().require_admin_token()
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Las rutas administrativas no están configuradas.',
        ) from exc

    provided = x_admin_token.strip()
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token administrativo inválido.')
