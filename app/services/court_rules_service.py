"""
Court rules knowledge service.

Ingests .txt / .docx court-rule documents into the OpenSearch (or configured)
`court_rules` collection and answers generic legal questions via naive RAG.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.adapters.vector_store.base import VectorStoreAdapter
from app.config.pgvector_store import pgvector_vector_dim
from app.config.settings import settings
from app.config.vector_store_factory import create_connected_vector_store
from app.core.pipelines.ingestion.chunkers.court_rules import CourtRulesChunker
from app.core.pipelines.ingestion.loaders.docx_loader import DocxLoader
from app.core.pipelines.ingestion.loaders.text import TextLoader
from app.core.pipelines.ingestion.pipeline import (
    IngestionConfig,
    IngestionPipeline,
    IngestionResult,
)
from app.services.retrieval_service import RAGService

logger = logging.getLogger(__name__)

COURT_RULES_SYSTEM_PROMPT = """You are a US Legal Pro assistant answering court rules and filing questions.

Use ONLY the retrieved court-rule context below. Do not invent statutes, deadlines, or procedures.
If the context is insufficient, say you do not have that rule in the knowledge base and suggest the user consult local counsel or official court resources.
When an Authority or source file is present in context, mention it briefly.
Keep the tone professional and concise. Do not use markdown or emojis.
After answering, briefly offer help with filing a court case if appropriate.
"""

_SUPPORTED_EXTENSIONS = {".txt", ".text", ".md", ".docx"}
_vector_store_cache: Dict[str, VectorStoreAdapter] = {}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip().lower())
    return cleaned.strip("_") or "doc"


def build_source_key(
    *,
    file_name: str,
    state_code: str,
    case_type: str,
    doc_type: str,
) -> str:
    base = Path(file_name).stem
    return _slug(f"{state_code}_{case_type}_{doc_type}_{base}")


def infer_doc_type(file_name: str, explicit: Optional[str] = None) -> str:
    if explicit:
        return _slug(explicit)
    name = (file_name or "").lower()
    if "faq" in name:
        return "faq"
    if "statewide" in name:
        return "statewide_rule"
    if "standard" in name:
        return "standard_rules"
    return "court_rule"


class CourtRulesService:
    """Ingest and query court-rule knowledge (default: OpenSearch)."""

    def __init__(
        self,
        embedder: Any,
        llm_client: Optional[Any] = None,
        vector_store: Optional[VectorStoreAdapter] = None,
    ):
        self.embedder = embedder
        self.llm_client = llm_client or embedder
        self._vector_store = vector_store
        self.collection_name = getattr(
            settings, "COURT_RULES_COLLECTION", "court_rules"
        )
        self.vector_store_name = getattr(
            settings, "COURT_RULES_VECTOR_STORE", None
        ) or getattr(settings, "DEFAULT_VECTOR_STORE", "opensearch")
        self.top_k = int(getattr(settings, "COURT_RULES_TOP_K", 3) or 3)

    async def _get_vector_store(self) -> VectorStoreAdapter:
        if self._vector_store is not None:
            return self._vector_store
        cached = _vector_store_cache.get(self.vector_store_name)
        if cached is not None:
            self._vector_store = cached
            return cached

        adapter = await create_connected_vector_store(
            self.vector_store_name, self.embedder
        )
        _vector_store_cache[self.vector_store_name] = adapter
        self._vector_store = adapter
        return adapter

    def _loader_for_extension(self, extension: str):
        ext = extension.lower()
        if ext == ".docx":
            return DocxLoader()
        if ext in {".txt", ".text", ".md", ".markdown", ".rst"}:
            return TextLoader()
        raise ValueError(
            f"Unsupported court-rules file type '{ext}'. Upload .txt or .docx."
        )

    async def ingest_file_bytes(
        self,
        *,
        file_bytes: bytes,
        file_name: str,
        state_code: str = "TX",
        case_type: str = "divorce",
        doc_type: Optional[str] = None,
        replace_existing: bool = True,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not file_bytes:
            raise ValueError("Uploaded file is empty.")

        extension = Path(file_name).suffix.lower()
        if extension not in _SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{extension}'. Allowed: .txt, .docx"
            )

        resolved_doc_type = infer_doc_type(file_name, doc_type)
        source_key = build_source_key(
            file_name=file_name,
            state_code=state_code,
            case_type=case_type,
            doc_type=resolved_doc_type,
        )
        content_hash = hashlib.sha256(file_bytes).hexdigest()[:16]

        metadata = {
            "kb": "court_rules",
            "state_code": (state_code or "").upper(),
            "case_type": _slug(case_type),
            "doc_type": resolved_doc_type,
            "source_file": file_name,
            "source_key": source_key,
            "content_hash": content_hash,
            **(extra_metadata or {}),
        }

        suffix = extension or ".txt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            loader = self._loader_for_extension(extension)
            documents = await loader.load(tmp_path)
            if not documents:
                raise RuntimeError(f"No text extracted from {file_name}")

            for doc in documents:
                doc.doc_id = source_key
                doc.metadata = {**doc.metadata, **metadata, "source": file_name}
                doc.source = file_name

            vector_store = await self._get_vector_store()
            if hasattr(vector_store, "ensure_knn_mapping"):
                await vector_store.ensure_knn_mapping(
                    self.collection_name,
                    pgvector_vector_dim(self.embedder),
                )
            if replace_existing and hasattr(vector_store, "delete_by_metadata"):
                deleted = await vector_store.delete_by_metadata(
                    self.collection_name,
                    {"source_key": source_key},
                )
                logger.info(
                    "Replaced prior court-rules chunks for source_key=%s deleted=%s",
                    source_key,
                    deleted,
                )

            chunker = CourtRulesChunker(
                max_chunk_size=int(
                    getattr(settings, "COURT_RULES_CHUNK_SIZE", 1800) or 1800
                ),
                chunk_overlap=int(
                    getattr(settings, "COURT_RULES_CHUNK_OVERLAP", 150) or 150
                ),
            )
            pipeline = IngestionPipeline(
                vector_store=vector_store,
                embedder=self.embedder,
                chunker=chunker,
                loader=loader,
            )
            result: IngestionResult = await pipeline.ingest_documents(
                documents,
                IngestionConfig(
                    collection_name=self.collection_name,
                    chunk_size=chunker.max_chunk_size,
                    chunk_overlap=chunker.chunk_overlap,
                    reset_collection=False,
                    metadata=metadata,
                ),
            )

            return {
                "success": result.vectors_stored > 0 and result.failed_chunks == 0,
                "collection_name": self.collection_name,
                "vector_store": self.vector_store_name,
                "source_key": source_key,
                "source_file": file_name,
                "state_code": metadata["state_code"],
                "case_type": metadata["case_type"],
                "doc_type": metadata["doc_type"],
                "total_documents": result.total_documents,
                "total_chunks": result.total_chunks,
                "vectors_stored": result.vectors_stored,
                "failed_chunks": result.failed_chunks,
                "vector_dim": pgvector_vector_dim(self.embedder),
                "metadata": metadata,
            }
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

    async def reset_index(self) -> Dict[str, Any]:
        """Drop the court-rules index so the next ingest recreates it correctly."""
        vector_store = await self._get_vector_store()
        existed = await vector_store.collection_exists(self.collection_name)
        embedding_type = None
        if existed and hasattr(vector_store, "embedding_field_type"):
            embedding_type = await vector_store.embedding_field_type(
                self.collection_name
            )

        deleted = False
        if existed:
            deleted = await vector_store.delete_collection(self.collection_name)

        logger.info(
            "Court-rules index reset: existed=%s deleted=%s previous_embedding_type=%s",
            existed,
            deleted,
            embedding_type,
        )
        return {
            "collection_name": self.collection_name,
            "vector_store": self.vector_store_name,
            "existed": existed,
            "deleted": deleted,
            "previous_embedding_type": embedding_type,
        }

    def _retrieval_filters(
        self,
        *,
        state_code: Optional[str] = None,
        case_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {"kb": "court_rules"}
        if state_code:
            filters["state_code"] = state_code.upper()
        if case_type:
            filters["case_type"] = _slug(case_type)
        return filters

    async def retrieve_chunks(
        self,
        query: str,
        *,
        state_code: Optional[str] = None,
        case_type: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Semantic search only: embed query → OpenSearch k-NN → top-k chunks.
        Does not call the LLM.
        """
        query = (query or "").strip()
        k = int(top_k or self.top_k)
        filters = self._retrieval_filters(state_code=state_code, case_type=case_type)

        if not query:
            return {
                "query": "",
                "top_k": k,
                "chunk_count": 0,
                "collection_name": self.collection_name,
                "vector_store": self.vector_store_name,
                "filters": filters,
                "chunks": [],
            }

        vector_store = await self._get_vector_store()
        if hasattr(vector_store, "ensure_knn_mapping"):
            await vector_store.ensure_knn_mapping(
                self.collection_name,
                pgvector_vector_dim(self.embedder),
            )
        query_embedding = await self.embedder.embed(query)
        score_threshold = float(
            getattr(settings, "COURT_RULES_SCORE_THRESHOLD", 0.0) or 0.0
        )
        results = await vector_store.query(
            collection_name=self.collection_name,
            query_vector=query_embedding,
            top_k=k,
            filters=filters,
            score_threshold=score_threshold,
        )

        chunks: List[Dict[str, Any]] = []
        for rank, hit in enumerate(results, start=1):
            payload = dict(hit.payload or {})
            content = str(payload.get("content") or "")
            chunks.append(
                {
                    "rank": rank,
                    "id": hit.id,
                    "score": hit.score,
                    "content": content,
                    "source_file": payload.get("source_file"),
                    "source_key": payload.get("source_key"),
                    "section_title": payload.get("section_title"),
                    "state_code": payload.get("state_code"),
                    "case_type": payload.get("case_type"),
                    "doc_type": payload.get("doc_type"),
                    "chunk_index": payload.get("chunk_index"),
                    "metadata": payload,
                }
            )

        logger.info(
            "Court-rules retrieve query=%r top_k=%s returned=%s filters=%s",
            query[:80],
            k,
            len(chunks),
            filters,
        )
        return {
            "query": query,
            "top_k": k,
            "chunk_count": len(chunks),
            "collection_name": self.collection_name,
            "vector_store": self.vector_store_name,
            "filters": filters,
            "chunks": chunks,
        }

    async def answer_question(
        self,
        question: str,
        *,
        state_code: Optional[str] = None,
        case_type: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        question = (question or "").strip()
        if not question:
            return {"answer": "", "sources": [], "used_rag": False}

        filters = self._retrieval_filters(state_code=state_code, case_type=case_type)

        rag = RAGService(llm_client=self.llm_client, embedder=self.embedder)
        try:
            response = await rag.execute_naive_rag(
                query=question,
                vector_store_name=self.vector_store_name,
                collection_name=self.collection_name,
                top_k=top_k or self.top_k,
                filters=filters,
                score_threshold=float(
                    getattr(settings, "COURT_RULES_SCORE_THRESHOLD", 0.0) or 0.0
                ),
                system_prompt=COURT_RULES_SYSTEM_PROMPT,
            )
        except Exception as exc:
            logger.warning("Court-rules RAG failed: %s", exc, exc_info=True)
            return {
                "answer": (
                    "I could not retrieve court-rule context right now. "
                    "Please try again, or I can help you start a court filing."
                ),
                "sources": [],
                "used_rag": False,
                "error": str(exc),
            }

        sources: List[Dict[str, Any]] = []
        for src in response.sources or []:
            meta = getattr(src, "metadata", None) or {}
            sources.append(
                {
                    "id": getattr(src, "id", None),
                    "score": getattr(src, "score", None),
                    "source_file": meta.get("source_file"),
                    "section_title": meta.get("section_title"),
                    "content_preview": (getattr(src, "content", "") or "")[:240],
                }
            )

        return {
            "answer": response.answer or "",
            "sources": sources,
            "used_rag": True,
            "collection_name": self.collection_name,
            "vector_store": self.vector_store_name,
            "filters": filters,
        }
