"""Optional API authentication via AUTH_METHOD (none | api_key | bearer | jwt)."""

from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False)


def _validate_api_key(api_key: Optional[str]) -> Optional[Any]:
    settings = get_settings()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )
    allowed = [settings.API_KEY] if settings.API_KEY else []
    if settings.API_KEYS:
        allowed.extend(k.strip() for k in settings.API_KEYS.split(",") if k.strip())
    if api_key not in allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return {"auth": "api_key"}


def _validate_bearer(token: str) -> Optional[Any]:
    settings = get_settings()
    if not settings.BEARER_TOKEN or token != settings.BEARER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
    return {"auth": "bearer"}


def _validate_jwt(token: str) -> Optional[Any]:
    try:
        import jwt as pyjwt
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT support not installed (install PyJWT)",
        )
    settings = get_settings()
    if not settings.JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET not configured",
        )
    try:
        payload = pyjwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return {"auth": "jwt", "payload": payload}
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


async def get_optional_auth(
    api_key: Optional[str] = Depends(_api_key_header),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[Any]:
    """
    If AUTH_METHOD is none, return None. Otherwise validate and return identity or raise 401.
    """
    settings = get_settings()
    if settings.AUTH_METHOD == "none":
        return None
    if settings.AUTH_METHOD == "api_key":
        return _validate_api_key(api_key)
    if settings.AUTH_METHOD in ("bearer", "jwt"):
        token = None
        if credentials and credentials.scheme.lower() == "bearer":
            token = credentials.credentials
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Authorization: Bearer token",
            )
        if settings.AUTH_METHOD == "bearer":
            return _validate_bearer(token)
        return _validate_jwt(token)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Unknown AUTH_METHOD: {settings.AUTH_METHOD}",
    )
