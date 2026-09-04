"""
Semantic Chunker

Chunking strategy that respects semantic boundaries like paragraphs and sentences.
"""

import re
import logging
from typing import List, Dict, Any, Optional

from app.core.pipelines.ingestion.chunkers.base import ChunkingStrategy, Chunk

logger = logging.getLogger(__name__)


class SemanticChunker(ChunkingStrategy):
    """
    Semantic chunking that respects document structure.
    
    Splits text at natural boundaries like paragraphs and sentences,
    trying to keep semantically related content together.
    """

    def __init__(
        self,
        max_chunk_size: int = 1000,
        min_chunk_size: int = 100,
        paragraph_separator: str = "\n\n",
        sentence_separators: List[str] = None,
        respect_headers: bool = True
    ):
        """
        Initialize the semantic chunker.
        
        Args:
            max_chunk_size: Maximum size for each chunk.
            min_chunk_size: Minimum size for each chunk (to avoid tiny chunks).
            paragraph_separator: String that separates paragraphs.
            sentence_separators: Patterns for sentence boundaries.
            respect_headers: Whether to treat headers as chunk boundaries.
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.paragraph_separator = paragraph_separator
        self.sentence_separators = sentence_separators or [". ", "! ", "? ", ".\n"]
        self.respect_headers = respect_headers
        
        # Header patterns (Markdown style)
        self.header_pattern = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)
        
        logger.info(f"[SemanticChunker] Initialized with max_chunk_size={max_chunk_size}, min_chunk_size={min_chunk_size}, paragraph_separator={repr(paragraph_separator)}, sentence_separators={self.sentence_separators}, respect_headers={respect_headers}")

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = [text]
        logger.debug("[SemanticChunker] Splitting text into sentences")
        for sep in self.sentence_separators:
            new_sentences = []
            for sent in sentences:
                parts = sent.split(sep)
                for i, part in enumerate(parts):
                    if i < len(parts) - 1:
                        new_sentences.append(part + sep.rstrip())
                    else:
                        new_sentences.append(part)
            sentences = new_sentences
        logger.debug(f"[SemanticChunker] Sentence split produced {len(sentences)} sentences")
        return [s.strip() for s in sentences if s.strip()]

    def _split_at_headers(self, text: str) -> List[tuple]:
        """Split text at headers, returning (header, content) tuples."""
        logger.debug("[SemanticChunker] Splitting text at headers (respect_headers=%s)", self.respect_headers)
        sections = []
        
        matches = list(self.header_pattern.finditer(text))
        
        if not matches:
            logger.debug("[SemanticChunker] No headers found. Returning whole text as one section.")
            return [(None, text)]
        
        # Content before first header
        if matches[0].start() > 0:
            pre_header = text[:matches[0].start()].strip()
            logger.debug("[SemanticChunker] Found pre-header section of length %d", len(pre_header))
            sections.append((None, pre_header))
        
        # Each header and its content
        for i, match in enumerate(matches):
            header = match.group()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            logger.debug("[SemanticChunker] Section: header='%s' content_length=%d", header, len(content))
            sections.append((header, content))
        
        logger.debug("[SemanticChunker] Split into %d sections by headers", len(sections))
        return sections

    def chunk(
        self,
        text: str,
        doc_id: str,
        metadata: Dict[str, Any] = None
    ) -> List[Chunk]:
        """
        Split text into semantically coherent chunks.
        
        Args:
            text: The text to chunk.
            doc_id: ID of the source document.
            metadata: Optional metadata to include in chunks.
            
        Returns:
            List of Chunk objects.
        """
        if not text:
            logger.warning(f"[SemanticChunker] Empty input text for document {doc_id}. Returning empty chunk list.")
            return []
        
        metadata = metadata or {}
        chunks = []
        chunk_index = 0
        
        # First, split at headers if enabled
        if self.respect_headers:
            logger.debug(f"[SemanticChunker] Splitting text at headers for document {doc_id}")
            sections = self._split_at_headers(text)
        else:
            logger.debug(f"[SemanticChunker] Not respecting headers for document {doc_id}")
            sections = [(None, text)]
        
        for section_header, section_content in sections:
            if not section_content:
                logger.debug(f"[SemanticChunker] Skipping empty section in document {doc_id}; header={section_header!r}")
                continue
            
            # Split section into paragraphs
            paragraphs = section_content.split(self.paragraph_separator)
            paragraphs = [p.strip() for p in paragraphs if p.strip()]
            logger.debug(f"[SemanticChunker] Section(header={section_header!r}): split into {len(paragraphs)} paragraphs")
            
            current_chunk_parts = []
            if section_header:
                current_chunk_parts.append(section_header)
            current_length = len(section_header) if section_header else 0
            
            for para in paragraphs:
                para_length = len(para)
                logger.debug(f"[SemanticChunker] Considering paragraph (length={para_length}) for current chunk (length={current_length}); doc_id={doc_id}, header={section_header!r}")
                
                # If paragraph alone exceeds max size, split into sentences
                if para_length > self.max_chunk_size:
                    logger.info(f"[SemanticChunker] Paragraph exceeds max_chunk_size ({self.max_chunk_size}). Splitting into sentences for doc_id={doc_id}, header={section_header!r}")
                    # First, flush current chunk if any
                    if current_chunk_parts and current_length > 0:
                        chunk_text = "\n\n".join(current_chunk_parts)
                        logger.debug(f"[SemanticChunker] Flushing chunk (index={chunk_index}) (length={len(chunk_text)}) before multi-sentence split; doc_id={doc_id}, header={section_header!r}")
                        chunk = self._create_chunk(
                            chunk_text, doc_id, chunk_index, metadata, section_header
                        )
                        chunks.append(chunk)
                        chunk_index += 1
                        current_chunk_parts = []
                        current_length = 0
                    
                    # Split paragraph into sentences
                    sentences = self._split_into_sentences(para)
                    logger.debug(f"[SemanticChunker] Split paragraph into {len(sentences)} sentences for doc_id={doc_id}")
                    sent_chunk = []
                    sent_length = 0
                    
                    for sent in sentences:
                        if sent_length + len(sent) > self.max_chunk_size and sent_chunk:
                            chunk_text = " ".join(sent_chunk)
                            logger.debug(f"[SemanticChunker] Creating chunk from sentences (index={chunk_index}) (length={len(chunk_text)}) for doc_id={doc_id}, header={section_header!r}")
                            chunk = self._create_chunk(
                                chunk_text, doc_id, chunk_index, metadata, section_header
                            )
                            chunks.append(chunk)
                            chunk_index += 1
                            sent_chunk = []
                            sent_length = 0
                        
                        sent_chunk.append(sent)
                        sent_length += len(sent) + 1
                    
                    if sent_chunk:
                        logger.debug(f"[SemanticChunker] Carrying {len(sent_chunk)} leftover sentences to next section chunk (total length={sent_length})")
                        current_chunk_parts = sent_chunk
                        current_length = sent_length
                
                # If adding this paragraph would exceed max, start new chunk
                elif current_length + para_length > self.max_chunk_size:
                    logger.debug(f"[SemanticChunker] Current chunk would exceed max_chunk_size after adding paragraph. Flushing chunk (index={chunk_index}) (length={current_length}) for doc_id={doc_id}, header={section_header!r}")
                    if current_chunk_parts:
                        chunk_text = "\n\n".join(current_chunk_parts)
                        chunk = self._create_chunk(
                            chunk_text, doc_id, chunk_index, metadata, section_header
                        )
                        chunks.append(chunk)
                        chunk_index += 1
                    
                    current_chunk_parts = [para]
                    current_length = para_length
                
                else:
                    current_chunk_parts.append(para)
                    current_length += para_length + 2  # +2 for paragraph separator
                    logger.debug(f"[SemanticChunker] Added paragraph to current chunk (now length={current_length}) for doc_id={doc_id}, header={section_header!r}")
            
            # Don't forget the last chunk of this section
            if current_chunk_parts:
                chunk_text = "\n\n".join(current_chunk_parts)
                
                # Only create if meets minimum size
                if len(chunk_text) >= self.min_chunk_size:
                    logger.debug(f"[SemanticChunker] Creating final chunk for section (index={chunk_index}), length={len(chunk_text)}, doc_id={doc_id}, header={section_header!r}")
                    chunk = self._create_chunk(
                        chunk_text, doc_id, chunk_index, metadata, section_header
                    )
                    chunks.append(chunk)
                    chunk_index += 1
                elif chunks:
                    logger.debug(f"[SemanticChunker] Last chunk too small (length={len(chunk_text)}). Merging with previous chunk for doc_id={doc_id}")
                    # Merge with previous chunk
                    prev_chunk = chunks[-1]
                    prev_chunk.content += "\n\n" + chunk_text
                    prev_chunk.metadata["chunk_size"] = len(prev_chunk.content)
                else:
                    logger.warning(f"[SemanticChunker] Skipped chunk of size {len(chunk_text)} (smaller than min_chunk_size={self.min_chunk_size}) in doc_id={doc_id}; no previous chunk to merge with.")
        
        logger.info(f"[SemanticChunker] Created {len(chunks)} semantic chunks from document {doc_id}")
        return chunks

    def _create_chunk(
        self,
        content: str,
        doc_id: str,
        chunk_index: int,
        metadata: Dict[str, Any],
        section_header: Optional[str]
    ) -> Chunk:
        """Create a Chunk object."""
        chunk_metadata = {
            **metadata,
            "chunk_size": len(content),
            "chunker": "SemanticChunker",
        }
        if section_header:
            chunk_metadata["section_header"] = section_header
        
        logger.debug(f"[SemanticChunker] Creating Chunk (doc_id={doc_id}, chunk_index={chunk_index}, header={section_header!r}, chunk_size={len(content)})")
        return Chunk(
            content=content,
            chunk_id="",
            doc_id=doc_id,
            chunk_index=chunk_index,
            metadata=chunk_metadata,
        )

