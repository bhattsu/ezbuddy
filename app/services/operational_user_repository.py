"""RDS access for ``operational.users`` (login / auth token persistence)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.adapters.data_sources.AIM_rds import RDSRepository, RDSQueryError
from app.utils.sql_queries import get_sql

logger = logging.getLogger(__name__)

SQL_FETCH_USER_BY_EMAIL = get_sql("operational.fetch_user_by_email")
SQL_FETCH_USER_BY_EMAIL_AND_SESSION = get_sql("operational.fetch_user_by_email_and_session")
SQL_UPSERT_USER_ON_LOGIN = get_sql("operational.upsert_user_on_login")
SQL_FETCH_USER_BY_AUTH_TOKEN = get_sql("operational.fetch_user_by_auth_token")
SQL_FETCH_USER_BY_ID = get_sql("operational.fetch_user_by_id")


class OperationalUserRepository:
    """Persist and lookup platform users for filing chat."""

    def __init__(self, rds: Optional[RDSRepository] = None):
        self.rds = rds

    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if self.rds is None:
            return None
        try:
            rows = await self.rds.fetch(SQL_FETCH_USER_BY_EMAIL, email)
            return rows[0] if rows else None
        except RDSQueryError as exc:
            logger.warning("User fetch by email failed: %s", exc)
            return None

    async def get_by_email_and_session(
        self, email: str, session_id: str
    ) -> Optional[Dict[str, Any]]:
        if self.rds is None:
            return None
        try:
            rows = await self.rds.fetch(
                SQL_FETCH_USER_BY_EMAIL_AND_SESSION, email, session_id
            )
            return rows[0] if rows else None
        except RDSQueryError as exc:
            logger.warning("User fetch by email and session failed: %s", exc)
            return None

    async def get_by_auth_token(self, auth_token: str) -> Optional[Dict[str, Any]]:
        if self.rds is None:
            return None
        try:
            rows = await self.rds.fetch(SQL_FETCH_USER_BY_AUTH_TOKEN, auth_token)
            return rows[0] if rows else None
        except RDSQueryError as exc:
            logger.warning("User fetch by auth_token failed: %s", exc)
            return None

    async def get_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        if self.rds is None:
            return None
        try:
            rows = await self.rds.fetch(SQL_FETCH_USER_BY_ID, user_id)
            return rows[0] if rows else None
        except RDSQueryError as exc:
            logger.warning("User fetch by user_id failed: %s", exc)
            return None

    async def upsert_on_login(
        self,
        *,
        user_id: str,
        email: str,
        auth_token: str,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self.rds is None:
            logger.error("RDS not configured; cannot upsert user")
            return None
        try:
            rows = await self.rds.fetch(
                SQL_UPSERT_USER_ON_LOGIN,
                user_id,
                email,
                auth_token,
                session_id,
            )
            return rows[0] if rows else None
        except RDSQueryError as exc:
            logger.warning("User upsert on login failed: %s", exc)
            return None
