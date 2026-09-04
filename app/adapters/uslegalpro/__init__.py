"""US Legal Pro external API adapter."""

from app.adapters.uslegalpro.tokens import parse_auth_token, resolve_auth_token

__all__ = ["parse_auth_token", "resolve_auth_token", "USLegalProClient"]


def __getattr__(name: str):
    if name == "USLegalProClient":
        from app.adapters.uslegalpro.client import USLegalProClient
        return USLegalProClient
    raise AttributeError(name)
