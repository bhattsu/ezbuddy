# Modularity Architecture

This document outlines the modular structure of the Knowledge Augmentation Framework application.

## Architecture Overview

The application follows a **layered, modular architecture** with clear separation of concerns, standardized for AIMatic projects:

```
┌───────────────────────────────────────────┐
│           API Layer (Endpoints)           │  ← HTTP/Request handling
├───────────────────────────────────────────┤
│          Service Layer (Orchestration)    │  ← Business process orchestration
├───────────────────────────────────────────┤
│          Core Layer (Business Logic)      │  ← Domain-specific logic (Pipelines, prompts)
├───────────────────────────────────────────┤
│          Adapters Layer (External)        │  ← External integrations (LLM, Vector DB)
└───────────────────────────────────────────┘
```

## Module Structure

### 1. API Layer (`app/api/`)

**Purpose**: Handle HTTP requests/responses, validation, routing, and dependency injection.

**Structure**:
```
api/
├── endpoints/          # HTTP endpoint handlers
│   ├── naive_rag.py
│   ├── ingestion.py    # Document ingestion endpoints (file, txt, text, directory, zip)
│   └── chatbot.py      # Unified chatbot API
├── schemas/            # Pydantic request/response models
│   ├── rag.py
│   ├── ingestion.py    # Ingestion schemas
│   └── chatbot.py      # Chatbot schemas
├── dependencies/       # FastAPI dependency injection components
│   └── __init__.py     # Common dependencies
├── middleware/         # Cross-cutting request processing logic
└── routes.py           # Route aggregation
```

**Principles**:
- ✅ Endpoints are **thin** - only handle HTTP concerns and validation.
- ✅ Business logic is decoupled into **services**.
- ✅ Each endpoint file is **separate** for horizontal scalability.
- ✅ Services are **reusable** across different delivery channels (API, CLI).

### 2. Service Layer (`app/services/`)

**Purpose**: Bridge between the API and Core logic. Orchestrates high-level business flows.

**Services**:
- `retrieval_service.py`: Orchestrates all RAG query execution.
- `ingestion_service.py`: Orchestrates document ingestion pipelines.
- `chatbot_service.py`: Manages session-based chatbot interactions and memory.

**Responsibilities**:
- Selecting the appropriate Core strategy via registries.
- Coordinating between multiple Core components.
- Logging and transaction/execution management.
- Formatting data for the delivery layer.

### 3. Core Layer (`app/core/`)

**Purpose**: Contains the fundamental business logic and domain definitions.

#### Prompts (`app/core/prompts/`)
```
core/prompts/
├── templates.py              # IDP key/value extraction prompts
├── document_generation.py    # Court-form HTML / FTL / AcroForm prompts
└── loader.py                 # Prompt version registry
```

#### Agents (`app/agents/`)
```
agents/
├── llm_client.py                 # Shared Bedrock/OpenAI invoke helpers
├── document_generation_agent.py  # VLM HTML / FTL / AcroForm calling
├── extraction_agent.py           # IDP LLM enhancement calling
└── prompts.py                    # Re-exports core document-gen prompts
```

#### RAG Pipelines (`app/core/pipelines/rag/`)
```
core/pipelines/rag/
├── base.py              # Abstract RAGStrategy base class
└── strategies/          # RAG algorithm implementations
    └── naive.py         # Sole supported strategy (Naive RAG)
```

#### Ingestion Pipelines (`app/core/pipelines/ingestion/`)
```
core/pipelines/ingestion/
├── pipeline.py          # Main ingestion orchestration
├── loaders/             # Document loaders (PDF, Text, Image, etc.)
│   ├── text.py
│   ├── pdf.py
│   ├── image.py
│   └── textract.py      # AWS Textract support
└── chunkers/            # Chunking strategies
    ├── fixed.py
    ├── semantic.py
    └── recursive.py
```

**Principles**:
- ✅ Domain logic implements strict interfaces (ABCs).
- ✅ Strategies are **self-contained unit-testable** units.
- ✅ No awareness of the delivery (HTTP) layer.

### 4. Adapters Layer (`app/adapters/`)

**Purpose**: Integration with third-party services and infrastructure.

**Structure**:
```
adapters/
├── llm/                 # Large Language Model providers
│   └── bedrock.py       # AWS Bedrock implementation
├── vector_store/        # Vector database integrations
│   ├── base.py          # Abstract adapter interface
│   └── pgvector.py
├── cache/               # Redis/Memory caching
├── memory/              # Session persistence layers
└── data_sources/        # External API or database clients
```

**Principles**:
- ✅ Adapters implement a common interface for easy swapping.
- ✅ Registry pattern allows dynamic loading of adapters at runtime.
- ✅ Isolation of infrastructure-specific SDKs (boto3, asyncpg, etc.).

## Modularity Checklist

### ✅ Separation of Concerns
- [x] Endpoints handle only HTTP concerns.
- [x] Services contain business orchestration.
- [x] Core pipelines contain pure domain logic.
- [x] Adapters handle external SDK specifics.

### ✅ Dependency Direction
- [x] `API` → `Services` → `Core` & `Adapters`.
- [x] No circular dependencies.
- [x] Domain layer (`Core`) has zero knowledge of the API layer.

### ✅ Registry Pattern
- [x] `VectorStoreRegistry` dynamically resolves database adapters.
- [x] `RAGStrategyRegistry` dynamically resolves RAG logic.

## Usage Examples

### Programmatic Usage (Without API)

```python
# Direct service usage with new import paths
from app.services.retrieval_service import RAGService
from app.services.ingestion_service import IngestionService

# Create services
rag_service = RAGService(vector_store, llm, embedder)
ingestion_service = IngestionService(vector_store, embedder)

# Use services
result = await rag_service.execute_naive_rag(...)
ingestion_result = await ingestion_service.ingest_file(...)
```

### API Usage

```bash
# Ingest a document
curl -X POST "http://localhost:8000/ingestion/file" \
  -F "file=@document.pdf" \
  -F "vector_store=pgvector" \
  -F "collection_name=my_collection"

# Query with RAG
curl -X POST "http://localhost:8000/rag/naive/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is RAG?",
    "vector_store": "pgvector",
    "collection_name": "my_collection"
  }'
```

## Benefits of the AIMatic Architecture

1.  **Pluggability**: Swap LLMs or Vector DBs by adding an adapter and registering it.
2.  **Extensibility**: Add new RAG strategies to `core/strategies` without touching the API layer.
3.  **Testability**: Each layer can be mocked independently.
4.  **Consistency**: Follows the organizational template for all AI products.
