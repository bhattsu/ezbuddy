"""
AWS OpenSearch (k-NN) Vector Store Adapter

Stores embeddings in an OpenSearch index with knn_vector fields.
Supports Amazon OpenSearch Service with IAM SigV4 auth.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.adapters.vector_store.base import (
    DistanceMetric,
    SearchResult,
    VectorStoreAdapter,
    VectorStoreConfig,
)
from app.config.registry import register_adapter

logger = logging.getLogger(__name__)


def _space_type(metric: DistanceMetric) -> str:
    mapping = {
        DistanceMetric.COSINE: "cosinesimil",
        DistanceMetric.EUCLIDEAN: "l2",
        DistanceMetric.DOT_PRODUCT: "innerproduct",
    }
    return mapping.get(metric, "cosinesimil")


@register_adapter("opensearch")
class OpenSearchAdapter(VectorStoreAdapter):
    """Amazon OpenSearch Service / OpenSearch k-NN adapter."""

    def __init__(self, config: VectorStoreConfig):
        super().__init__(config)
        self._client = None
        self._async_mode = True

    def _endpoint(self) -> str:
        host = (self.config.host or "").strip()
        if not host:
            raise ValueError("OpenSearch host/endpoint is required")
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        use_ssl = bool(self.config.extra_params.get("use_ssl", True))
        scheme = "https" if use_ssl else "http"
        port = int(self.config.port or (443 if use_ssl else 80))
        if (use_ssl and port == 443) or (not use_ssl and port == 80):
            return f"{scheme}://{host}"
        return f"{scheme}://{host}:{port}"

    def _build_client(self):
        try:
            from opensearchpy import OpenSearch, RequestsHttpConnection
        except ImportError as exc:
            raise ImportError(
                "opensearch-py is required for OpenSearch. "
                "Install with: pip install opensearch-py requests-aws4auth"
            ) from exc

        endpoint = self._endpoint()
        parsed = urlparse(endpoint)
        host = parsed.hostname or self.config.host
        port = parsed.port or int(self.config.port or 443)
        use_ssl = parsed.scheme != "http"
        verify_certs = bool(self.config.extra_params.get("verify_certs", True))
        region = self.config.extra_params.get("region") or "us-east-1"
        use_aws_auth = bool(self.config.extra_params.get("use_aws_auth", True))
        # Managed domain = "es"; OpenSearch Serverless = "aoss"
        aws_service = self.config.extra_params.get("aws_service") or "es"

        http_auth = None
        if use_aws_auth:
            import boto3
            from requests_aws4auth import AWS4Auth

            session = boto3.Session(
                aws_access_key_id=self.config.extra_params.get("aws_access_key_id") or None,
                aws_secret_access_key=self.config.extra_params.get("aws_secret_access_key")
                or None,
                region_name=region,
            )
            credentials = session.get_credentials()
            if credentials is None:
                raise ConnectionError(
                    "No AWS credentials found for OpenSearch SigV4 auth"
                )
            frozen = credentials.get_frozen_credentials()
            http_auth = AWS4Auth(
                frozen.access_key,
                frozen.secret_key,
                region,
                aws_service,
                session_token=frozen.token,
            )
        elif self.config.extra_params.get("username"):
            http_auth = (
                self.config.extra_params.get("username"),
                self.config.extra_params.get("password") or self.config.api_key or "",
            )

        kwargs = {
            "hosts": [{"host": host, "port": port}],
            "http_auth": http_auth,
            "use_ssl": use_ssl,
            "verify_certs": verify_certs,
            "connection_class": RequestsHttpConnection,
            "timeout": int(self.config.extra_params.get("timeout", 60)),
        }

        # Prefer sync client (works without aiohttp). Optional async if installed.
        try:
            from opensearchpy import AsyncOpenSearch  # type: ignore

            self._async_mode = True
            logger.info("Using AsyncOpenSearch client")
            return AsyncOpenSearch(**kwargs)
        except Exception:
            self._async_mode = False
            logger.info("Using sync OpenSearch client")
            return OpenSearch(**kwargs)

    async def connect(self) -> None:
        try:
            self._client = self._build_client()
            ok = await self.health_check()
            if not ok:
                # OpenSearch Serverless often rejects /_cluster/health and ping;
                # still allow connect — first real index call will validate auth.
                aws_service = self.config.extra_params.get("aws_service") or "es"
                if aws_service == "aoss":
                    logger.warning(
                        "OpenSearch Serverless ping failed; continuing (AOSS is expected)"
                    )
                else:
                    raise ConnectionError("OpenSearch ping failed")
            logger.info("Connected to OpenSearch at %s", self._endpoint())
        except Exception as e:
            logger.error("Failed to connect to OpenSearch: %s", e)
            raise ConnectionError(f"Failed to connect to OpenSearch: {e}") from e

    async def health_check(self) -> bool:
        try:
            if self._client is None:
                return False
            # ping() is unreliable on AOSS; treat client construction success as OK there.
            aws_service = self.config.extra_params.get("aws_service") or "es"
            if aws_service == "aoss":
                return True
            return bool(await self._call(self._client.ping))
        except Exception as e:
            logger.error("OpenSearch health check failed: %s", e)
            return False

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            if self._async_mode and hasattr(self._client, "close"):
                await self._client.close()
            elif hasattr(self._client, "close"):
                self._client.close()
        finally:
            self._client = None
            logger.info("Disconnected from OpenSearch")

    async def _call(self, fn, *args, **kwargs):
        if self._async_mode:
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def create_collection(
        self,
        collection_name: str,
        vector_dim: int,
        distance_metric: DistanceMetric = DistanceMetric.COSINE,
        **kwargs,
    ) -> bool:
        if await self.collection_exists(collection_name):
            return True

        aws_service = self.config.extra_params.get("aws_service") or "es"
        # Managed domains commonly use nmslib. AOSS auto-selects faiss —
        # do NOT send "engine" on create (Serverless rejects it).
        engine = self.config.extra_params.get("knn_engine") or "nmslib"

        if aws_service == "aoss":
            # Matches the working Serverless Dev Tools mapping.
            body = {
                "settings": {"index": {"knn": True}},
                "mappings": {
                    "properties": {
                        "id": {"type": "keyword"},
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": int(vector_dim),
                            "method": {
                                "name": "hnsw",
                                "space_type": _space_type(distance_metric),
                                "parameters": {},
                            },
                        },
                        "content": {"type": "text"},
                        "doc_id": {"type": "keyword"},
                        "chunk_index": {"type": "integer"},
                        "kb": {"type": "keyword"},
                        "state_code": {"type": "keyword"},
                        "case_type": {"type": "keyword"},
                        "doc_type": {"type": "keyword"},
                        "source_file": {"type": "keyword"},
                        "source_key": {"type": "keyword"},
                        "section_title": {"type": "text"},
                    }
                },
            }
        else:
            body = {
                "settings": {
                    "index": {
                        "knn": True,
                        "knn.algo_param.ef_search": int(
                            self.config.extra_params.get("ef_search", 100)
                        ),
                        "number_of_shards": int(
                            self.config.extra_params.get("number_of_shards", 1)
                        ),
                        "number_of_replicas": int(
                            self.config.extra_params.get("number_of_replicas", 1)
                        ),
                    }
                },
                "mappings": {
                    "properties": {
                        "id": {"type": "keyword"},
                        "embedding": {
                            "type": "knn_vector",
                            "dimension": vector_dim,
                            "method": {
                                "name": "hnsw",
                                "space_type": _space_type(distance_metric),
                                "engine": engine,
                                "parameters": {
                                    "ef_construction": int(
                                        self.config.extra_params.get(
                                            "ef_construction", 128
                                        )
                                    ),
                                    "m": int(self.config.extra_params.get("hnsw_m", 16)),
                                },
                            },
                        },
                        "content": {"type": "text"},
                        "doc_id": {"type": "keyword"},
                        "chunk_index": {"type": "integer"},
                        "kb": {"type": "keyword"},
                        "state_code": {"type": "keyword"},
                        "case_type": {"type": "keyword"},
                        "doc_type": {"type": "keyword"},
                        "source_file": {"type": "keyword"},
                        "source_key": {"type": "keyword"},
                        "section_title": {"type": "text"},
                        "metadata": {"type": "object", "enabled": True},
                    }
                },
            }
        try:
            await self._call(self._client.indices.create, index=collection_name, body=body)
            logger.info(
                "Created OpenSearch index '%s' dim=%s aws_service=%s",
                collection_name,
                vector_dim,
                aws_service,
            )
            return True
        except Exception as e:
            # Race: index created concurrently
            err = str(e).lower()
            if "resource_already_exists_exception" in err:
                return True
            logger.error("Failed to create OpenSearch index %s: %s", collection_name, e)
            if "403" in err or "authorization" in err or "security_exception" in err:
                raise PermissionError(
                    "OpenSearch Serverless denied index create (403). "
                    "Add a data-access policy for your IAM user/role with "
                    "aoss:CreateIndex, aoss:WriteDocument, aoss:ReadDocument, "
                    "aoss:DescribeIndex on index/<collection-name>/* "
                    "and collection permissions on collection/<collection-name>. "
                    f"Details: {e}"
                ) from e
            return False

    async def delete_collection(self, collection_name: str) -> bool:
        try:
            if not await self.collection_exists(collection_name):
                return True
            await self._call(self._client.indices.delete, index=collection_name)
            logger.info("Deleted OpenSearch index '%s'", collection_name)
            return True
        except Exception as e:
            logger.error("Failed to delete OpenSearch index %s: %s", collection_name, e)
            return False

    async def collection_exists(self, collection_name: str) -> bool:
        try:
            return bool(
                await self._call(self._client.indices.exists, index=collection_name)
            )
        except Exception as e:
            logger.error("Failed checking OpenSearch index %s: %s", collection_name, e)
            return False

    async def embedding_field_info(self, collection_name: str) -> Dict[str, Any]:
        """Return embedding field mapping info: type, dimension, method, mode."""
        try:
            mapping = await self._call(
                self._client.indices.get_mapping, index=collection_name
            )
            for index_body in (mapping or {}).values():
                props = (
                    (index_body or {}).get("mappings", {}).get("properties", {}) or {}
                )
                field = props.get("embedding") or {}
                if field:
                    return {
                        "type": field.get("type"),
                        "dimension": field.get("dimension"),
                        "method": field.get("method") or {},
                        "mode": field.get("mode"),
                    }
            return {}
        except Exception as e:
            logger.warning(
                "Could not read mapping for index %s: %s", collection_name, e
            )
            return {}

    async def embedding_field_type(self, collection_name: str) -> Optional[str]:
        info = await self.embedding_field_info(collection_name)
        field_type = info.get("type")
        return str(field_type) if field_type else None

    async def ensure_knn_mapping(
        self,
        collection_name: str,
        vector_dim: int,
        distance_metric: DistanceMetric = DistanceMetric.COSINE,
    ) -> None:
        """
        Ensure index exists with embedding as knn_vector (required for k-NN).

        Indexes auto-created by a plain bulk write get a `float` mapping, which
        makes k-NN search fail with "Field 'embedding' is not knn_vector type."
        """
        if not await self.collection_exists(collection_name):
            created = await self.create_collection(
                collection_name, vector_dim, distance_metric
            )
            if not created:
                raise RuntimeError(
                    f"Failed to create OpenSearch index '{collection_name}' "
                    "with knn_vector mapping."
                )
            logger.info(
                "OpenSearch index '%s' ready with knn_vector dim=%s",
                collection_name,
                vector_dim,
            )
            return

        info = await self.embedding_field_info(collection_name)
        field_type = info.get("type")
        mapped_dim = info.get("dimension")

        if field_type and field_type != "knn_vector":
            raise RuntimeError(
                f"OpenSearch index '{collection_name}' has embedding mapped as "
                f"'{field_type}' instead of 'knn_vector', so semantic search cannot "
                "work. Delete in Dev Tools (`DELETE court_rules`), recreate with "
                "knn_vector, then re-ingest."
            )

        if mapped_dim is not None and int(mapped_dim) != int(vector_dim):
            raise RuntimeError(
                f"OpenSearch index '{collection_name}' embedding dimension is "
                f"{mapped_dim}, but the embedder uses {vector_dim}. Recreate the "
                "index with the matching dimension, then re-ingest."
            )

        logger.info(
            "OpenSearch index '%s' knn mapping OK (type=%s dim=%s mode=%s)",
            collection_name,
            field_type or "unknown",
            mapped_dim or vector_dim,
            info.get("mode"),
        )

    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        metadata: List[Dict[str, Any]],
        **kwargs,
    ) -> int:
        if not ids:
            return 0
        try:
            from opensearchpy.helpers import async_bulk, bulk

            if self._async_mode:
                success, errors = await async_bulk(
                    self._client,
                    self._iter_actions(collection_name, ids, embeddings, metadata),
                    raise_on_error=False,
                )
            else:
                success, errors = await asyncio.to_thread(
                    bulk,
                    self._client,
                    list(self._iter_actions(collection_name, ids, embeddings, metadata)),
                    raise_on_error=False,
                )
            if errors:
                logger.warning(
                    "OpenSearch upsert had %s errors (showing first): %s",
                    len(errors) if isinstance(errors, list) else errors,
                    errors[:1] if isinstance(errors, list) else errors,
                )
                first = errors[0] if isinstance(errors, list) and errors else {}
                err_blob = str(first).lower()
                if "403" in err_blob or "authorization" in err_blob:
                    raise PermissionError(
                        "OpenSearch Serverless denied document write (403). "
                        "Update the collection data-access policy to allow "
                        "aoss:WriteDocument / aoss:CreateIndex for your IAM principal. "
                        f"Sample error: {first}"
                    )

            # Refresh is not reliably supported on AOSS; ignore failures there.
            aws_service = self.config.extra_params.get("aws_service") or "es"
            if aws_service != "aoss":
                try:
                    await self._call(
                        self._client.indices.refresh, index=collection_name
                    )
                except Exception as refresh_err:
                    logger.warning(
                        "OpenSearch refresh skipped/failed: %s", refresh_err
                    )

            # bulk() returns (success_count, error_list) when raise_on_error=False
            stored = int(success) if isinstance(success, int) else 0
            if errors and isinstance(errors, list):
                # success count from helpers is docs that succeeded
                pass
            logger.info(
                "Upserted %s vectors to OpenSearch index '%s'",
                stored,
                collection_name,
            )
            return stored
        except PermissionError:
            raise
        except Exception as e:
            logger.error("OpenSearch upsert failed: %s", e, exc_info=True)
            return 0

    def _iter_actions(self, collection_name, ids, embeddings, metadata):
        for doc_id, embedding, meta in zip(ids, embeddings, metadata):
            meta = dict(meta or {})
            # Only write fields that match the court_rules knn_vector mapping
            # (AOSS vector indexes often reject undeclared nested objects).
            yield {
                "_op_type": "index",
                "_index": collection_name,
                "_id": doc_id,
                "_source": {
                    "id": doc_id,
                    "embedding": embedding,
                    "content": meta.get("content", ""),
                    "doc_id": meta.get("doc_id"),
                    "chunk_index": meta.get("chunk_index"),
                    "kb": meta.get("kb"),
                    "state_code": meta.get("state_code"),
                    "case_type": meta.get("case_type"),
                    "doc_type": meta.get("doc_type"),
                    "source_file": meta.get("source_file") or meta.get("source"),
                    "source_key": meta.get("source_key"),
                    "section_title": meta.get("section_title"),
                },
            }

    def _filter_clause(self, filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not filters:
            return []
        clauses: List[Dict[str, Any]] = []
        for key, value in filters.items():
            if value is None or value == {} or value == []:
                continue
            # Prefer top-level keyword fields for filter speed
            field = key if key in {
                "kb",
                "state_code",
                "case_type",
                "doc_type",
                "source_file",
                "source_key",
                "doc_id",
            } else f"metadata.{key}"
            if isinstance(value, list):
                clauses.append({"terms": {field: value}})
            elif isinstance(value, bool):
                clauses.append({"term": {field: value}})
            else:
                clauses.append({"term": {field: value}})
        return clauses

    async def query(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        include_vectors: bool = False,
        **kwargs,
    ) -> List[SearchResult]:
        filter_clauses = self._filter_clause(filters)
        knn_query: Dict[str, Any] = {
            "knn": {
                "embedding": {
                    "vector": query_vector,
                    "k": top_k,
                }
            }
        }
        if filter_clauses:
            # Post-filter on knn results for OpenSearch Service knn
            body = {
                "size": top_k,
                "query": {
                    "bool": {
                        "must": [knn_query],
                        "filter": filter_clauses,
                    }
                },
            }
        else:
            body = {"size": top_k, "query": knn_query}

        if not include_vectors:
            body["_source"] = {
                "excludes": ["embedding"],
            }

        score_threshold = float(kwargs.get("score_threshold") or 0.0)
        try:
            response = await self._call(
                self._client.search, index=collection_name, body=body
            )
            hits = (response or {}).get("hits", {}).get("hits", [])
            results: List[SearchResult] = []
            for hit in hits:
                score = float(hit.get("_score") or 0.0)
                if score_threshold and score < score_threshold:
                    continue
                source = hit.get("_source") or {}
                # Documents store fields at top-level (no nested metadata).
                payload = {
                    key: source.get(key)
                    for key in (
                        "content",
                        "source_file",
                        "source_key",
                        "state_code",
                        "case_type",
                        "doc_type",
                        "section_title",
                        "kb",
                        "doc_id",
                        "chunk_index",
                        "id",
                    )
                    if source.get(key) is not None
                }
                if "content" not in payload:
                    payload["content"] = source.get("content") or ""
                results.append(
                    SearchResult(
                        id=str(hit.get("_id") or source.get("id") or ""),
                        score=score,
                        payload=payload,
                        vector=source.get("embedding") if include_vectors else None,
                    )
                )
            logger.info(
                "OpenSearch query on '%s' returned %s hits",
                collection_name,
                len(results),
            )
            return results
        except Exception as e:
            logger.error("OpenSearch query failed: %s", e, exc_info=True)
            return []

    async def delete(self, collection_name: str, ids: List[str]) -> bool:
        if not ids:
            return True
        try:
            body = {"query": {"ids": {"values": ids}}}
            await self._call(
                self._client.delete_by_query,
                index=collection_name,
                body=body,
                refresh=True,
            )
            return True
        except Exception as e:
            logger.error("OpenSearch delete by ids failed: %s", e)
            return False

    async def delete_by_metadata(
        self,
        collection_name: str,
        filters: Dict[str, Any],
    ) -> int:
        if not filters or not await self.collection_exists(collection_name):
            return 0
        clauses = self._filter_clause(filters)
        if not clauses:
            return 0
        try:
            response = await self._call(
                self._client.delete_by_query,
                index=collection_name,
                body={"query": {"bool": {"filter": clauses}}},
                refresh=True,
            )
            deleted = int((response or {}).get("deleted") or 0)
            logger.info(
                "Deleted %s docs from '%s' matching %s",
                deleted,
                collection_name,
                filters,
            )
            return deleted
        except Exception as e:
            logger.error("OpenSearch delete_by_metadata failed: %s", e)
            return 0

    async def get_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        include_vectors: bool = False,
    ) -> List[SearchResult]:
        if not ids:
            return []
        try:
            response = await self._call(
                self._client.mget,
                index=collection_name,
                body={"ids": ids},
            )
            results: List[SearchResult] = []
            for doc in (response or {}).get("docs", []):
                if not doc.get("found"):
                    continue
                source = doc.get("_source") or {}
                payload = dict(source.get("metadata") or {})
                if "content" not in payload and source.get("content"):
                    payload["content"] = source["content"]
                results.append(
                    SearchResult(
                        id=str(doc.get("_id")),
                        score=1.0,
                        payload=payload,
                        vector=source.get("embedding") if include_vectors else None,
                    )
                )
            return results
        except Exception as e:
            logger.error("OpenSearch get_by_ids failed: %s", e)
            return []

    async def count(self, collection_name: str) -> int:
        try:
            if not await self.collection_exists(collection_name):
                return 0
            response = await self._call(
                self._client.count, index=collection_name, body={"query": {"match_all": {}}}
            )
            return int((response or {}).get("count") or 0)
        except Exception as e:
            logger.error("OpenSearch count failed: %s", e)
            return 0
