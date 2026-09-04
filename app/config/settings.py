"""
Application Settings

Unified configuration for RAG + IDP capabilities.
Loaded from environment variables via Pydantic settings.
"""

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env manually (skip in Lambda)
if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(dotenv_path=os.path.abspath(env_path))
    load_dotenv()

IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


class Settings(BaseSettings):
    """Unified application settings for RAG and IDP."""

    # ============== Environment ==============
    ENV: str = Field(default="development")
    APP_NAME: str = Field(default="US Legal Pro - Unified RAG + IDP")
    DEBUG: bool = Field(default=False)
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: str = Field(default="text")  # text | json

    # ============== AWS General ==============
    AWS_REGION: str = Field(default="us-east-1")
    AWS_ACCESS_KEY_ID: Optional[str] = Field(default=None)
    AWS_SECRET_ACCESS_KEY: Optional[str] = Field(default=None)
    AWS_VERIFY_SSL: bool = Field(default=True)

    # ============== AWS Bedrock (RAG embeddings) ==============
    # LLM chat/generation uses MODEL_ID under IDP - LLM (single source of truth)
    BEDROCK_EMBEDDING_MODEL_ID: str = Field(default="amazon.titan-embed-text-v2:0")
    BEDROCK_MULTIMODAL_EMBEDDING_MODEL_ID: Optional[str] = Field(default=None)

    # ============== AWS Textract (RAG ingestion loader) ==============
    TEXTRACT_REGION: Optional[str] = Field(default=None)
    TEXTRACT_S3_BUCKET: Optional[str] = Field(default=None)

    # ============== Default RAG Settings ==============
    DEFAULT_VECTOR_STORE: str = Field(default="opensearch")
    DEFAULT_RAG_TYPE: str = Field(default="naive")
    DEFAULT_TOP_K: int = Field(default=10)
    DEFAULT_SCORE_THRESHOLD: float = Field(default=0.0)
    DEFAULT_VECTOR_DIM: int = Field(default=1536)
    PGVECTOR_SCHEMA: str = Field(default="public")

    # Court rules knowledge (generic_legal chat RAG → OpenSearch)
    COURT_RULES_VECTOR_STORE: str = Field(default="opensearch")
    COURT_RULES_COLLECTION: str = Field(default="court_rules")
    COURT_RULES_TOP_K: int = Field(default=3)
    COURT_RULES_SCORE_THRESHOLD: float = Field(default=0.0)
    COURT_RULES_CHUNK_SIZE: int = Field(default=1800)
    COURT_RULES_CHUNK_OVERLAP: int = Field(default=150)

    # Amazon OpenSearch Service
    OPENSEARCH_ENDPOINT: Optional[str] = Field(
        default=None,
        description="OpenSearch domain endpoint, e.g. https://search-xxx.us-east-1.es.amazonaws.com",
        validation_alias=AliasChoices("OPENSEARCH_ENDPOINT", "opensearch_endpoint"),
    )
    OPENSEARCH_PORT: int = Field(default=443)
    OPENSEARCH_REGION: Optional[str] = Field(default=None)
    OPENSEARCH_USE_AWS_AUTH: bool = Field(default=True)
    OPENSEARCH_USE_SSL: bool = Field(default=True)
    OPENSEARCH_VERIFY_CERTS: bool = Field(default=True)
    OPENSEARCH_USERNAME: Optional[str] = Field(default=None)
    OPENSEARCH_PASSWORD: Optional[str] = Field(default=None)
    OPENSEARCH_KNN_ENGINE: str = Field(default="nmslib")
    OPENSEARCH_EF_SEARCH: int = Field(default=100)
    OPENSEARCH_EF_CONSTRUCTION: int = Field(default=128)
    OPENSEARCH_HNSW_M: int = Field(default=16)
    OPENSEARCH_NUMBER_OF_SHARDS: int = Field(default=1)
    OPENSEARCH_NUMBER_OF_REPLICAS: int = Field(default=1)
    OPENSEARCH_TIMEOUT: int = Field(default=60)
    OPENSEARCH_AWS_SERVICE: str = Field(
        default="es",
        description="SigV4 service name: es (managed domain) or aoss (serverless)",
    )

    # RDS (PostgreSQL / pgvector) — accepts RDS_HOST / rds_host etc. from .env
    rds_host: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RDS_HOST", "rds_host"),
    )
    rds_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("RDS_PORT", "rds_port"),
    )
    rds_database: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RDS_DATABASE", "rds_database"),
    )
    rds_username: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RDS_USERNAME", "rds_username"),
    )
    rds_password: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("RDS_PASSWORD", "rds_password"),
    )

    # ============== Legal Filing Chatbot ==============
    DOCUMENT_TEMPLATE_S3_KEY: str = Field(
        default="",
        description="Fallback S3 key for court PDF templates when RDS SQL returns none",
    )
    DOCUMENT_GENERATION_API_URL: str = Field(
        default="http://localhost:8000/api/generate",
        description="Document generation API URL (internal service uses DocumentGenerationService directly)",
    )
    DOCUMENT_ANALYSIS_API_URL: str = Field(
        default="http://localhost:8000/api/analyze",
        description="Document analysis API URL",
    )
    CHATBOT_WS_ENABLED: bool = Field(default=True)

    # ============== US Legal Pro external API (codes / dropdowns) ==============
    USLEGALPRO_API_BASE_URL: str = Field(
        default="https://api-stage.uslegalpro.com",
        description="Base URL for codes API (no trailing slash); Excel endpoint paths are appended",
    )
    USLEGALPRO_CLIENT_TOKEN: Optional[str] = Field(default=None)
    USLEGALPRO_AUTH_TOKEN: Optional[str] = Field(default=None)
    USLEGALPRO_API_TIMEOUT_SECONDS: float = Field(default=30.0)

    # ============== IDP - LLM ==============
    LLM_PROVIDER: str = Field(default="bedrock")
    MODEL_ID: str = Field(
        default="",
        description="Single Bedrock LLM model / inference-profile ID for all LLM calls",
    )
    OPENAI_API_KEY: str = Field(default="")
    OPENAI_MODEL: str = Field(default="gpt-4o")

    # ============== IDP - Extractor ==============
    EXTRACTOR_TYPE: str = Field(default="AWS_textract")
    BUCKET_NAME: str = Field(default="")

    # ============== IDP - File Processing Limits ==============
    MAX_FILE_SIZE_MB: int = Field(default=100)
    MAX_PAGES_PER_REQUEST: int = Field(default=50)
    CHUNK_SIZE_MB: int = Field(default=10)
    MAX_BATCH_FILES: int = Field(default=10)

    # ============== IDP - Timeouts (seconds) ==============
    TEXTRACT_TIMEOUT: int = Field(default=300)
    LLM_TIMEOUT: int = Field(default=1800)
    # Document generation VLM (full PDF in one call) — keep high for multi-page forms
    DOC_GEN_TIMEOUT: int = Field(default=1800)
    DOC_GEN_PAGES_PER_CHUNK: int = Field(default=2)
    DOC_GEN_PDF_DPI: int = Field(
        default=200,
        description=(
            "DPI when rendering PDF pages to PNG for VLM overlays "
            "(higher = sharper exact form background)."
        ),
    )
    DOC_GEN_VLM_CONCURRENCY: int = Field(
        default=3,
        description=(
            "Max parallel per-page VLM calls for /api/generate. "
            "Example: 9 pages with concurrency 3 → 3 sequential batches of 3."
        ),
    )
    DOC_GEN_MAX_TOKENS: int = Field(
        default=30000,
        description="max_tokens for document-generation Bedrock/VLM calls.",
    )
    FILE_UPLOAD_TIMEOUT: int = Field(default=120)
    SHUTDOWN_DRAIN_SECONDS: int = Field(default=30)

    # ============== IDP - Concurrency ==============
    MAX_WORKERS: int = Field(default=4)
    MAX_CONCURRENT_PAGES: int = Field(default=3)

    # ============== IDP - S3 ==============
    S3_PREFIX: str = Field(default="document-uploads")
    S3_TEMP_EXPIRY_DAYS: int = Field(default=1)
    # Legal package ZIP asset prefixes inside BUCKET_NAME (goml-uslegalpro-dev)
    S3_DOCUMENTS_REPO_PREFIX: str = Field(default="documents-repo")
    S3_KNOWLEDGE_PREFIX: str = Field(default="knowledge")
    S3_PACKAGE_ASSETS_PREFIX: str = Field(default="documents-repo")

    # ============== IDP - Retry ==============
    MAX_RETRIES: int = Field(default=3)
    RETRY_DELAY: float = Field(default=1.0)

    # ============== IDP - Health Checks ==============
    HEALTH_CHECK_S3: bool = Field(default=False)
    HEALTH_CHECK_BEDROCK: bool = Field(default=False)

    # ============== IDP - Auth ==============
    AUTH_METHOD: str = Field(default="none")
    API_KEY: str = Field(default="")
    API_KEYS: str = Field(default="")
    BEARER_TOKEN: str = Field(default="")
    JWT_SECRET: str = Field(default="")
    JWT_ALGORITHM: str = Field(default="HS256")

    # ============== IDP - Rate Limiting ==============
    RATE_LIMIT_ENABLED: bool = Field(default=False)
    RATE_LIMIT_PER_MINUTE: int = Field(default=60)

    # ============== IDP - Security ==============
    ALLOWED_UPLOAD_CONTENT_TYPES: str = Field(default="")

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return (
            f"postgresql+asyncpg://{self.rds_username}:{self.rds_password}"
            f"@{self.rds_host}:{self.rds_port}/{self.rds_database}"
        )

    @computed_field
    @property
    def SYNC_DATABASE_URI(self) -> str:
        return (
            f"postgresql://{self.rds_username}:{self.rds_password}"
            f"@{self.rds_host}:{self.rds_port}/{self.rds_database}"
        )

    model_config = SettingsConfigDict(
        env_file=".env" if not IS_LAMBDA else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        populate_by_name=True,
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def get_vector_store_config(store_type: str) -> dict:
    """Get configuration dict for a specific vector store type."""
    configs = {
        "pgvector": {
            "host": settings.rds_host,
            "port": settings.rds_port,
            "user": settings.rds_username,
            "password": settings.rds_password,
            "database": settings.rds_database,
            "schema": settings.PGVECTOR_SCHEMA,
        },
    }

    if store_type not in configs:
        raise ValueError(f"Unknown vector store type: {store_type}")

    return configs[store_type]
