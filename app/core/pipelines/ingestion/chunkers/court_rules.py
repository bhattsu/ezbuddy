"""
Structure-aware chunker for court rules / FAQ documents.

Prefers natural legal units over blind character cuts:
- FAQ: Q: / A: / Authority blocks
- Statewide-style: ## headings and SECTION headers
- Numbered rules: "1. ..." lists
Falls back to recursive chunking for unstructured text.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.pipelines.ingestion.chunkers.base import Chunk, ChunkingStrategy
from app.core.pipelines.ingestion.chunkers.recursive import RecursiveChunker

_FAQ_Q_RE = re.compile(r"(?m)^Q:\s*")
_NUMBERED_RULE_RE = re.compile(r"(?m)^\s*(\d{1,3})\.\s+\S")
_MD_HEADING_RE = re.compile(r"(?m)^#{1,3}\s+\S")
_SECTION_HEADER_RE = re.compile(r"(?m)^([A-Z][A-Z0-9 /,&()\-]{2,80})$")


def _stable_chunk_id(doc_id: str, chunk_index: int, content: str) -> str:
    digest = hashlib.sha256(
        f"{doc_id}:{chunk_index}:{content[:200]}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{doc_id}_{chunk_index}_{digest}"


class CourtRulesChunker(ChunkingStrategy):
    """Chunk court-rule / FAQ text by document structure."""

    def __init__(self, max_chunk_size: int = 1800, chunk_overlap: int = 150):
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap
        self._fallback = RecursiveChunker(
            chunk_size=max_chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk(
        self,
        text: str,
        doc_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Chunk]:
        text = (text or "").strip()
        if not text:
            return []

        base_meta = dict(metadata or {})
        sections = self._split_into_sections(text)
        if not sections:
            return self._fallback.chunk(text, doc_id, base_meta)

        chunks: List[Chunk] = []
        chunk_index = 0
        for section_title, section_body in sections:
            pieces = self._split_oversized(section_body)
            for piece in pieces:
                content = piece.strip()
                if not content:
                    continue
                meta = {
                    **base_meta,
                    "section_title": section_title or "",
                    "chunk_strategy": "court_rules",
                }
                chunks.append(
                    Chunk(
                        content=content,
                        chunk_id=_stable_chunk_id(doc_id, chunk_index, content),
                        doc_id=doc_id,
                        chunk_index=chunk_index,
                        metadata=meta,
                    )
                )
                chunk_index += 1
        return chunks

    def _split_into_sections(self, text: str) -> List[Tuple[str, str]]:
        if _FAQ_Q_RE.search(text):
            return self._split_faq(text)
        if _MD_HEADING_RE.search(text):
            return self._split_markdown_headings(text)
        if len(_NUMBERED_RULE_RE.findall(text)) >= 3:
            return self._split_numbered_rules(text)
        if self._looks_like_all_caps_sections(text):
            return self._split_caps_sections(text)
        return [("document", text)]

    def _split_faq(self, text: str) -> List[Tuple[str, str]]:
        matches = list(_FAQ_Q_RE.finditer(text))
        if not matches:
            return [("document", text)]

        sections: List[Tuple[str, str]] = []
        if matches[0].start() > 0:
            preface = text[: matches[0].start()].strip()
            if preface:
                sections.append(("preface", preface))

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end].strip()
            first_line = block.splitlines()[0] if block else "FAQ"
            sections.append((first_line[:120], block))
        return sections

    def _split_markdown_headings(self, text: str) -> List[Tuple[str, str]]:
        matches = list(_MD_HEADING_RE.finditer(text))
        if not matches:
            return [("document", text)]

        sections: List[Tuple[str, str]] = []
        if matches[0].start() > 0:
            preface = text[: matches[0].start()].strip()
            if preface:
                sections.append(("preface", preface))

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end].strip()
            title = match.group(0).lstrip("#").strip()[:120]
            sections.append((title or "section", block))
        return sections

    def _split_numbered_rules(self, text: str) -> List[Tuple[str, str]]:
        matches = list(_NUMBERED_RULE_RE.finditer(text))
        if not matches:
            return [("document", text)]

        sections: List[Tuple[str, str]] = []
        if matches[0].start() > 0:
            preface = text[: matches[0].start()].strip()
            if preface:
                sections.append(("preface", preface))

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end].strip()
            sections.append((f"rule_{match.group(1)}", block))
        return sections

    def _looks_like_all_caps_sections(self, text: str) -> bool:
        headers = [
            m.group(1)
            for m in _SECTION_HEADER_RE.finditer(text)
            if "Q:" not in m.group(1) and len(m.group(1).split()) <= 12
        ]
        return len(headers) >= 2

    def _split_caps_sections(self, text: str) -> List[Tuple[str, str]]:
        lines = text.splitlines()
        sections: List[Tuple[str, str]] = []
        current_title = "preface"
        current_lines: List[str] = []

        def flush() -> None:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_title, body))

        for line in lines:
            stripped = line.strip()
            if (
                stripped
                and stripped.upper() == stripped
                and len(stripped) > 2
                and len(stripped.split()) <= 12
                and not stripped.startswith("Q:")
                and re.match(r"^[A-Z0-9 /,&()\-]+$", stripped)
            ):
                flush()
                current_title = stripped[:120]
                current_lines = [stripped]
            else:
                current_lines.append(line)
        flush()
        return sections or [("document", text)]

    def _split_oversized(self, text: str) -> List[str]:
        if len(text) <= self.max_chunk_size:
            return [text]
        pieces = self._fallback.chunk(text, doc_id="tmp", metadata={})
        return [p.content for p in pieces if p.content.strip()]
