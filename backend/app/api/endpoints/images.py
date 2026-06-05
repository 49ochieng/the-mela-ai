"""
Mela AI - Image Generation Endpoints
Supports FLUX.1-Kontext-pro (primary) and DALL-E 3 (fallback).
Provider selection is handled inside dalle_service (ImageGenerationService).
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import List
from enum import Enum

from app.core.security import get_current_user
from app.schemas.auth import UserInfo
from app.services.dalle_service import (
    dalle_service,
    ImageSize,
    ImageQuality,
    ImageStyle,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ImageSizeEnum(str, Enum):
    """Image size options."""
    SQUARE = "1024x1024"
    LANDSCAPE = "1792x1024"
    PORTRAIT = "1024x1792"


class ImageQualityEnum(str, Enum):
    """Image quality options."""
    STANDARD = "standard"
    HD = "hd"


class ImageStyleEnum(str, Enum):
    """Image style options."""
    VIVID = "vivid"
    NATURAL = "natural"


class GenerateImageRequest(BaseModel):
    """Request model for image generation."""
    prompt: str = Field(
        ...,
        description="Text description of the image to generate",
        max_length=4000,
    )
    size: ImageSizeEnum = Field(
        default=ImageSizeEnum.SQUARE,
        description="Image size",
    )
    quality: ImageQualityEnum = Field(
        default=ImageQualityEnum.STANDARD,
        description="Image quality (HD costs more tokens)",
    )
    style: ImageStyleEnum = Field(
        default=ImageStyleEnum.VIVID,
        description="Image style - vivid produces more dramatic images",
    )


class GenerateBatchRequest(BaseModel):
    """Request model for batch image generation."""
    prompts: List[str] = Field(
        ...,
        description="List of prompts to generate images for",
        max_items=5,
    )
    size: ImageSizeEnum = Field(default=ImageSizeEnum.SQUARE)
    quality: ImageQualityEnum = Field(default=ImageQualityEnum.STANDARD)
    style: ImageStyleEnum = Field(default=ImageStyleEnum.VIVID)


@router.post("/generate")
async def generate_image(
    request: GenerateImageRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Generate an image from a text prompt using DALL-E 3.

    Returns the URL of the generated image and the revised prompt.
    DALL-E 3 may modify your prompt for better results.
    """
    try:
        if dalle_service is None or not dalle_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Image generation service is not configured",
            )

        result = await dalle_service.generate_image(
            prompt=request.prompt,
            size=ImageSize(request.size.value),
            quality=ImageQuality(request.quality.value),
            style=ImageStyle(request.style.value),
        )

        return result.to_dict()

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        err_str = str(e).lower()
        logger.error(f"Image generation failed: {e}")
        if "410" in err_str or "deprecated" in err_str or "no longer available" in err_str:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Image generation is temporarily unavailable — the model deployment "
                    "has been deprecated. Please contact your administrator to update the "
                    "FLUX or DALL-E deployment configuration."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Image generation failed: {str(e)}",
        )


@router.post("/generate/batch")
async def generate_images_batch(
    request: GenerateBatchRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Generate multiple images from multiple prompts.

    Limited to 5 prompts per request to prevent abuse.
    """
    try:
        if dalle_service is None or not dalle_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Image generation service is not configured",
            )

        results = await dalle_service.generate_images_batch(
            prompts=request.prompts,
            size=ImageSize(request.size.value),
            quality=ImageQuality(request.quality.value),
            style=ImageStyle(request.style.value),
        )

        return {"images": [r.to_dict() for r in results]}

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        err_str = str(e).lower()
        logger.error(f"Batch image generation failed: {e}")
        if "410" in err_str or "deprecated" in err_str or "no longer available" in err_str:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Image generation is temporarily unavailable — the model deployment "
                    "has been deprecated. Please contact your administrator."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch image generation failed: {str(e)}",
        )


@router.get("/download")
async def download_image(
    url: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Download a generated image from its URL.

    Returns the image as a binary response.
    """
    try:
        if not dalle_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Image generation service is not configured",
            )

        image_bytes = await dalle_service.download_image(url)

        return Response(
            content=image_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": 'attachment; filename="generated_image.png"',
            },
        )

    except Exception as e:
        logger.error(f"Image download failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Image download failed: {str(e)}",
        )


@router.get("/status")
async def get_service_status(
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Check if the image generation service is available.
    """
    available = dalle_service is not None and dalle_service.is_configured
    # Report which provider is active (first configured in priority order)
    active_provider = "none"
    active_model = "none"
    if available:
        for p in dalle_service._provider_order:
            if dalle_service._provider_configured(p):
                active_provider = p
                active_model = (
                    dalle_service._flux_deployment if p == "flux"
                    else dalle_service._dalle_deployment
                )
                break
    return {
        "available": available,
        "active_provider": active_provider,
        "model": active_model,
        "supported_sizes": [s.value for s in ImageSizeEnum],
        "supported_qualities": [q.value for q in ImageQualityEnum],
        "supported_styles": [s.value for s in ImageStyleEnum],
    }
