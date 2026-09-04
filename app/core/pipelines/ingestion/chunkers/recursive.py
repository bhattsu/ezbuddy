"""
Recursive Character Text Splitter

Chunking strategy that recursively splits text using a hierarchy of separators.
"""

import logging
from typing import List, Dict, Any, Optional

from app.core.pipelines.ingestion.chunkers.base import ChunkingStrategy, Chunk

logger = logging.getLogger(__name__)


class RecursiveChunker(ChunkingStrategy):
    """
    Recursive chunking using a hierarchy of separators.
    
    Attempts to split text at the most meaningful boundaries first,
    falling back to smaller separators as needed.
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: List[str] = None,
        keep_separator: bool = True
    ):
        """
        Initialize the recursive chunker.
        
        Args:
            chunk_size: Target size for each chunk.
            chunk_overlap: Overlap between consecutive chunks.
            separators: List of separators in order of preference.
            keep_separator: Whether to keep separators in the output.
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or [
            "\n\n",  # Double newline (paragraphs)
            "\n",    # Single newline
            ". ",    # Sentence end
            ", ",    # Clause
            " ",     # Word
            "",      # Character (last resort)
        ]
        self.keep_separator = keep_separator

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
        """Recursively split text using the separator hierarchy."""
        if not text:
            logger.debug("No text received for splitting; returning empty list.")
            return []
        
        # Base case: no more separators, split by character
        if not separators:
            logger.debug("No more separators. Performing character-level split.")
            split_by_char = [text[i:i+self.chunk_size] for i in range(0, len(text), self.chunk_size)]
            logger.debug(f"Character-level split produced {len(split_by_char)} chunks.")
            return split_by_char
        
        separator = separators[0]
        remaining_separators = separators[1:]
        logger.debug(f"Attempting to split using separator: '{separator}' "
                     f"with {len(text)} chars remaining.")

        # Handle empty separator (character-level split)
        if separator == "":
            logger.debug("Empty separator reached, performing character-level split again.")
            split_by_char = [text[i:i+self.chunk_size] for i in range(0, len(text), self.chunk_size)]
            logger.debug(f"Character-level split produced {len(split_by_char)} chunks.")
            return split_by_char
        
        # Split by this separator
        splits = text.split(separator)
        logger.debug(f"Splitting by '{separator}': {len(splits)} splits.")

        # If split didn't work, try next separator
        if len(splits) == 1:
            logger.debug(f"Separator '{separator}' did not work, trying next in hierarchy.")
            return self._split_text(text, remaining_separators)
        
        # Merge splits back together while respecting chunk_size
        chunks = []
        current_chunk = []
        current_length = 0
        
        for idx, split in enumerate(splits):
            split_with_sep = split + (separator if self.keep_separator else "")
            split_length = len(split_with_sep)
            
            # If single split is too large, recursively split it
            if split_length > self.chunk_size:
                logger.debug(
                    f"Split index {idx} with length {split_length} > chunk_size {self.chunk_size}. "
                    "Recursively splitting this part."
                )
                # First, add current chunk if not empty
                if current_chunk:
                    chunk_text = separator.join(current_chunk) if not self.keep_separator else "".join(current_chunk)
                    logger.debug(
                        f"Adding current chunk before recursion: {chunk_text[:50]}... (length {len(chunk_text)})"
                    )
                    chunks.append(chunk_text)
                    current_chunk = []
                    current_length = 0
                
                # Recursively split the large piece
                sub_chunks = self._split_text(split, remaining_separators)
                logger.debug(f"Recursively split produced {len(sub_chunks)} sub-chunks.")
                chunks.extend(sub_chunks)
                continue
            
            # If adding this split would exceed chunk_size
            if current_length + split_length > self.chunk_size and current_chunk:
                chunk_text = separator.join(current_chunk) if not self.keep_separator else "".join(current_chunk)
                logger.debug(
                    f"Adding chunk: {chunk_text[:50]}... (length {len(chunk_text)}) "
                    f"because adding new split ({split_length}) would exceed chunk_size."
                )
                chunks.append(chunk_text)
                
                # Calculate overlap
                overlap_text = ""
                if self.chunk_overlap > 0:
                    overlap_length = 0
                    overlap_parts = []
                    for part in reversed(current_chunk):
                        if overlap_length + len(part) <= self.chunk_overlap:
                            overlap_parts.insert(0, part)
                            overlap_length += len(part) + len(separator)
                        else:
                            break
                    logger.debug(
                        f"Building overlap region of length {overlap_length} across {len(overlap_parts)} parts."
                    )
                    current_chunk = overlap_parts
                    current_length = sum(len(p) + len(separator) for p in current_chunk)
                else:
                    current_chunk = []
                    current_length = 0
            
            current_chunk.append(split_with_sep if self.keep_separator else split)
            current_length += split_length
        
        # Add remaining chunk
        if current_chunk:
            chunk_text = separator.join(current_chunk) if not self.keep_separator else "".join(current_chunk)
            logger.debug(
                f"Adding final chunk: {chunk_text[:50]}... (length {len(chunk_text)})"
            )
            chunks.append(chunk_text)
        
        logger.debug(f"_split_text created {len(chunks)} chunks at this recursion level.")
        return chunks

    def chunk(
        self,
        text: str,
        doc_id: str,
        metadata: Dict[str, Any] = None
    ) -> List[Chunk]:
        """
        Split text into chunks using recursive splitting.
        
        Args:
            text: The text to chunk.
            doc_id: ID of the source document.
            metadata: Optional metadata to include in chunks.
            
        Returns:
            List of Chunk objects.
        """
        if not text:
            logger.warning(f"No text received for chunking in document {doc_id}.")
            return []
        
        metadata = metadata or {}
        
        logger.info(
            f"Starting chunking for document {doc_id} with length {len(text)}."
        )
        # Get raw text chunks
        text_chunks = self._split_text(text, self.separators)
        logger.info(
            f"Text was split into {len(text_chunks)} preliminary chunks in document {doc_id}."
        )
        
        # Clean and convert to Chunk objects
        chunks = []
        for i, chunk_text in enumerate(text_chunks):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                logger.debug(
                    f"Skipping empty or whitespace-only chunk at index {i}."
                )
                continue
            
            chunk = Chunk(
                content=chunk_text,
                chunk_id="",
                doc_id=doc_id,
                chunk_index=i,
                metadata={
                    **metadata,
                    "chunk_size": len(chunk_text),
                    "chunker": "RecursiveChunker",
                },
            )
            logger.debug(
                f"Chunk {i}: size={len(chunk_text)}, "
                f"first 40 chars='{chunk_text[:40]}', metadata={chunk.metadata}"
            )
            chunks.append(chunk)
        
        # Reindex after cleaning
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
        
        logger.info(f"Created {len(chunks)} recursive chunks from document {doc_id}")
        return chunks

