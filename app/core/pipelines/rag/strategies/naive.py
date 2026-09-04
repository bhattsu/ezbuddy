"""
Naive RAG Strategy

The simplest RAG implementation: single retrieval followed by generation.
"""

import logging
from typing import List, Optional

from app.core.pipelines.rag.base import (
    RAGStrategy,
    RAGConfig,
    RAGResponse,
    RAGType,
    RetrievedDocument,
    RetrievalContext,
)
from app.config.registry import register_strategy

logger = logging.getLogger(__name__)


@register_strategy(RAGType.NAIVE)
class NaiveRAG(RAGStrategy):
    """
    Naive RAG implementation.
    
    This is the simplest RAG approach:
    1. Embed the query
    2. Retrieve top-k similar documents
    3. Generate response using retrieved context
    """

    @property
    def rag_type(self) -> RAGType:
        return RAGType.NAIVE

    async def retrieve(
        self,
        query: str,
        context: RetrievalContext
    ) -> List[RetrievedDocument]:
        """
        Retrieve relevant documents using vector similarity search.
        """
        try:
            logger.info(f"[NaiveRAG][retrieve] Embedding query: '{query}'")
            
            # Check if this is a multimodal query (image provided and not empty)
            has_image_base64 = context.query_image_base64 and context.query_image_base64.strip()
            has_image_bytes = context.query_image_bytes is not None and len(context.query_image_bytes) > 0
            is_multimodal = has_image_base64 or has_image_bytes
            
            logger.debug(f"[NaiveRAG][retrieve] Multimodal check: is_multimodal={is_multimodal}, "
                        f"has_image_base64={bool(has_image_base64)}, "
                        f"has_image_bytes={has_image_bytes}, "
                        f"has_embed_multimodal={hasattr(self.embedder, 'embed_multimodal')}")
            
            if is_multimodal and hasattr(self.embedder, 'embed_multimodal'):
                # Multimodal query: text + image or image only
                logger.info("[NaiveRAG][retrieve] Using multimodal embedding for query")
                query_embedding = await self.embedder.embed_multimodal(
                    text=query if query else None,
                    image_base64=context.query_image_base64 if has_image_base64 else None,
                    image_bytes=context.query_image_bytes if has_image_bytes else None
                )
                logger.debug(f"[NaiveRAG][retrieve] Multimodal query embedded successfully. Vector size: {len(query_embedding)}")
            else:
                # Text-only query
                if is_multimodal:
                    logger.warning("[NaiveRAG][retrieve] Multimodal query detected but embedder doesn't support it. Falling back to text embedding.")
                query_embedding = await self.embedder.embed(query)
                logger.debug(f"[NaiveRAG][retrieve] Text query embedded successfully. Vector size: {len(query_embedding)}")

            logger.info(
                f"[NaiveRAG][retrieve] Querying vector store: collection='{context.collection_name}', top_k={context.top_k}, filters={context.filters}, score_threshold={context.score_threshold}"
            )
            # Search vector store
            results = await self.vector_store.query(
                collection_name=context.collection_name,
                query_vector=query_embedding,
                top_k=context.top_k,
                filters=context.filters,
                score_threshold=context.score_threshold,
            )
            logger.debug(f"[NaiveRAG][retrieve] Vector store returned {len(results)} results.")

            # Convert to RetrievedDocument objects
            documents = []
            for result in results:
                doc = RetrievedDocument(
                    id=result.id,
                    content=result.payload.get("content", result.payload.get("metadata", "")),
                    score=result.score,
                    metadata=result.payload,
                    source=result.payload.get("source"),
                )
                logger.debug(f"[NaiveRAG][retrieve] RetrievedDocument created: id={doc.id}, score={doc.score}")
                documents.append(doc)
            
            logger.info(f"[NaiveRAG][retrieve] Retrieved {len(documents)} documents for query.")
            return documents
            
        except Exception as e:
            logger.error(f"[NaiveRAG][retrieve] Error in retrieval: {e}", exc_info=True)
            return []

    async def generate(
        self,
        query: str,
        documents: List[RetrievedDocument],
        system_prompt: Optional[str] = None,
        query_image_base64: Optional[str] = None,
        query_image_bytes: Optional[bytes] = None,
        **kwargs
    ) -> str:
        """
        Generate an answer using the LLM with retrieved context.
        Supports multimodal input by including images from retrieved documents.
        """
        try:
            logger.info(f"[NaiveRAG][generate] Preparing to generate answer. Query: '{query}' Document count: {len(documents)}")
            
            # Separate image and text documents
            image_documents = []
            text_documents = []
            for doc in documents:
                is_multimodal = (
                    doc.metadata.get("multimodal", False) or 
                    doc.metadata.get("content_type") == "image"
                )
                if is_multimodal:
                    image_documents.append(doc)
                else:
                    text_documents.append(doc)
            
            logger.debug(f"[NaiveRAG][generate] Found {len(image_documents)} image documents and {len(text_documents)} text documents")
            
            # Check if query image is provided
            has_query_image = (query_image_base64 and query_image_base64.strip()) or (query_image_bytes and len(query_image_bytes) > 0)
            logger.debug(f"[NaiveRAG][generate] Query image provided: {has_query_image}")
            
            # Build text context from text documents
            text_context = self._build_context(text_documents) if text_documents else ""
            
            # Use provided system prompt or default
            sys_prompt = system_prompt or self.get_default_system_prompt()
            logger.debug(f"[NaiveRAG][generate] Using system prompt: {sys_prompt[:80]}{'...' if len(sys_prompt) > 80 else ''}")

            # Build message content - support multimodal for Claude 3
            # LangChain ChatBedrock supports content as a list for multimodal
            user_content = []
            
            # Add query image first if provided (and no documents retrieved, or as additional context)
            if has_query_image:
                image_base64 = query_image_base64 if query_image_base64 and query_image_base64.strip() else None
                if not image_base64 and query_image_bytes:
                    import base64
                    image_base64 = base64.b64encode(query_image_bytes).decode('utf-8')
                
                if image_base64:
                    # Try to detect image format from base64 header or default to PNG
                    mime_type = self._detect_image_mime_type(query_image_bytes) if query_image_bytes else "image/png"
                    
                    user_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_base64
                        }
                    })
                    logger.debug(f"[NaiveRAG][generate] Added query image to message (mime_type: {mime_type}, base64_len: {len(image_base64)})")
            
            # Add images from retrieved documents (if any)
            for i, doc in enumerate(image_documents, 1):
                # Get base64 image from content
                image_base64 = doc.content
                if image_base64:
                    # Determine MIME type from metadata or default to PNG
                    image_format = doc.metadata.get("image_format", ".png")
                    mime_type_map = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                        ".bmp": "image/bmp",
                        ".tiff": "image/tiff",
                        ".tif": "image/tiff"
                    }
                    mime_type = mime_type_map.get(image_format.lower(), "image/png")
                    
                    # LangChain ChatBedrock format for Claude: content blocks with image
                    # Format: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
                    user_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_base64
                        }
                    })
                    logger.debug(f"[NaiveRAG][generate] Added image {i} to message (format: {image_format}, mime: {mime_type}, base64_len: {len(image_base64)})")
            
            # Build prompt text
            prompt_text = ""
            if text_context:
                prompt_text = f"""Based on the following context, answer the question.

Context:
{text_context}

Question: {query}

Answer:"""
            else:
                if has_query_image or image_documents:
                    # Enhanced prompt for image queries
                    if not query or query.strip() == "":
                        # Image-only query - provide detailed analysis
                        if image_documents:
                            prompt_text = """Please analyze the images provided below. For each image, provide:

1. A detailed description of what you see (objects, people, activities, settings, colors, etc.)
2. Any visible text, logos, or branding
3. The context or scene (e.g., sports event, vehicle, nature, etc.)
4. Any notable details or characteristics

If multiple images are provided, describe each one separately and clearly distinguish between them. Be specific and accurate in your descriptions."""
                        else:
                            # Only query image, no retrieved images
                            prompt_text = """Please analyze the image provided below. Provide:

1. A detailed description of what you see (objects, people, activities, settings, colors, etc.)
2. Any visible text, logos, or branding
3. The context or scene (e.g., sports event, vehicle, nature, etc.)
4. Any notable details or characteristics

Be specific and accurate in your description."""
                    else:
                        # Text query with images
                        if image_documents:
                            prompt_text = f"""Answer the following question about the image(s) provided below. 

When describing images, be specific and accurate. If multiple images are shown, clearly identify which image you're referring to.

Question: {query}

Answer:"""
                        else:
                            prompt_text = f"""Answer the following question about the image provided below.

Question: {query}

Answer:"""
                else:
                    prompt_text = f"""I couldn't find any relevant documents in the knowledge base to answer your question.

Question: {query}

Please note that no matching documents were found. You may want to try rephrasing your question or checking if the relevant information has been ingested into the system."""
            
            # Add text prompt (after images if any, or as only content if no images)
            if prompt_text:
                if user_content or has_query_image or image_documents:
                    # If we have images, add text as part of the content list
                    user_content.append({
                        "type": "text",
                        "text": prompt_text
                    })
                else:
                    # If no images, just use text string
                    user_content = prompt_text
            
            # Build messages - Claude 3 supports multimodal content
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content}
            ]
            logger.debug(f"[NaiveRAG][generate] Messages constructed for LLM with {len(user_content)} content blocks ({len(image_documents)} images)")

            # Generate response
            logger.info(f"[NaiveRAG][generate] Invoking LLM for generation.")
            response = await self.llm.invoke(messages)
            logger.info("[NaiveRAG][generate] LLM invocation complete.")

            return response
            
        except Exception as e:
            logger.error(f"[NaiveRAG][generate] Error in generation: {e}", exc_info=True)
            return f"I encountered an error generating a response: {str(e)}"

    async def execute(
        self,
        query: str,
        config: RAGConfig
    ) -> RAGResponse:
        """
        Execute the full Naive RAG pipeline.
        """
        logger.info(f"[NaiveRAG][execute] Starting execution for query: '{query}' with config: {config}")
        # Build retrieval context with multimodal support
        retrieval_context = RetrievalContext(
            collection_name=config.collection_name,
            top_k=config.top_k,
            filters=config.filters,
            score_threshold=config.score_threshold,
            query_text=query,
            query_image_base64=config.extra_params.get("query_image_base64"),
            query_image_bytes=config.extra_params.get("query_image_bytes"),
        )
        logger.debug(f"[NaiveRAG][execute] RetrievalContext built: {retrieval_context}")
        
        # Retrieve documents
        logger.info(f"[NaiveRAG][execute] Retrieving documents...")
        documents = await self.retrieve(query, retrieval_context)
        logger.info(f"[NaiveRAG][execute] Retrieved {len(documents)} documents.")

        # Generate response
        logger.info(f"[NaiveRAG][execute] Generating answer from retrieved documents...")
        answer = await self.generate(
            query=query,
            documents=documents,
            system_prompt=config.system_prompt,
            query_image_base64=config.extra_params.get("query_image_base64"),
            query_image_bytes=config.extra_params.get("query_image_bytes"),
        )
        logger.info(f"[NaiveRAG][execute] Answer generated.")

        # Calculate confidence
        confidence = self._calculate_confidence(documents)
        logger.debug(f"[NaiveRAG][execute] Confidence calculated: {confidence}")

        # Build response
        logger.info("[NaiveRAG][execute] Building RAGResponse object.")
        response = RAGResponse(
            answer=answer,
            sources=self._documents_to_sources(documents),
            rag_type=self.rag_type.value,
            vector_store=config.vector_store,
            confidence=confidence,
            metadata={
                "total_documents": len(documents),
                "top_k": config.top_k,
            }
        )
        logger.info("[NaiveRAG][execute] Execution complete. Returning response.")
        return response

