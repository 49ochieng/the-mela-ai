"""
Mela AI - Image Generation Service
Primary provider: FLUX.1-Kontext-pro (Azure AI)
Fallback provider: DALL-E 3 (Azure OpenAI)

Public interface is unchanged — callers use dalle_service.generate_image()
regardless of which provider actually handles the request.
"""

import logging
import base64
from typing import Dict, Any, Optional, List
from enum import Enum

import httpx
from openai import AsyncAzureOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─── Canonical enums (kept for callers that import them) ─────────────────────

class ImageSize(str, Enum):
    SQUARE    = "1024x1024"
    LANDSCAPE = "1792x1024"
    PORTRAIT  = "1024x1792"


class ImageQuality(str, Enum):
    STANDARD = "standard"
    HD       = "hd"


class ImageStyle(str, Enum):
    VIVID   = "vivid"
    NATURAL = "natural"


# ─── Result dataclass ─────────────────────────────────────────────────────────

class ImageGenerationResult:
    def __init__(
        self,
        url: str,
        revised_prompt: str,
        original_prompt: str,
        size: str,
        quality: str,
        style: str,
        b64_json: Optional[str] = None,
        provider: str = "unknown",
    ):
        self.url            = url
        self.revised_prompt = revised_prompt
        self.original_prompt = original_prompt
        self.size           = size
        self.quality        = quality
        self.style          = style
        self.b64_json       = b64_json
        self.provider       = provider

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "url":             self.url,
            "revised_prompt":  self.revised_prompt,
            "original_prompt": self.original_prompt,
            "size":            self.size,
            "quality":         self.quality,
            "style":           self.style,
            "provider":        self.provider,
        }
        if self.b64_json:
            d["b64_json"] = self.b64_json
        return d


# ─── ImageGenerationService ───────────────────────────────────────────────────

class ImageGenerationService:
    """
    Routes image generation to FLUX (primary) then DALL-E (fallback).

    Provider selection is determined by IMAGE_PROVIDER_ORDER env var.
    Only configured providers are tried; misconfigured ones are skipped.
    """

    def __init__(self):
        self._flux_endpoint   = (settings.FLUX_ENDPOINT or "").rstrip("/")
        self._flux_api_key    = settings.FLUX_API_KEY or ""
        self._flux_deployment = settings.FLUX_DEPLOYMENT or "FLUX.1-Kontext-pro"
        self._flux_api_version = settings.FLUX_API_VERSION or "2024-05-01-preview"

        self._dalle_endpoint   = (settings.AZURE_DALLE_ENDPOINT or "").rstrip("/")
        self._dalle_api_key    = settings.AZURE_DALLE_API_KEY or ""
        self._dalle_api_version = settings.AZURE_DALLE_API_VERSION or "2024-02-01"
        self._dalle_deployment  = settings.AZURE_DALLE_DEPLOYMENT or "dall-e-3"

        self._dalle_client: Optional[AsyncAzureOpenAI] = None
        if self._dalle_configured:
            try:
                self._dalle_client = AsyncAzureOpenAI(
                    api_key=self._dalle_api_key,
                    api_version=self._dalle_api_version,
                    azure_endpoint=self._dalle_endpoint,
                )
            except Exception as e:
                logger.warning(f"DALL-E client init failed: {e}")

        # Build priority list from IMAGE_PROVIDER_ORDER
        order = [p.strip() for p in settings.IMAGE_PROVIDER_ORDER.split(",") if p.strip()]
        self._provider_order = order or ["flux", "dalle"]

        active = [p for p in self._provider_order if self._provider_configured(p)]
        logger.info(
            "ImageGenerationService ready | order=%s | active=%s",
            self._provider_order, active,
        )

    # ── config checks ────────────────────────────────────────────────────────

    @property
    def _flux_configured(self) -> bool:
        return bool(self._flux_endpoint and self._flux_api_key and self._flux_deployment)

    @property
    def _dalle_configured(self) -> bool:
        return bool(self._dalle_endpoint and self._dalle_api_key and self._dalle_deployment)

    def _provider_configured(self, name: str) -> bool:
        if name == "flux":
            return self._flux_configured
        if name == "dalle":
            return self._dalle_configured
        return False

    @property
    def is_configured(self) -> bool:
        """True if at least one image provider is ready."""
        return any(self._provider_configured(p) for p in self._provider_order)

    # ── FLUX generation ──────────────────────────────────────────────────────

    async def _generate_with_flux(
        self,
        prompt: str,
        size: str,
        response_format: str,
    ) -> ImageGenerationResult:
        """POST directly to FLUX Azure AI endpoint via httpx."""
        # Azure AI Foundry deployment path (same pattern as Azure OpenAI)
        url = (
            f"{self._flux_endpoint}/openai/deployments/{self._flux_deployment}"
            f"/images/generations?api-version={self._flux_api_version}"
        )
        # FLUX only supports b64_json — "url" format returns 422.
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }

        logger.info(
            "FLUX generate | deployment=%s size=%s prompt=%.80s",
            self._flux_deployment, size, prompt,
        )

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "api-key": self._flux_api_key,
                    "Content-Type": "application/json",
                },
            )

        if response.status_code == 404:
            # Deployment-path variant: try without /openai/deployments prefix
            alt_url = (
                f"{self._flux_endpoint}/images/generations"
                f"?api-version={self._flux_api_version}"
            )
            payload["model"] = self._flux_deployment
            logger.info("FLUX 404 on deployment path, retrying inference path: %s", alt_url)
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    alt_url,
                    json=payload,
                    headers={
                        "api-key": self._flux_api_key,
                        "Content-Type": "application/json",
                    },
                )

        if response.status_code != 200:
            raise RuntimeError(
                f"FLUX API error {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        img  = data["data"][0]
        logger.info("FLUX generate OK | provider=flux")
        return ImageGenerationResult(
            url=img.get("url", ""),
            revised_prompt=img.get("revised_prompt", prompt),
            original_prompt=prompt,
            size=size,
            quality="standard",
            style="default",
            b64_json=img.get("b64_json"),
            provider="flux",
        )

    # ── DALL-E generation ────────────────────────────────────────────────────

    async def _generate_with_dalle(
        self,
        prompt: str,
        size: str,
        quality: str,
        style: str,
        response_format: str,
    ) -> ImageGenerationResult:
        if not self._dalle_client:
            raise RuntimeError("DALL-E client not initialised")

        logger.info(
            "DALL-E generate | deployment=%s size=%s prompt=%.80s",
            self._dalle_deployment, size, prompt,
        )
        response = await self._dalle_client.images.generate(
            model=self._dalle_deployment,
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
            n=1,
            response_format=response_format,
        )
        img = response.data[0]
        logger.info("DALL-E generate OK | provider=dalle")
        return ImageGenerationResult(
            url=img.url or "",
            revised_prompt=img.revised_prompt or prompt,
            original_prompt=prompt,
            size=size,
            quality=quality,
            style=style,
            b64_json=img.b64_json,
            provider="dalle",
        )

    # ── Public API ───────────────────────────────────────────────────────────

    async def generate_image(
        self,
        prompt: str,
        size: ImageSize = ImageSize.SQUARE,
        quality: ImageQuality = ImageQuality.STANDARD,
        style: ImageStyle = ImageStyle.VIVID,
        n: int = 1,
        response_format: str = "url",
    ) -> ImageGenerationResult:
        """
        Generate an image. Tries providers in IMAGE_PROVIDER_ORDER order.
        FLUX is tried first if configured; DALL-E is the fallback.
        """
        if not self.is_configured:
            raise ValueError("No image generation provider is configured")

        size_val    = size.value    if isinstance(size, ImageSize)    else size
        quality_val = quality.value if isinstance(quality, ImageQuality) else quality
        style_val   = style.value   if isinstance(style, ImageStyle)   else style

        last_error: Optional[Exception] = None

        for provider in self._provider_order:
            if not self._provider_configured(provider):
                continue
            try:
                if provider == "flux":
                    return await self._generate_with_flux(prompt, size_val, response_format)
                if provider == "dalle":
                    return await self._generate_with_dalle(
                        prompt, size_val, quality_val, style_val, response_format
                    )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Image provider '%s' failed, trying next: %s", provider, e
                )
                continue

        raise RuntimeError(
            f"All image providers failed. Last error: {last_error}"
        )

    async def generate_images_batch(
        self,
        prompts: List[str],
        size: ImageSize = ImageSize.SQUARE,
        quality: ImageQuality = ImageQuality.STANDARD,
        style: ImageStyle = ImageStyle.VIVID,
    ) -> List[ImageGenerationResult]:
        results = []
        for prompt in prompts:
            try:
                result = await self.generate_image(
                    prompt=prompt, size=size, quality=quality, style=style,
                )
                results.append(result)
            except Exception as e:
                logger.error("Batch image failed for prompt '%.50s': %s", prompt, e)
        return results

    async def download_image(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    @staticmethod
    def base64_to_bytes(b64_string: str) -> bytes:
        return base64.b64decode(b64_string)

    @staticmethod
    def bytes_to_base64(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("utf-8")


# ─── Singleton ────────────────────────────────────────────────────────────────

try:
    dalle_service = ImageGenerationService()
except Exception as e:
    logger.warning(f"Failed to initialise ImageGenerationService: {e}")
    dalle_service = None  # type: ignore[assignment]
