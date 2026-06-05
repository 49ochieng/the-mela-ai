"""
Mela AI - Translation Endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional

from app.core.security import get_current_user
from app.schemas.auth import UserInfo
from app.services.translator_service import translator_service

logger = logging.getLogger(__name__)
router = APIRouter()


class TranslateRequest(BaseModel):
    """Request model for text translation."""
    text: str = Field(..., description="Text to translate")
    target_language: str = Field(..., description="Target language code (e.g., 'es', 'fr', 'de')")
    source_language: Optional[str] = Field(None, description="Source language code (auto-detected if not provided)")


class TranslateBatchRequest(BaseModel):
    """Request model for batch text translation."""
    texts: List[str] = Field(..., description="List of texts to translate")
    target_language: str = Field(..., description="Target language code")
    source_language: Optional[str] = Field(None, description="Source language code")


class DetectLanguageRequest(BaseModel):
    """Request model for language detection."""
    text: str = Field(..., description="Text to analyze")


class TransliterateRequest(BaseModel):
    """Request model for transliteration."""
    text: str = Field(..., description="Text to transliterate")
    language: str = Field(..., description="Language code")
    from_script: str = Field(..., description="Source script (e.g., 'Latn')")
    to_script: str = Field(..., description="Target script (e.g., 'Arab')")


@router.post("/translate")
async def translate_text(
    request: TranslateRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Translate text to target language.

    Supports automatic language detection if source_language is not provided.
    """
    try:
        if not translator_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Translation service is not configured",
            )

        result = await translator_service.translate(
            text=request.text,
            target_language=request.target_language,
            source_language=request.source_language,
        )

        return result.to_dict()

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Translation failed: {str(e)}",
        )


@router.post("/translate/batch")
async def translate_batch(
    request: TranslateBatchRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Translate multiple texts to target language.

    More efficient than calling translate multiple times.
    """
    try:
        if not translator_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Translation service is not configured",
            )

        results = await translator_service.translate_batch(
            texts=request.texts,
            target_language=request.target_language,
            source_language=request.source_language,
        )

        return {"translations": [r.to_dict() for r in results]}

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Batch translation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch translation failed: {str(e)}",
        )


@router.post("/detect")
async def detect_language(
    request: DetectLanguageRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Detect the language of text.

    Returns language code, confidence score, and translation support status.
    """
    try:
        if not translator_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Translation service is not configured",
            )

        result = await translator_service.detect_language(request.text)

        return result.to_dict()

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Language detection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Language detection failed: {str(e)}",
        )


@router.get("/languages")
async def get_supported_languages(
    scope: str = "translation",
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Get list of supported languages.

    Args:
        scope: Scope of languages ('translation', 'transliteration', 'dictionary')
    """
    try:
        # Return cached common languages if service is not configured
        if not translator_service.is_configured:
            return {"languages": translator_service.SUPPORTED_LANGUAGES}

        result = await translator_service.get_supported_languages(scope)
        return result

    except Exception as e:
        logger.error(f"Failed to get languages: {e}")
        # Fall back to cached languages
        return {"languages": translator_service.SUPPORTED_LANGUAGES}


@router.post("/transliterate")
async def transliterate_text(
    request: TransliterateRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Transliterate text from one script to another.

    Useful for converting between writing systems (e.g., Latin to Arabic).
    """
    try:
        if not translator_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Translation service is not configured",
            )

        result = await translator_service.transliterate(
            text=request.text,
            language=request.language,
            from_script=request.from_script,
            to_script=request.to_script,
        )

        return {"text": result}

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Transliteration failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transliteration failed: {str(e)}",
        )
