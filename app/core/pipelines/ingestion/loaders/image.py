"""
Multimodal Image Loader

Loader for images that will be embedded using multimodal embedding models.
This is different from TextractLoader which extracts text from images.
"""

import logging
import base64
from typing import List, Optional
from pathlib import Path
import asyncio

from app.core.pipelines.ingestion.loaders.base import DocumentLoader, Document
from PIL import Image
import io

logger = logging.getLogger(__name__)


class ImageLoader(DocumentLoader):
    """
    Image loader for multimodal embeddings.
    
    Loads images and prepares them for embedding with multimodal models.
    Supports: PNG, JPEG, TIFF, WebP, BMP
    
    Automatically resizes images to comply with Bedrock model limits (default: 4096x4096).
    """

    # Default max size for Bedrock Titan Embed Image v1 (20M pixels = ~4096x4096)
    DEFAULT_MAX_SIZE = (4096, 4096)
    MAX_PIXELS = 20_000_000  # Bedrock Titan Embed Image v1 limit

    def __init__(
        self,
        max_image_size: Optional[tuple] = None,
        supported_formats: Optional[List[str]] = None
    ):
        """
        Initialize the image loader.
        
        Args:
            max_image_size: Optional tuple (width, height) to resize images.
                           Defaults to (4096, 4096) to comply with Bedrock limits.
            supported_formats: List of supported image formats (defaults to common formats).
        """
        # Use default if not specified to ensure Bedrock compatibility
        self.max_image_size = max_image_size or self.DEFAULT_MAX_SIZE
        self.supported_formats = supported_formats or [
            ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"
        ]
        logger.debug(f"ImageLoader initialized with max_size={self.max_image_size}, formats={self.supported_formats}")

    def _load_image_bytes(self, file_path: Path) -> bytes:
        """
        Load image file as bytes.
        
        Args:
            file_path: Path to image file.
            
        Returns:
            Image bytes.
        """
        with open(file_path, "rb") as f:
            return f.read()

    def _encode_image_base64(self, image_bytes: bytes) -> str:
        """
        Encode image bytes to base64 string.
        
        Args:
            image_bytes: Raw image bytes.
            
        Returns:
            Base64 encoded string.
        """
        return base64.b64encode(image_bytes).decode('utf-8')

    def _resize_image_if_needed(self, image_bytes: bytes) -> bytes:
        """
        Resize image to comply with max_image_size and pixel limits.
        
        Args:
            image_bytes: Original image bytes.
            
        Returns:
            Resized image bytes (or original if no resize needed).
        """
        try:
            image = Image.open(io.BytesIO(image_bytes))
            original_size = image.size
            original_pixels = original_size[0] * original_size[1]
            
            # Check if resizing is needed
            max_width, max_height = self.max_image_size
            max_pixels = max_width * max_height
            
            if original_pixels <= max_pixels and original_size[0] <= max_width and original_size[1] <= max_height:
                logger.debug(f"Image size {original_size} ({original_pixels} pixels) is within limits, no resizing needed")
                return image_bytes
            
            logger.info(f"Resizing image from {original_size} ({original_pixels} pixels) to max {self.max_image_size} ({max_pixels} pixels)")
            
            # Use thumbnail to preserve aspect ratio
            image.thumbnail(self.max_image_size, Image.Resampling.LANCZOS)
            
            # Verify final size
            final_size = image.size
            final_pixels = final_size[0] * final_size[1]
            logger.debug(f"Image resized to {final_size} ({final_pixels} pixels)")
            
            # Convert back to bytes
            output = io.BytesIO()
            # Preserve format
            format_ext = image.format or 'PNG'
            image.save(output, format=format_ext, optimize=True)
            return output.getvalue()
        except Exception as e:
            logger.warning(f"Failed to resize image: {e}. Using original size.", exc_info=True)
            return image_bytes

    async def load(self, source: str) -> List[Document]:
        """
        Load an image file for multimodal embedding.
        
        Args:
            source: Path to image file.
            
        Returns:
            List containing a single Document with image data.
        """
        file_path = Path(source)
        logger.info(f"Loading image file: {source}")
        
        if not file_path.exists():
            logger.error(f"Image file not found: {source}")
            return []
        
        if file_path.suffix.lower() not in self.supported_formats:
            logger.warning(f"Unsupported image format: {file_path.suffix}. Supported: {self.supported_formats}")
            return []
        
        try:
            # Load image bytes
            image_bytes = await asyncio.to_thread(self._load_image_bytes, file_path)
            logger.debug(f"Loaded {len(image_bytes)} bytes from {source}")
            
            # Always resize to ensure compliance with Bedrock limits
            image_bytes = await asyncio.to_thread(self._resize_image_if_needed, image_bytes)
            
            # Encode to base64 for embedding
            image_base64 = await asyncio.to_thread(self._encode_image_base64, image_bytes)
            
            # Get metadata
            metadata = self._get_file_metadata(file_path)
            metadata.update({
                "loader": "ImageLoader",
                "content_type": "image",
                "image_format": file_path.suffix.lower(),
                "image_size_bytes": len(image_bytes),
                "multimodal": True,
            })
            
            # Create document with base64 image as content
            # The embedder will handle base64 images
            doc = Document(
                content=image_base64,  # Base64 encoded image
                metadata=metadata,
                source=str(file_path.absolute()),
            )
            
            # Store original bytes in metadata for potential future use
            doc.metadata["image_bytes"] = image_bytes
            
            logger.info(f"Successfully loaded image: {source} (size: {len(image_bytes)} bytes)")
            return [doc]
            
        except Exception as e:
            logger.error(f"Failed to load image {source}: {e}", exc_info=True)
            return []

    async def load_directory(self, directory: str, pattern: str = "*") -> List[Document]:
        """
        Load all matching images from a directory.
        
        Args:
            directory: Path to directory.
            pattern: Glob pattern for file matching.
            
        Returns:
            List of Document objects.
        """
        dir_path = Path(directory)
        logger.info(f"Loading images from directory: {directory}, pattern: {pattern}")
        
        if not dir_path.exists():
            logger.error(f"Directory not found: {directory}")
            return []
        
        documents = []
        files_found = list(dir_path.glob(pattern))
        logger.debug(f"Found {len(files_found)} files matching pattern {pattern}")
        
        for file_path in files_found:
            if file_path.is_file() and file_path.suffix.lower() in self.supported_formats:
                logger.debug(f"Processing image file: {file_path}")
                docs = await self.load(str(file_path))
                if docs:
                    logger.debug(f"Loaded {len(docs)} document(s) from {file_path}")
                else:
                    logger.warning(f"No documents loaded from {file_path}")
                documents.extend(docs)
            else:
                logger.debug(f"Skipping file (unsupported format or not a file): {file_path}")
        
        logger.info(f"Loaded {len(documents)} images from {directory}")
        return documents

