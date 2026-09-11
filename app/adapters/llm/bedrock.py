"""
AWS Bedrock Client

Single entry point for all LLM and embedding operations.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from typing import Any, Dict, List, Optional, Type, TypeVar

import boto3
from botocore.config import Config
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage
from PIL import Image
from pydantic import BaseModel

from app.config.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_shared_bedrock: Optional["Bedrock"] = None
_llm_tokens_total: List[int] = [0]


def _bedrock_runtime_config() -> Config:
    """Long read timeout for large VLM/HTML generation responses."""
    read_timeout = max(
        1800,
        int(getattr(settings, "DOC_GEN_TIMEOUT", None) or settings.LLM_TIMEOUT or 1800)
        + 120,
    )
    return Config(
        read_timeout=read_timeout,
        connect_timeout=60,
        retries={"max_attempts": 3, "mode": "standard"},
    )


def get_bedrock() -> "Bedrock":
    """Return the process-wide Bedrock instance (LLM + embeddings)."""
    global _shared_bedrock
    if _shared_bedrock is None:
        _shared_bedrock = Bedrock()
    return _shared_bedrock


def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    if not text or not text.strip():
        return None
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_str = text[start_idx : end_idx + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("JSON decode error: %s", str(e))
            json_str = re.sub(r",\s*}", "}", json_str)
            json_str = re.sub(r",\s*]", "]", json_str)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    return None


def _sanitize_bedrock_prompt_body(prompt_body: Dict[str, Any], model_id: str = "") -> Dict[str, Any]:
    body = dict(prompt_body)
    if "temperature" in body and "top_p" in body:
        logger.debug("Dropping top_p because temperature is set (model allows only one)")
        body.pop("top_p", None)
    mid = (model_id or "").lower()
    if "response_format" in body and ("anthropic" in mid or "claude" in mid):
        logger.debug("Dropping response_format for Anthropic InvokeModel")
        body.pop("response_format", None)
    return body


class Bedrock:
    """
    AWS Bedrock client for LLM and embedding operations.

    LLM paths:
    - ``invoke`` / LangChain messages (RAG chat)
    - ``invoke_prompt_body`` (agents with images, Anthropic content blocks)
    - ``invoke_structured_with_schema`` (Pydantic-typed text JSON)

    Non-LLM:
    - ``embed`` / ``embed_batch`` / ``embed_multimodal`` (RAG vector store)
    """

    def __init__(self):
        """Initialize the Bedrock client."""
        logger.info("Initializing Bedrock client...")
        self.bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
            config=_bedrock_runtime_config(),
        )
        
        self.llm_model_id = settings.MODEL_ID
        self.embedding_model_id = settings.BEDROCK_EMBEDDING_MODEL_ID
        self.multimodal_embedding_model_id = getattr(settings, 'BEDROCK_MULTIMODAL_EMBEDDING_MODEL_ID', None)
        
        logger.debug(f"Using LLM model ID: {self.llm_model_id}")
        logger.debug(f"Using Embedding model ID: {self.embedding_model_id}")
        logger.debug(f"Using Multimodal Embedding model ID: {self.multimodal_embedding_model_id}")

        self.llm = ChatBedrock(
            model_id=self.llm_model_id,
            client=self.bedrock_client,
            temperature=0,
            provider='anthropic',
            max_tokens=8192
        )
        self._prompt_timeout_seconds = int(
            getattr(settings, "DOC_GEN_TIMEOUT", None)
            or settings.LLM_TIMEOUT
            or 300
        )
        
        # Determine embedding dimension based on model
        self._embedding_dim = self._get_embedding_dimension()
        
        # Image size limits for Bedrock Titan Embed Image v1
        self.max_image_size = (4096, 4096)  # Max 20M pixels (~4096x4096)
        self.max_pixels = 20_000_000
        
        logger.info(f"Bedrock initialized with LLM: {self.llm_model_id}, Embedding dimension: {self._embedding_dim}")
    
    def _get_embedding_dimension(self) -> int:
        """
        Get the embedding dimension for the configured embedding model.
        
        Returns:
            Embedding dimension (default: 1024 for titan-embed-text-v2, 1536 for v1).
        """
        model_id = self.embedding_model_id.lower()
        
        # Titan embedding model dimensions
        if "titan-embed-text-v2" in model_id:
            return 1024
        elif "titan-embed-text-v1" in model_id:
            return 1536
        elif "titan-embed-image" in model_id:
            return 1024  # Image embeddings are typically 1024
        elif "titan-multimodal" in model_id:
            return 1024  # Multimodal embeddings are typically 1024
        else:
            # Default to 1024 for unknown models, but log a warning
            logger.warning(f"Unknown embedding model: {model_id}. Defaulting to dimension 1024.")
            return 1024
    
    @property
    def embedding_dimension(self) -> int:
        """Get the embedding dimension for this embedder."""
        return self._embedding_dim

    @property
    def doc_gen_timeout(self) -> int:
        return self._prompt_timeout_seconds

    @staticmethod
    def build_text_prompt_body(prompt: str) -> Dict[str, Any]:
        """Anthropic text-only request body for JSON-style agent prompts."""
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": 0.3,
            "top_p": 0.9,
            "messages": [{"role": "user", "content": prompt}],
        }

    @staticmethod
    def image_content_blocks(page_images_b64: List[str]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for img in page_images_b64:
            if not img:
                continue
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img,
                },
            })
        return blocks

    @staticmethod
    def extract_text_field(llm_output: Dict[str, Any], *keys: str) -> Optional[str]:
        if not isinstance(llm_output, dict):
            return None
        for key in keys:
            value = llm_output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = llm_output.get("result")
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def strip_html_fences(html: str) -> str:
        text = (html or "").strip()
        if text.startswith("```html"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    @classmethod
    def recover_html_from_llm_output(cls, llm_output: Dict[str, Any]) -> Optional[str]:
        for key in ("html", "html_content", "output"):
            value = None
            if isinstance(llm_output, dict):
                value = llm_output.get(key)
                if value is None and isinstance(llm_output.get("result"), dict):
                    value = llm_output["result"].get(key)
            if isinstance(value, str) and value.strip():
                text = cls.strip_html_fences(value)
                lower = text.lower()
                for marker in ("<!doctype html", "<html", "<section", "<div", "<body"):
                    idx = lower.find(marker)
                    if idx != -1:
                        return text[idx:]
                if "<" in text and ">" in text:
                    return text
        return None

    def _invoke_model_sync(self, body: Dict[str, Any]):
        return self.bedrock_client.invoke_model(
            modelId=self.llm_model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

    def _invoke_model_stream_sync(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Stream Bedrock response so long generations don't idle-timeout."""
        response = self.bedrock_client.invoke_model_with_response_stream(
            modelId=self.llm_model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        chunks: List[str] = []
        input_tokens = 0
        output_tokens = 0
        stop_reason = None
        stream = response.get("body")
        for event in stream:
            chunk = event.get("chunk") or {}
            raw = chunk.get("bytes")
            if not raw:
                continue
            payload = json.loads(raw)
            event_type = payload.get("type")
            if event_type == "content_block_delta":
                delta = payload.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = delta.get("text") or ""
                    if text:
                        chunks.append(text)
            elif event_type == "message_start":
                usage = (payload.get("message") or {}).get("usage") or {}
                input_tokens = usage.get("input_tokens", 0) or 0
            elif event_type == "message_delta":
                usage = payload.get("usage") or {}
                output_tokens = usage.get("output_tokens", 0) or output_tokens
                stop_reason = (payload.get("delta") or {}).get("stop_reason") or stop_reason
        return {
            "text": "".join(chunks),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
        }

    async def invoke_prompt_body_stream(self, prompt_body: Dict[str, Any]) -> Dict[str, Any]:
        """Streaming invoke for long document-generation VLM calls."""
        logger.info("Invoking Bedrock with provider-native prompt body (streaming).")
        loop = asyncio.get_event_loop()
        body = _sanitize_bedrock_prompt_body(prompt_body, self.llm_model_id)
        body.pop("response_format", None)
        try:
            streamed = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self._invoke_model_stream_sync(body)),
                timeout=self._prompt_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Bedrock streaming call timed out after %s seconds",
                self._prompt_timeout_seconds,
            )
            raise RuntimeError("LLM API call timed out")
        except Exception as e:
            logger.error("Bedrock streaming call failed: %s", str(e), exc_info=True)
            raise RuntimeError(f"LLM API call failed: {str(e)}") from e

        output_text = streamed["text"]
        input_tokens = streamed["input_tokens"]
        output_tokens = streamed["output_tokens"]
        if streamed.get("stop_reason") == "max_tokens":
            logger.warning(
                "Bedrock stream hit max_tokens; output may be truncated (%d chars)",
                len(output_text),
            )
        if input_tokens or output_tokens:
            logger.info(
                "llm_tokens",
                extra={"input_tokens": input_tokens, "output_tokens": output_tokens},
            )

        stripped = (output_text or "").lstrip().lower()
        if (
            stripped.startswith("<!doctype html")
            or stripped.startswith("<html")
            or stripped.startswith("<section")
        ):
            result: Dict[str, Any] = {"html": output_text}
        else:
            parsed = _parse_json_response(output_text)
            if parsed:
                result = parsed
            else:
                logger.warning("Streamed response was not valid JSON; returning raw text")
                result = {"output": output_text, "error": "Failed to parse JSON response"}

        return {
            "result": result,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }

    async def invoke_prompt_stream(self, prompt_body: Dict[str, Any]) -> Dict[str, Any]:
        response = await self.invoke_prompt_body_stream(prompt_body)
        usage = response.get("usage") or {}
        _llm_tokens_total[0] += usage.get("input_tokens", 0)
        _llm_tokens_total[0] += usage.get("output_tokens", 0)
        return response.get("result") or {}

    async def _invoke_prompt_body_internal(self, prompt_body: Dict[str, Any]) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        body = _sanitize_bedrock_prompt_body(prompt_body, self.llm_model_id)
        is_structured_output = "response_format" in body

        try:
            try:
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: self._invoke_model_sync(body)),
                    timeout=self._prompt_timeout_seconds,
                )
            except Exception as api_error:
                error_str = str(api_error)
                if "response_format" in error_str and "Extra inputs are not permitted" in error_str:
                    logger.warning(
                        "API version doesn't support structured output, falling back to prompt-based instructions"
                    )
                    body = {k: v for k, v in body.items() if k != "response_format"}
                    body = _sanitize_bedrock_prompt_body(body, self.llm_model_id)
                    response = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda b=body: self._invoke_model_sync(b)),
                        timeout=self._prompt_timeout_seconds,
                    )
                    is_structured_output = False
                elif "temperature" in error_str and "top_p" in error_str:
                    logger.warning(
                        "Model rejects temperature+top_p together; retrying with temperature only"
                    )
                    body = _sanitize_bedrock_prompt_body(body, self.llm_model_id)
                    body.pop("top_p", None)
                    response = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda b=body: self._invoke_model_sync(b)),
                        timeout=self._prompt_timeout_seconds,
                    )
                else:
                    raise

            response_body = json.loads(response["body"].read())
            usage = response_body.get("usage") or {}
            input_tokens = usage.get("inputTokens", 0) or usage.get("input_tokens", 0)
            output_tokens = usage.get("outputTokens", 0) or usage.get("output_tokens", 0)
            if input_tokens or output_tokens:
                logger.info(
                    "llm_tokens",
                    extra={"input_tokens": input_tokens, "output_tokens": output_tokens},
                )

            content = response_body.get("content", [])
            result: Dict[str, Any] = {}

            if content and len(content) > 0:
                content_item = content[0]
                if is_structured_output:
                    if content_item.get("type") == "tool_use":
                        input_data = content_item.get("input", {})
                        if input_data:
                            logger.info("Successfully received structured output from Bedrock")
                            result = input_data
                    else:
                        output_text = content_item.get("text", "")
                        if output_text:
                            try:
                                result = json.loads(output_text)
                                logger.info("Successfully parsed structured output JSON from Bedrock")
                            except json.JSONDecodeError as e:
                                logger.warning("Structured output returned invalid JSON: %s", str(e))
                                parsed = _parse_json_response(output_text)
                                if parsed:
                                    result = parsed
                else:
                    output_text = content[0].get("text", "")
                    parsed = _parse_json_response(output_text)
                    if parsed:
                        logger.info("Successfully parsed JSON response from LLM")
                        result = parsed
                    else:
                        logger.warning("Failed to parse JSON from LLM response, returning raw text")
                        result = {"output": output_text, "error": "Failed to parse JSON response"}

            if not result and content:
                logger.warning("Empty parsed result from Bedrock API")

            return {
                "result": result,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            }

        except asyncio.TimeoutError:
            logger.error("Bedrock call timed out after %s seconds", self._prompt_timeout_seconds)
            raise RuntimeError("LLM API call timed out")
        except Exception as e:
            logger.error("Bedrock API call failed: %s", str(e), exc_info=True)
            raise RuntimeError(f"LLM API call failed: {str(e)}") from e

    async def invoke_prompt(self, prompt_body: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke with a provider-native body; returns parsed result dict only."""
        response = await self.invoke_prompt_body(prompt_body)
        usage = response.get("usage") or {}
        _llm_tokens_total[0] += usage.get("input_tokens", 0)
        _llm_tokens_total[0] += usage.get("output_tokens", 0)
        return response.get("result") or {}

    async def invoke_prompt_with_timeout(
        self,
        prompt_body: Dict[str, Any],
        timeout: Optional[int] = None,
        *,
        stream: bool = False,
    ) -> Dict[str, Any]:
        wait = timeout if timeout is not None else settings.LLM_TIMEOUT
        call = (
            self.invoke_prompt_stream(prompt_body)
            if stream
            else self.invoke_prompt(prompt_body)
        )
        try:
            return await asyncio.wait_for(call, timeout=wait)
        except asyncio.TimeoutError:
            logger.error("LLM call timed out after %ss", wait)
            raise RuntimeError(f"LLM call timed out after {wait}s")

    async def invoke_structured_prompt(
        self,
        prompt: str,
        schema: Type[T],
        timeout: Optional[int] = None,
    ) -> T:
        """Text prompt + Pydantic schema via LangChain structured output."""
        wait = timeout if timeout is not None else settings.LLM_TIMEOUT
        messages = [HumanMessage(content=prompt)]

        async def _call() -> T:
            return await self.invoke_structured_with_schema(messages, schema)

        try:
            return await asyncio.wait_for(_call(), timeout=wait)
        except asyncio.TimeoutError:
            logger.error("Structured LLM call timed out after %ss", wait)
            raise RuntimeError(f"LLM call timed out after {wait}s")

    async def invoke(self, messages):
        """
        Invoke the LLM with a list of messages.
        
        Args:
            messages: List of message dicts with 'role' and 'content' keys,
                     or LangChain message objects.
        
        Returns:
            The AI response content string.
        """
        logger.info(f"Invoking LLM with {len(messages)} messages...")
        logger.debug(f"Invoke messages: {messages}")
        try:
            response = await self.llm.ainvoke(messages)
            logger.info(f"LLM invocation successful.")
            logger.debug(f"LLM response: {getattr(response, 'content', repr(response))}")
            return response.content
        except Exception as e:
            logger.error(f"LLM invocation failed: {e}")
            raise

    async def invoke_prompt_body(self, prompt_body: dict) -> dict:
        """
        Invoke Bedrock with a provider-native request body.

        This is the shared entry point used by agents that need Anthropic
        document/image content blocks and per-call generation parameters.

        Returns:
            A dictionary containing parsed ``result`` and token ``usage``.
        """
        logger.info("Invoking Bedrock with provider-native prompt body.")
        return await self._invoke_prompt_body_internal(prompt_body)

    async def invoke_structured_with_schema(self, messages, schema):
        """
        Invoke the LLM expecting structured output.
        
        Args:
            messages: List of messages.
            schema: Pydantic model defining the expected output structure.
        
        Returns:
            Parsed Pydantic object.
        """
        logger.info(
            f"Invoking LLM for structured output with {len(messages)} messages and schema: {getattr(schema, '__name__', str(schema))}"
        )
        logger.debug(f"Structured invoke messages: {messages}")
        try:
            model_with_structure = self.llm.with_structured_output(schema)
            response = await model_with_structure.ainvoke(messages)
            logger.info("Structured invocation successful.")
            logger.debug(f"Structured LLM response: {repr(response)}")
            return response
        except Exception as e:
            logger.error(f"Structured invocation failed: {e}")
            raise

    async def embed(self, text: str) -> list:
        """
        Generate embedding for a text string using Bedrock Titan.
        
        Args:
            text: The text to embed.
        
        Returns:
            Embedding vector (list of floats, dimension 1536).
        """
        logger.info(f"Generating embedding for text of length {len(text)}...")
        logger.debug(f"Text to embed: {text[:180]}{'...' if len(text) > 200 else ''}")
        try:
            response = self.bedrock_client.invoke_model(
                body=json.dumps({"inputText": text}),
                modelId=self.embedding_model_id,
                contentType="application/json",
                accept="application/json"
            )

            logger.debug(f"Raw embedding response: {response}")
            
            response_body = json.loads(response.get('body').read())
            embedding = response_body.get('embedding')
            
            if not embedding:
                logger.error("No embedding returned from Bedrock.")
                raise ValueError("No embedding returned from Bedrock")
            
            logger.info(f"Embedding generated successfully. Vector size: {len(embedding)}")
            logger.debug(f"Embedding vector preview: {embedding[:10]}{'...' if len(embedding) > 10 else ''}")
            
            return embedding
            
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    async def embed_batch(self, texts: list) -> list:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of text strings to embed.
        
        Returns:
            List of embedding vectors.
        """
        logger.info(f"Generating embeddings for batch of {len(texts)} texts...")
        embeddings = []
        for idx, text in enumerate(texts):
            logger.debug(f"Embedding batch item {idx+1}/{len(texts)}: text length={len(text)}")
            embedding = await self.embed(text)
            embeddings.append(embedding)
        logger.info("Batch embedding generation completed.")
        logger.debug(f"Embeddings shape: {len(embeddings)} x {len(embeddings[0]) if embeddings else 0}")
        return embeddings

    def embed_sync(self, text: str) -> list:
        """
        Synchronous version of embed for non-async contexts.
        
        Args:
            text: The text to embed.
        
        Returns:
            Embedding vector.
        """
        logger.info(f"Synchronously generating embedding for text of length {len(text)}...")
        logger.debug(f"Sync text to embed: {text[:180]}{'...' if len(text) > 200 else ''}")
        try:
            response = self.bedrock_client.invoke_model(
                body=json.dumps({"inputText": text}),
                modelId=self.embedding_model_id,
                contentType="application/json",
                accept="application/json"
            )
            logger.debug(f"Raw sync embedding response: {response}")

            response_body = json.loads(response.get('body').read())
            embedding = response_body.get('embedding')
            
            if not embedding:
                logger.error("No embedding returned from Bedrock (sync call).")
                raise ValueError("No embedding returned from Bedrock")
            
            logger.info(f"Sync embedding generated successfully. Vector size: {len(embedding)}")
            logger.debug(f"Sync embedding vector preview: {embedding[:10]}{'...' if len(embedding) > 10 else ''}")
            
            return embedding
            
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    async def embed_multimodal(
        self,
        text: Optional[str] = None,
        image_base64: Optional[str] = None,
        image_bytes: Optional[bytes] = None
    ) -> list:
        """
        Generate multimodal embedding for text and/or image.
        
        This method supports:
        - Text-only embeddings (fallback to text embedder)
        - Image-only embeddings (requires multimodal model)
        - Combined text + image embeddings (requires multimodal model)
        
        Args:
            text: Optional text string to embed.
            image_base64: Optional base64-encoded image string.
            image_bytes: Optional raw image bytes (will be converted to base64).
            
        Returns:
            Embedding vector.
        """
        # Normalize empty strings to None
        if image_base64 and not image_base64.strip():
            image_base64 = None
        if image_bytes and len(image_bytes) == 0:
            image_bytes = None
        
        # If only text provided, use text embedder
        if text and not image_base64 and not image_bytes:
            logger.debug("Multimodal embed called with text only, using text embedder")
            return await self.embed(text)
        
        # If only image provided, need multimodal model
        if (image_base64 or image_bytes) and not text:
            if not self.multimodal_embedding_model_id:
                raise ValueError(
                    "Multimodal embedding model not configured. "
                    "Set BEDROCK_MULTIMODAL_EMBEDDING_MODEL_ID in settings."
                )
            
            # Convert image_bytes to base64 if needed
            if image_bytes and not image_base64:
                image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            
            logger.info(f"Generating image embedding using multimodal model: {self.multimodal_embedding_model_id}")
            logger.debug(f"Image base64 length: {len(image_base64) if image_base64 else 0}")
            
            # Prepare request body for multimodal embedding
            # Format depends on the specific model API
            body = {
                "inputImage": image_base64
            }
            
            try:
                response = self.bedrock_client.invoke_model(
                    body=json.dumps(body),
                    modelId=self.multimodal_embedding_model_id,
                    contentType="application/json",
                    accept="application/json"
                )
                
                response_body = json.loads(response.get('body').read())
                embedding = response_body.get('embedding')
                
                if not embedding:
                    logger.error("No embedding returned from multimodal model.")
                    raise ValueError("No embedding returned from multimodal model")
                
                logger.info(f"Multimodal embedding generated successfully. Vector size: {len(embedding)}")
                return embedding
                
            except Exception as e:
                error_str = str(e)
                # Check if error is due to image size
                if "exceeds max pixels" in error_str.lower() or "image exceeds" in error_str.lower():
                    logger.warning(f"Image exceeds Bedrock limits, attempting to resize: {e}")
                    try:
                        # Resize and retry
                        resized_base64 = self._resize_image_for_bedrock(image_base64)
                        body = {
                            "inputImage": resized_base64
                        }
                        response = self.bedrock_client.invoke_model(
                            body=json.dumps(body),
                            modelId=self.multimodal_embedding_model_id,
                            contentType="application/json",
                            accept="application/json"
                        )
                        response_body = json.loads(response.get('body').read())
                        embedding = response_body.get('embedding')
                        
                        if not embedding:
                            logger.error("No embedding returned from multimodal model after resize.")
                            raise ValueError("No embedding returned from multimodal model")
                        
                        logger.info(f"Multimodal embedding generated successfully after resize. Vector size: {len(embedding)}")
                        return embedding
                    except Exception as retry_error:
                        logger.error(f"Multimodal embedding generation failed even after resize: {retry_error}", exc_info=True)
                        raise
                else:
                    logger.error(f"Multimodal embedding generation failed: {e}", exc_info=True)
                    raise
        
        # Combined text + image
        if text and (image_base64 or image_bytes):
            if not self.multimodal_embedding_model_id:
                raise ValueError(
                    "Multimodal embedding model not configured for combined text+image. "
                    "Set BEDROCK_MULTIMODAL_EMBEDDING_MODEL_ID in settings."
                )
            
            if image_bytes and not image_base64:
                image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            
            logger.info(f"Generating combined text+image embedding using multimodal model")
            logger.debug(f"Text length: {len(text)}, Image base64 length: {len(image_base64)}")
            
            body = {
                "inputText": text,
                "inputImage": image_base64
            }
            
            try:
                response = self.bedrock_client.invoke_model(
                    body=json.dumps(body),
                    modelId=self.multimodal_embedding_model_id,
                    contentType="application/json",
                    accept="application/json"
                )
                
                response_body = json.loads(response.get('body').read())
                embedding = response_body.get('embedding')
                
                if not embedding:
                    logger.error("No embedding returned from multimodal model.")
                    raise ValueError("No embedding returned from multimodal model")
                
                logger.info(f"Combined multimodal embedding generated. Vector size: {len(embedding)}")
                return embedding
                
            except Exception as e:
                error_str = str(e)
                # Check if error is due to image size
                if "exceeds max pixels" in error_str.lower() or "image exceeds" in error_str.lower():
                    logger.warning(f"Image exceeds Bedrock limits in combined query, attempting to resize: {e}")
                    try:
                        # Resize and retry
                        resized_base64 = self._resize_image_for_bedrock(image_base64)
                        body = {
                            "inputText": text,
                            "inputImage": resized_base64
                        }
                        response = self.bedrock_client.invoke_model(
                            body=json.dumps(body),
                            modelId=self.multimodal_embedding_model_id,
                            contentType="application/json",
                            accept="application/json"
                        )
                        response_body = json.loads(response.get('body').read())
                        embedding = response_body.get('embedding')
                        
                        if not embedding:
                            logger.error("No embedding returned from multimodal model after resize.")
                            raise ValueError("No embedding returned from multimodal model")
                        
                        logger.info(f"Combined multimodal embedding generated successfully after resize. Vector size: {len(embedding)}")
                        return embedding
                    except Exception as retry_error:
                        logger.error(f"Combined multimodal embedding generation failed even after resize: {retry_error}", exc_info=True)
                        raise
                else:
                    logger.error(f"Combined multimodal embedding generation failed: {e}", exc_info=True)
                    raise
        
        raise ValueError("Must provide at least text or image for embedding")
