"""Factory to create the AWS Textract extractor."""

from app.adapters.file_extraction.extractors.textract.extractor import AWSTextractExtractor


class ExtractorFactory:
    """Creates the AWS Textract extractor (sole supported extractor)."""

    @staticmethod
    def create():
        """Create and return the AWS Textract extractor instance."""
        return AWSTextractExtractor()