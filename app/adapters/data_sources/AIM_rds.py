

from __future__ import annotations

"""
Production-ready RDS (asyncpg/PostgreSQL) repository abstraction.

This module is intentionally decoupled from `src/config/settings.py`.
Pass connection params explicitly, or inject an existing `asyncpg.Pool`.
"""

import asyncio
import asyncpg
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple, TypeVar

logger = logging.getLogger(__name__)

__all__ = [
    "RDSRepository",
    "RDSRepositoryError",
    "RDSConnectionError",
    "RDSQueryError",
    "build_dsn",
]

T = TypeVar("T")


class RDSRepositoryError(Exception):
    """Base exception for this module."""


class RDSConnectionError(RDSRepositoryError):
    """Raised when the repository cannot connect to the database."""


class RDSQueryError(RDSRepositoryError):
    """Raised when a query fails and is not retryable."""


def build_dsn(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
) -> str:
    """Build an asyncpg DSN string."""
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


class RDSRepository:
    """Small async repository wrapper around asyncpg connection pooling.

    Notes:
    - Always use parameterized queries for user input.
    - This repository provides retries for common transient Postgres failures
      (deadlocks, serialization failures, and connection errors).
    """

    # Postgres SQLSTATE codes (common retryable ones)
    _SQLSTATE_DEADLOCK = "40P01"
    _SQLSTATE_SERIALIZATION_FAILURE = "40001"
    _SQLSTATE_LOCK_NOT_AVAILABLE = "55P03"

    def __init__(
        self,
        *,
        host: Optional[str] = None,
        port: int = 5432,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        dsn: Optional[str] = None,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        command_timeout: float = 60.0,
        pool: Optional[asyncpg.Pool] = None,
        max_retries: int = 5,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._dsn = dsn
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password

        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.command_timeout = command_timeout

        self._pool: Optional[asyncpg.Pool] = pool

        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool

        if not self._dsn and not all([self._host, self._database, self._user, self._password]):
            raise RDSConnectionError(
                "RDSRepository requires either `dsn` or all of: host, database, user, password"
            )

        try:
            pool_kwargs: Dict[str, Any] = {
                "min_size": self.pool_min_size,
                "max_size": self.pool_max_size,
                "command_timeout": self.command_timeout,
            }
            if self._dsn:
                pool_kwargs["dsn"] = self._dsn
            else:
                pool_kwargs.update(
                    {
                        "host": self._host,
                        "port": self._port,
                        "user": self._user,
                        "password": self._password,
                        "database": self._database,
                    }
                )
            self._pool = await asyncpg.create_pool(**pool_kwargs)
            logger.info("RDSRepository connection pool created (min=%s max=%s)", self.pool_min_size, self.pool_max_size)
            return self._pool
        except Exception as exc:  # noqa: BLE001
            raise RDSConnectionError(f"Failed to create asyncpg pool: {exc}") from exc

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, asyncpg.PostgresConnectionError):
            return True

        sqlstate = getattr(exc, "sqlstate", None)
        return sqlstate in {
            self._SQLSTATE_DEADLOCK,
            self._SQLSTATE_SERIALIZATION_FAILURE,
            self._SQLSTATE_LOCK_NOT_AVAILABLE,
        }

    async def _retry(self, operation: str, fn, *args, **kwargs) -> Any:
        delay = self.retry_backoff_seconds
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                retryable = self._is_retryable(exc)
                if not retryable or attempt == self.max_retries:
                    if retryable:
                        raise RDSQueryError(f"{operation} failed after retries: {exc}") from exc
                    raise RDSQueryError(f"{operation} failed (non-retryable): {exc}") from exc

                logger.warning(
                    "%s retryable failure (attempt %s/%s); sleeping %.1fs: %s",
                    operation,
                    attempt,
                    self.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                delay *= 2

        # Should never hit due to raises above; keep mypy happy.
        raise RDSQueryError(f"{operation} failed: {last_exc}")

    async def fetch(self, query: str, *params: Any) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return all rows as dicts."""
        pool = await self._get_pool()
        return await self._retry(
            "fetch",
            lambda: self._fetch_with_pool(pool, query, params),
        )

    async def _fetch_with_pool(self, pool: asyncpg.Pool, query: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]

    async def fetch_one(self, query: str, *params: Any) -> Optional[Dict[str, Any]]:
        """Execute a SELECT query and return first row or None."""
        pool = await self._get_pool()
        return await self._retry(
            "fetch_one",
            lambda: self._fetch_one_with_pool(pool, query, params),
        )

    async def _fetch_one_with_pool(
        self, pool: asyncpg.Pool, query: str, params: Sequence[Any]
    ) -> Optional[Dict[str, Any]]:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def fetch_one_with_statement_timeout(
        self,
        query: str,
        *params: Any,
        timeout_sec: float = 90.0,
    ) -> Optional[Dict[str, Any]]:
        """Single-row fetch with Postgres statement_timeout for clean server-side cancel."""
        pool = await self._get_pool()
        timeout_ms = max(1000, int(timeout_sec * 1000))

        async def _run() -> Optional[Dict[str, Any]]:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('statement_timeout', $1, true)",
                        str(timeout_ms),
                    )
                    row = await conn.fetchrow(query, *params)
                    return dict(row) if row else None

        return await self._retry(
            "fetch_one_with_statement_timeout",
            _run,
        )

    async def fetch_val(self, query: str, *params: Any) -> Any:
        """Execute a query and return a single scalar value."""
        pool = await self._get_pool()
        return await self._retry(
            "fetch_val",
            lambda: self._fetch_val_with_pool(pool, query, params),
        )

    async def _fetch_val_with_pool(
        self, pool: asyncpg.Pool, query: str, params: Sequence[Any]
    ) -> Any:
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *params)

    async def execute(self, query: str, *params: Any) -> str:
        """Execute INSERT/UPDATE/DELETE/DDL and return asyncpg status string."""
        pool = await self._get_pool()
        return await self._retry(
            "execute",
            lambda: self._execute_with_pool(pool, query, params),
        )

    async def _execute_with_pool(
        self, pool: asyncpg.Pool, query: str, params: Sequence[Any]
    ) -> str:
        async with pool.acquire() as conn:
            return await conn.execute(query, *params)

    async def execute_many(self, query: str, args: Sequence[Sequence[Any]]) -> None:
        """Run the same parameterized statement for many argument sets."""
        pool = await self._get_pool()
        await self._retry(
            "execute_many",
            lambda: self._execute_many_with_pool(pool, query, args),
        )

    async def _execute_many_with_pool(
        self, pool: asyncpg.Pool, query: str, args: Sequence[Sequence[Any]]
    ) -> None:
        async with pool.acquire() as conn:
            await conn.executemany(query, args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """Run multiple statements inside a database transaction.

        Usage:
            async with repo.transaction() as conn:
                await conn.execute("...")
                rows = await conn.fetch("...")
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def check_connection(self) -> bool:
        """Return True if a simple query can be executed."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("RDSRepository connection check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Close the underlying asyncpg pool."""
        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        try:
            await asyncio.wait_for(pool.close(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "RDS pool close timed out after 10s; terminating open connections"
            )
            pool.terminate()
        logger.info("RDSRepository connection pool closed")