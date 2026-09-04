"""Base extractor interface."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.api.schemas.document import (
        ExtractionRequest,
        ExtractionResponse,
        FileType,
    )


class BaseExtractor(ABC):
    """Base class for document extractors."""

    @abstractmethod
    async def extract(
        self,
        file_path: str,
        file_bytes: bytes,
        file_name: str,
        file_type: "FileType",
        request: "ExtractionRequest",
        document_id: str,
    ) -> "ExtractionResponse":
        """Extract data from document. Must be implemented by subclasses."""
        ...

    def get_extractor_name(self) -> str:
        """Return a short name for this extractor (e.g. for logging)."""
        return self.__class__.__name__
