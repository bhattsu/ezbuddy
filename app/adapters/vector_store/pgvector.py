"""
PostgreSQL + pgvector Vector Store Adapter

This module provides the pgvector implementation of the VectorStoreAdapter interface.
"""

import json
import logging
from typing import List, Dict, Any, Optional

from app.adapters.vector_store.base import (
    VectorStoreAdapter,
    VectorStoreConfig,
    SearchResult,
    DistanceMetric,
)
from app.config.registry import register_adapter

logger = logging.getLogger(__name__)


def _get_distance_operator(metric: DistanceMetric) -> str:
    """Get pgvector distance operator for metric."""
    mapping = {
        DistanceMetric.COSINE: "<=>",  # Cosine distance
        DistanceMetric.EUCLIDEAN: "<->",  # L2 distance
        DistanceMetric.DOT_PRODUCT: "<#>",  # Negative inner product
    }
    result = mapping.get(metric, "<=>")
    logger.debug(f"Mapping DistanceMetric '{metric}' to pgvector operator '{result}'")
    return result


def _get_index_method(metric: DistanceMetric) -> str:
    """Get pgvector index method for metric."""
    mapping = {
        DistanceMetric.COSINE: "vector_cosine_ops",
        DistanceMetric.EUCLIDEAN: "vector_l2_ops",
        DistanceMetric.DOT_PRODUCT: "vector_ip_ops",
    }
    result = mapping.get(metric, "vector_cosine_ops")
    logger.debug(f"Mapping DistanceMetric '{metric}' to pgvector index method '{result}'")
    return result


@register_adapter("pgvector")
class PgVectorAdapter(VectorStoreAdapter):
    """
    PostgreSQL + pgvector adapter.
    
    pgvector is an open-source extension for PostgreSQL that adds
    support for vector similarity search.
    """

    def __init__(self, config: VectorStoreConfig):
        """Initialize the pgvector adapter."""
        logger.debug(f"Initializing PgVectorAdapter with config: {config}")
        super().__init__(config)
        self._pool = None
        self._schema = self.config.extra_params.get("schema", "public")

    async def connect(self) -> None:
        """Establish connection to PostgreSQL."""
        try:
            import asyncpg
            
            # Build connection string
            user = self.config.extra_params.get("user", "postgres")
            password = self.config.api_key or self.config.extra_params.get("password", "")
            database = self.config.extra_params.get("database", "vectors")

            logger.info(
                f"Attempting to connect to PostgreSQL at host={self.config.host}, port={self.config.port}, db={database}, user={user}"
            )
            
            self._pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                user=user,
                password=password,
                database=database,
                min_size=self.config.extra_params.get("min_pool_size", 2),
                max_size=self.config.extra_params.get("max_pool_size", 10),
            )
            
            # Ensure pgvector extension is enabled and schema exists
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                logger.debug("Ensured pgvector extension is enabled.")
                
                # Create schema if it doesn't exist (and it's not 'public')
                if self._schema and self._schema != "public":
                    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
                    logger.info(f"Ensured schema '{self._schema}' exists.")
            
            logger.info(f"Connected to PostgreSQL at {self.config.host}:{self.config.port} (schema: {self._schema})")
        except ImportError:
            logger.error("asyncpg is not installed, cannot connect to PostgreSQL.")
            raise ImportError("asyncpg is required for pgvector adapter. Install with: pip install asyncpg")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise ConnectionError(f"Failed to connect to PostgreSQL: {e}")

    async def disconnect(self) -> None:
        """Close connection to PostgreSQL."""
        if self._pool:
            logger.info("Attempting to disconnect from PostgreSQL...")
            await self._pool.close()
            self._pool = None
            logger.info("Disconnected from PostgreSQL")

    def _get_table_name(self, collection_name: str) -> str:
        """Get schema-qualified table name."""
        if self._schema and self._schema != "public":
            return f'"{self._schema}"."{collection_name}"'
        return f'"{collection_name}"'
    
    async def create_collection(
        self,
        collection_name: str,
        vector_dim: int,
        distance_metric: DistanceMetric = DistanceMetric.COSINE,
        **kwargs
    ) -> bool:
        """Create a new table for vectors in PostgreSQL."""
        table_name = self._get_table_name(collection_name)
        logger.info(f"Creating collection (table) '{table_name}' with vector_dim={vector_dim} and distance_metric={distance_metric}")
        try:
            async with self._pool.acquire() as conn:
                # Create table
                logger.debug(f"Executing CREATE TABLE IF NOT EXISTS for '{table_name}'")
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id VARCHAR(256) PRIMARY KEY,
                        embedding vector({vector_dim}),
                        metadata JSONB,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Create index
                index_method = _get_index_method(distance_metric)
                index_name = f"{collection_name}_embedding_idx"
                logger.debug(f"Creating index '{index_name}' USING ivfflat with method '{index_method}'")
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS "{index_name}"
                    ON {table_name}
                    USING ivfflat (embedding {index_method})
                    WITH (lists = {kwargs.get('lists', 100)})
                """)
            
            logger.info(f"Created pgvector table: {table_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to create table {table_name}: {e}")
            return False

    async def delete_collection(self, collection_name: str) -> bool:
        """Delete a table from PostgreSQL."""
        table_name = self._get_table_name(collection_name)
        logger.info(f"Deleting collection (table) '{table_name}'")
        try:
            async with self._pool.acquire() as conn:
                logger.debug(f"Executing DROP TABLE IF EXISTS for '{table_name}'")
                await conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            logger.info(f"Deleted pgvector table: {table_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete table {table_name}: {e}")
            return False

    async def collection_exists(self, collection_name: str) -> bool:
        """Check if a table exists in PostgreSQL."""
        table_name = self._get_table_name(collection_name)
        logger.debug(f"Checking if collection (table) '{table_name}' exists")
        try:
            async with self._pool.acquire() as conn:
                if self._schema and self._schema != "public":
                    result = await conn.fetchval("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = $1 AND table_name = $2
                        )
                    """, self._schema, collection_name)
                else:
                    result = await conn.fetchval("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = 'public' AND table_name = $1
                        )
                    """, collection_name)
                logger.debug(f"Collection '{table_name}' exists: {result}")
                return result
        except Exception as e:
            logger.error(f"Error checking table existence: {e}")
            return False

    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        metadata: List[Dict[str, Any]],
        **kwargs
    ) -> int:
        """Insert or update vectors in PostgreSQL."""
        table_name = self._get_table_name(collection_name)
        logger.info(f"Upsert called on collection '{table_name}' for {len(ids)} vectors")
        try:
            async with self._pool.acquire() as conn:
                count = 0
                for id_val, embedding, meta in zip(ids, embeddings, metadata):
                    embedding_str = f"[{','.join(map(str, embedding))}]"
                    logger.debug(f"Upserting vector id='{id_val}' in collection '{table_name}'")
                    await conn.execute(f"""
                        INSERT INTO {table_name} (id, embedding, metadata)
                        VALUES ($1, $2::vector, $3::jsonb)
                        ON CONFLICT (id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata
                    """, id_val, embedding_str, json.dumps(meta))
                    count += 1
            
            logger.info(f"Upserted {count} vectors to {table_name}")
            return count
        except Exception as e:
            logger.error(f"Failed to upsert vectors: {e}")
            return 0

    async def query(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        include_vectors: bool = False,
        **kwargs
    ) -> List[SearchResult]:
        """Query PostgreSQL for similar vectors."""
        logger.info(f"Querying collection '{collection_name}' with top_k={top_k}, include_vectors={include_vectors}, filters={filters}")
        try:
            distance_metric = kwargs.get("distance_metric", DistanceMetric.COSINE)
            operator = _get_distance_operator(distance_metric)
            
            # Build query
            vector_str = f"[{','.join(map(str, query_vector))}]"
            select_fields = "id, metadata"
            if include_vectors:
                select_fields += ", embedding"
            
            # Build filter clause
            filter_clause = ""
            if filters:
                conditions = []
                for key, value in filters.items():
                    # Skip None, empty dict, or empty list values
                    if value is None or value == {} or value == []:
                        logger.debug(f"Skipping filter key '{key}' with empty/None value: {value}")
                        continue
                    
                    # Handle different value types
                    if isinstance(value, str):
                        # Escape single quotes in string values
                        escaped_value = value.replace("'", "''")
                        logger.debug(f"Adding filter for {key}={value} (string match)")
                        conditions.append(f"metadata->>'{key}' = '{escaped_value}'")
                    elif isinstance(value, bool):
                        logger.debug(f"Adding filter for {key}={value} (boolean match)")
                        conditions.append(f"(metadata->>'{key}')::boolean = {str(value).lower()}")
                    elif isinstance(value, (int, float)):
                        logger.debug(f"Adding filter for {key}={value} (numeric match)")
                        conditions.append(f"(metadata->>'{key}')::numeric = {value}")
                    elif isinstance(value, list):
                        # Handle list values (IN operator)
                        if len(value) > 0:
                            if all(isinstance(v, str) for v in value):
                                # String list
                                escaped_values = [v.replace("'", "''") for v in value]
                                values_str = ', '.join([f"'{v}'" for v in escaped_values])
                                conditions.append(f"metadata->>'{key}' IN ({values_str})")
                            else:
                                # Numeric list
                                values_str = ', '.join([str(v) for v in value])
                                conditions.append(f"(metadata->>'{key}')::numeric IN ({values_str})")
                    else:
                        logger.warning(f"Unsupported filter value type for key '{key}': {type(value)}, value: {value}")
                        continue
                
                if conditions:
                    filter_clause = "WHERE " + " AND ".join(conditions)
                else:
                    logger.debug("No valid filter conditions after processing, skipping filter")
            
            table_name = self._get_table_name(collection_name)
            query = f"""
                SELECT {select_fields}, 1 - (embedding {operator} $1::vector) as score
                FROM {table_name}
                {filter_clause}
                ORDER BY embedding {operator} $1::vector
                LIMIT {top_k}
            """
            logger.debug(f"Executing vector search query:\n{query.strip()}")
            
            async with self._pool.acquire() as conn:
                results = await conn.fetch(query, vector_str)
            
            search_results = [
                SearchResult(
                    id=str(row["id"]),
                    score=float(row["score"]) if row["score"] else 0.0,
                    payload=json.loads(row["metadata"]) if row["metadata"] else {},
                    vector=list(row["embedding"]) if include_vectors and row.get("embedding") else None,
                )
                for row in results
            ]
            
            logger.info(f"Query returned {len(search_results)} results from '{collection_name}'")
            logger.debug(f"Query result ids: {[r.id for r in search_results]}")
            return search_results
        except Exception as e:
            logger.error(f"Failed to query vectors: {e}")
            return []

    async def delete(
        self,
        collection_name: str,
        ids: List[str]
    ) -> bool:
        """Delete vectors by ID from PostgreSQL."""
        table_name = self._get_table_name(collection_name)
        logger.info(f"Deleting {len(ids)} vectors by ID from collection '{table_name}'")
        try:
            async with self._pool.acquire() as conn:
                logger.debug(f"Executing DELETE FROM {table_name} WHERE id IN {ids}")
                await conn.execute(f"""
                    DELETE FROM {table_name}
                    WHERE id = ANY($1)
                """, ids)
            logger.info(f"Deleted {len(ids)} vectors from {table_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete vectors: {e}")
            return False

    async def get_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        include_vectors: bool = False
    ) -> List[SearchResult]:
        """Retrieve vectors by ID from PostgreSQL."""
        table_name = self._get_table_name(collection_name)
        logger.info(f"Getting {len(ids)} vectors by id from collection '{table_name}' (include_vectors={include_vectors})")
        try:
            select_fields = "id, metadata"
            if include_vectors:
                select_fields += ", embedding"
            
            async with self._pool.acquire() as conn:
                logger.debug(f"Executing SELECT for ids {ids} in '{table_name}'")
                results = await conn.fetch(f"""
                    SELECT {select_fields}
                    FROM {table_name}
                    WHERE id = ANY($1)
                """, ids)
            
            logger.info(f"Retrieved {len(results)} vectors from '{collection_name}' by ids")
            return [
                SearchResult(
                    id=str(row["id"]),
                    score=1.0,
                    payload=json.loads(row["metadata"]) if row["metadata"] else {},
                    vector=list(row["embedding"]) if include_vectors and row.get("embedding") else None,
                )
                for row in results
            ]
        except Exception as e:
            logger.error(f"Failed to retrieve vectors by ID: {e}")
            return []

    async def count(self, collection_name: str) -> int:
        """Get the number of vectors in a table."""
        table_name = self._get_table_name(collection_name)
        logger.info(f"Getting count of vectors in collection '{table_name}'")
        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")
                logger.debug(f"Count from collection '{table_name}' is {result}")
                return result or 0
        except Exception as e:
            logger.error(f"Failed to get table count: {e}")
            return 0

    async def health_check(self) -> bool:
        """Check if PostgreSQL is healthy."""
        logger.info("Running PostgreSQL health check.")
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            logger.info("PostgreSQL health check passed.")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return False
