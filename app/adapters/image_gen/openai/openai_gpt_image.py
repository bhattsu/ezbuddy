"""OpenAI GPT Image client."""

import base64
import logging

from openai import OpenAI

from app.adapters.common import run_sync_in_executor, wrap_provider_error
from app.adapters.image_gen.base import ImageGenerationProvider
from app.config.settings import Settings

logger = logging.getLogger(__name__)


class OpenAIGPTImageClient(ImageGenerationProvider):
    """Text-to-image using OpenAI GPT Image models."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = OpenAI(api_key=settings.openai_api_key)

    def _generate(self, text: str) -> bytes:
        response = self._client.images.generate(
            model=self._settings.openai_image_model,
            prompt=text,
            size="1024x1024",
            n=1,
        )
        image_data = response.data[0]
        if image_data.b64_json:
            return base64.b64decode(image_data.b64_json)
        if image_data.url:
            import httpx

            with httpx.Client(timeout=120.0) as http:
                download = http.get(image_data.url)
                download.raise_for_status()
                return download.content
        raise ValueError("OpenAI image response contained no data")

    async def generate(self, text: str) -> bytes:
        try:
            return await run_sync_in_executor(self._generate, text)
        except Exception as exc:
            raise wrap_provider_error("openai", exc) from exc
