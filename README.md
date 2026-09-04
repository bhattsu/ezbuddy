# AIMatic RAG Boilerplate

Welcome to the **AIMatic RAG Boilerplate**, a standardized, production-ready backend framework designed for building Retrieval-Augmented Generation (RAG) applications across the organization.

## Purpose

This boilerplate simplifies the creation of AI-powered systems by providing a pre-configured architecture that supports Naive RAG, vector databases, and multimodal interactions. It follows the **AIMatic Standard Project Structure**, ensuring consistency and modularity across all internal projects.

---

## High-Level Flow

1.  **Ingestion**: Documents are loaded, chunked, and embedded via the **Core Pipeline** and stored in the **Vector Adapter**.
2.  **Request**: An external client calls the **API Layer**.
3.  **Orchestration**: The **Service Layer** resolves Naive RAG and the vector store.
4.  **Retrieval**: The **Core Pipeline** searches for context within the **Vector Adapter**.
5.  **Generation**: The **LLM Adapter** (AWS Bedrock/Claude 3) generates a grounded response.

---

## Developer Handbook: Where is everything?

| Component | Location | Responsibility |
| :--- | :--- | :--- |
| **API Endpoints** | `app/api/endpoints/` | HTTP request handling, input validation. |
| **Pydantic Schemas** | `app/api/schemas/` | Request/Response data models. |
| **Business Services** | `app/services/` | Logic orchestration (chatbot, retrieval, ingestion). |
| **RAG Strategies** | `app/core/pipelines/rag/strategies/` | RAG logic (Naive, Advanced, Agentic, etc.). |
| **Ingestion Logic** | `app/core/pipelines/ingestion/` | Document loaders and chunking algorithms. |
| **Third-party Adapters** | `app/adapters/` | SDK integrations for LLMs and Vector DBs. |
| **Prompts** | Inside Strategy classes in `app/core/` | Prompt templates for LLM generation. |
| **Configuration** | `app/config/settings.py` & `.env` | Environment variables and global settings. |

---

## How to Utilize this Boilerplate

### 1. Selecting RAG Type
The system uses Naive RAG via a **Registry Pattern** defined in `app/config/registry.py`.

### 2. Modifying Prompts
Prompts are currently encapsulated within the strategy classes (e.g., `app/core/pipelines/rag/strategies/naive.py`). To modify the system's behavior, navigate to the relevant strategy and update the `_build_context` or `generate` methods.

### 3. Adding a New RAG Strategy
1.  Create a new file in `app/core/pipelines/rag/strategies/`.
2.  Inherit from `RAGStrategy` in `base.py`.
3.  Use the `@register_strategy(RAGType.YOUR_TYPE)` decorator.
4.  Implement the `retrieve` and `generate` methods.

### 4. Adding a New Vector Store
1.  Add a new adapter in `app/adapters/vector_store/`.
2.  Inherit from `VectorStoreAdapter` in `base.py`.
3.  Use the `@register_adapter("your_store_name")` decorator.

---

## Setup & Local Development

### Prerequisites
- Python 3.10+
- AWS Credentials (for Bedrock)
- Access to PostgreSQL with the pgvector extension (vector store)

### Installation
```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Update .env with your AWS_REGION, MODEL_ID, RDS_*, etc.

# Run the project
fastapi dev app/main.py
```

### Documentation Links
- [API Documentation](API_DOCUMENTATION.md)
- [Modularity Architecture](MODULARITY.md)
- [Multimodal Support](MULTIMODAL.md)

---

## Consumption for Individual Projects

To use this boilerplate for your specific project:
1.  **Clone/Copy** the `Backend/` folder.
2.  **Define your Schema**: Update `app/api/schemas/` if your model requirements differ.
3.  **Configure Logic**: Select your preferred `rag_strategy` and `vector_store` in the `.env`.
4.  **Custom Prompts**: Tailor the language templates in `app/core/pipelines/rag/strategies/` to your domain.

---
**Standardized for AIMatic Compliance**
