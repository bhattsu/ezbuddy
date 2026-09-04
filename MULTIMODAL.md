# Multimodal RAG Support

This document describes the multimodal (text + image) query and ingestion capabilities of the Knowledge Augmentation Framework.

## Overview

The boilerplate now supports:
- **Image ingestion** with multimodal embeddings (not just OCR).
- **Multimodal queries** (text + image, image-only, or text-only).
- **Visual similarity search** using image embeddings.
- **Combined text + image queries** for richer context.

## Architecture

### Components

1. **ImageLoader** (`app/core/pipelines/ingestion/loaders/image.py`)
   - Loads images for multimodal embedding.
   - Converts images to base64 for embedding models.
   - Supports: PNG, JPEG, TIFF, WebP, BMP.
   - Optional image resizing for model compatibility.

2. **Multimodal Embedder** (`app/adapters/llm/bedrock.py`)
   - `embed_multimodal()` method supports:
     - Text-only (falls back to text embedder).
     - Image-only (requires multimodal model).
     - Combined text + image query vectors.

3. **Ingestion Pipeline** (`app/core/pipelines/ingestion/pipeline.py`)
   - Auto-detects image documents and assigns appropriate loaders.
   - Skips chunking for images (treats as single unit).
   - Uses multimodal embedding for images.

4. **RAG Strategies** (`app/core/pipelines/rag/strategies/`)
   - All strategies support multimodal queries.
   - `RetrievalContext` includes image query fields.
   - Automatic detection of multimodal vs text queries.

## Configuration

### Environment Variables

Add to your `.env` file:

```bash
# Multimodal Embedding Model (required for image embeddings)
BEDROCK_MULTIMODAL_EMBEDDING_MODEL_ID=amazon.titan-embed-image-v1
# Or: amazon.titan-multimodal-v1
```

### Model Selection

The system automatically selects the appropriate loader:
- **If multimodal model configured**: Uses `ImageLoader` for image embeddings.
- **If no multimodal model**: Falls back to `TextractLoader` for OCR processing.

## Usage

### 1. Ingest Images for Embedding

#### Via API

```bash
# Upload image file
curl -X POST "http://localhost:8000/ingestion/file" \
  -F "file=@image.png" \
  -F "vector_store=pgvector" \
  -F "collection_name=images" \
  -F "loader_type=image"  # Force image loader
```

#### Via Code

```python
from app.core.pipelines.ingestion.loaders.image import ImageLoader
from app.core.pipelines.ingestion.pipeline import IngestionPipeline, IngestionConfig
from app.adapters.llm.bedrock import Bedrock
from app.adapters.vector_store.pgvector import PgVectorAdapter

# Initialize components
embedder = Bedrock()  # Must have multimodal model configured
vector_store = PgVectorAdapter(config)
loader = ImageLoader()

# Create pipeline
pipeline = IngestionPipeline(
    vector_store=vector_store,
    embedder=embedder,
    chunker=FixedSizeChunker(),  # Not used for images
    loader=loader
)

# Ingest image
config = IngestionConfig(collection_name="images")
result = await pipeline.ingest_file("path/to/image.png", config)
```

### 2. Query with Images

#### Via API - JSON (Base64 Image)

```bash
# Convert image to base64 first
IMAGE_B64=$(base64 -i image.png)

curl -X POST "http://localhost:8000/rag/naive/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is in this image?",
    "query_image_base64": "'$IMAGE_B64'",
    "vector_store": "pgvector",
    "collection_name": "images",
    "top_k": 5
  }'
```

#### Via API - Form Upload

```bash
curl -X POST "http://localhost:8000/rag/naive/query-multimodal" \
  -F "query=What is in this image?" \
  -F "image=@query_image.png" \
  -F "vector_store=pgvector" \
  -F "collection_name=images" \
  -F "top_k=5"
```

#### Via Code

```python
from app.services.retrieval_service import RAGService
import base64

# Read image
with open("query_image.png", "rb") as f:
    image_bytes = f.read()
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')

# Query
service = RAGService(vector_store, llm, embedder)
result = await service.execute_naive_rag(
    query="What is in this image?",
    vector_store_name="pgvector",
    collection_name="images",
    query_image_base64=image_base64
)
```

## How It Works

### Ingestion Flow

```
Image File
    ↓
ImageLoader
    ↓
Base64 Encoding
    ↓
Document (content=base64, metadata={multimodal: True})
    ↓
Pipeline (skips chunking for images)
    ↓
Multimodal Embedder
    ↓
Image Embedding Vector
    ↓
Vector Store
```

### Query Flow

```
Query (text + image)
    ↓
Multimodal Embedder
    ↓
Query Embedding Vector
    ↓
Vector Store Search
    ↓
Retrieved Images/Documents
    ↓
LLM Generation (with image context)
    ↓
Response
```

## Implementation Details

### Image Handling

- Images are **not chunked** - treated as single embedding units.
- Base64 encoding for model compatibility.
- Automatic format detection and validation.

### Vector Store Compatibility

All vector stores support multimodal embeddings (this bundle uses PostgreSQL + pgvector):
- Metadata includes `multimodal: true` flag.
- Same vector dimension requirements as text embeddings.

## Notes

- **Multimodal model required**: Image embeddings require a configured multimodal embedding model.
- **Vector dimensions**: Ensure multimodal model output dimension matches your vector store configuration.
- **Storage**: Image base64 strings are stored in metadata for efficiency.
- **Performance**: Multimodal embeddings are typically larger - consider batch sizes.
