"""Optional rate limiting with slowapi; key by API key or client IP."""

from starlette.requests import Request

from app.config.settings import get_settings
from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_limit_key(request: Request) -> str:
    settings = get_settings()
    if settings.AUTH_METHOD == "api_key":
        key = request.headers.get("X-API-Key")
        if key:
            return f"apikey:{key}"
    addr = get_remote_address(request)
    return f"ip:{addr}"


def _get_limit_string() -> str:
    s = get_settings()
    if s.RATE_LIMIT_ENABLED:
        return f"{s.RATE_LIMIT_PER_MINUTE}/minute"
    return "99999/minute"


limiter = Limiter(key_func=_rate_limit_key)
rate_limit_string = _get_limit_string()
