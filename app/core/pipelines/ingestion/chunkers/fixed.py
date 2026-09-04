"""
Fixed Size Chunker

Simple chunking strategy with fixed character/token limits.
"""

import logging
from typing import List, Dict, Any, Optional

from app.core.pipelines.ingestion.chunkers.base import ChunkingStrategy, Chunk

logger = logging.getLogger(__name__)


class FixedSizeChunker(ChunkingStrategy):
    """
    Fixed size chunking with overlap.
    
    Splits text into chunks of approximately equal size with
    configurable overlap between consecutive chunks.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separator: str = " ",
        strip_whitespace: bool = True
    ):
        """
        Initialize the fixed size chunker.
        
        Args:
            chunk_size: Target size for each chunk in characters.
            chunk_overlap: Overlap between consecutive chunks.
            separator: Character to split on (to avoid mid-word splits).
            strip_whitespace: Whether to strip whitespace from chunks.
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separator = separator
        self.strip_whitespace = strip_whitespace
        
        if chunk_overlap >= chunk_size:
            logger.error("Invalid parameters: chunk_overlap (=%d) must be less than chunk_size (=%d)", chunk_overlap, chunk_size)
            raise ValueError("Overlap must be less than chunk size")
        logger.info(
            f"[FixedSizeChunker] Initialized with chunk_size={chunk_size}, chunk_overlap={chunk_overlap}, separator='{separator}', strip_whitespace={strip_whitespace}"
        )

    def chunk(
        self,
        text: str,
        doc_id: str,
        metadata: Dict[str, Any] = None
    ) -> List[Chunk]:
        """
        Split text into fixed-size chunks with overlap.
        
        Args:
            text: The text to chunk.
            doc_id: ID of the source document.
            metadata: Optional metadata to include in chunks.
            
        Returns:
            List of Chunk objects.
        """
        if not text:
            logger.warning(f"[FixedSizeChunker] Empty input text for document {doc_id}. Returning empty chunk list.")
            return []
        
        metadata = metadata or {}
        chunks = []
        
        # Split into segments at separator
        if self.separator:
            segments = text.split(self.separator)
            logger.debug(
                f"[FixedSizeChunker] Splitting text into segments using separator '{self.separator}'. {len(segments)} segments created."
            )
        else:
            segments = list(text)
            logger.debug(
                "[FixedSizeChunker] Splitting text into single-character segments because separator is empty."
            )
        
        current_chunk = []
        current_length = 0
        start_char = 0
        chunk_index = 0
        
        for segment in segments:
            segment_with_sep = segment + self.separator if self.separator else segment
            segment_length = len(segment_with_sep)
            
            # Check if adding this segment would exceed chunk size
            if current_length + segment_length > self.chunk_size and current_chunk:
                # Create chunk
                chunk_text = (self.separator if self.separator else "").join(current_chunk)
                if self.strip_whitespace:
                    chunk_text = chunk_text.strip()
                
                if chunk_text:
                    logger.debug(
                        f"[FixedSizeChunker] Creating chunk {chunk_index} for document {doc_id}: start_char={start_char}, length={len(chunk_text)}"
                    )
                    chunk = Chunk(
                        content=chunk_text,
                        chunk_id="",
                        doc_id=doc_id,
                        chunk_index=chunk_index,
                        metadata={
                            **metadata,
                            "chunk_size": len(chunk_text),
                            "chunker": "FixedSizeChunker",
                        },
                        start_char=start_char,
                        end_char=start_char + len(chunk_text),
                    )
                    chunks.append(chunk)
                    chunk_index += 1
                else:
                    logger.debug(
                        f"[FixedSizeChunker] Skipped empty chunk at chunk_index={chunk_index} for document {doc_id}"
                    )
                
                # Calculate overlap - keep last N characters worth of segments
                overlap_segments = []
                overlap_length = 0
                for seg in reversed(current_chunk):
                    seg_len = len(seg) + len(self.separator)
                    if overlap_length + seg_len <= self.chunk_overlap:
                        overlap_segments.insert(0, seg)
                        overlap_length += seg_len
                    else:
                        break
                
                logger.debug(
                    f"[FixedSizeChunker] Overlap calculation: Keeping {len(overlap_segments)} segments for overlap ({overlap_length} characters)"
                )
                start_char += current_length - overlap_length
                current_chunk = overlap_segments
                current_length = overlap_length
            
            current_chunk.append(segment)
            current_length += segment_length
        
        # Don't forget the last chunk
        if current_chunk:
            chunk_text = (self.separator if self.separator else "").join(current_chunk)
            if self.strip_whitespace:
                chunk_text = chunk_text.strip()
            
            if chunk_text:
                logger.debug(
                    f"[FixedSizeChunker] Creating final chunk {chunk_index} for document {doc_id}: start_char={start_char}, length={len(chunk_text)}"
                )
                chunk = Chunk(
                    content=chunk_text,
                    chunk_id="",
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    metadata={
                        **metadata,
                        "chunk_size": len(chunk_text),
                        "chunker": "FixedSizeChunker",
                    },
                    start_char=start_char,
                    end_char=start_char + len(chunk_text),
                )
                chunks.append(chunk)
            else:
                logger.debug(
                    f"[FixedSizeChunker] Skipped empty final chunk at chunk_index={chunk_index} for document {doc_id}"
                )
        
        logger.info(f"[FixedSizeChunker] Created {len(chunks)} chunks from document {doc_id}")
        return chunks

