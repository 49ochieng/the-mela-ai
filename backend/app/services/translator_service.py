"""
Mela AI - Azure Translator Service
Provides text and document translation capabilities using Azure Translator.
"""

import logging
from typing import List, Dict, Any, Optional
import httpx
import uuid

from app.core.config import settings

logger = logging.getLogger(__name__)


class TranslationResult:
    """Result from text translation."""
    def __init__(
        self,
        original_text: str,
        translated_text: str,
        source_language: str,
        target_language: str,
        confidence: float = 1.0,
    ):
        self.original_text = original_text
        self.translated_text = translated_text
        self.source_language = source_language
        self.target_language = target_language
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_text": self.original_text,
            "translated_text": self.translated_text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "confidence": self.confidence,
        }


class DetectionResult:
    """Result from language detection."""
    def __init__(self, language: str, confidence: float, is_translation_supported: bool):
        self.language = language
        self.confidence = confidence
        self.is_translation_supported = is_translation_supported

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "confidence": self.confidence,
            "is_translation_supported": self.is_translation_supported,
        }


class TranslatorService:
    """Service for Azure Translator operations."""

    # Common supported languages
    SUPPORTED_LANGUAGES = {
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "it": "Italian",
        "pt": "Portuguese",
        "ru": "Russian",
        "ja": "Japanese",
        "ko": "Korean",
        "zh-Hans": "Chinese (Simplified)",
        "zh-Hant": "Chinese (Traditional)",
        "ar": "Arabic",
        "hi": "Hindi",
        "nl": "Dutch",
        "pl": "Polish",
        "tr": "Turkish",
        "vi": "Vietnamese",
        "th": "Thai",
        "sv": "Swedish",
        "da": "Danish",
        "fi": "Finnish",
        "no": "Norwegian",
        "cs": "Czech",
        "el": "Greek",
        "he": "Hebrew",
        "hu": "Hungarian",
        "id": "Indonesian",
        "ms": "Malay",
        "ro": "Romanian",
        "sk": "Slovak",
        "uk": "Ukrainian",
    }

    def __init__(self):
        self.key = settings.AZURE_TRANSLATOR_KEY
        self.endpoint = settings.AZURE_TRANSLATOR_ENDPOINT
        self.document_endpoint = settings.AZURE_TRANSLATOR_DOCUMENT_ENDPOINT
        self.region = settings.AZURE_TRANSLATOR_REGION

        self.headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Ocp-Apim-Subscription-Region": self.region,
            "Content-Type": "application/json",
        }

    @property
    def is_configured(self) -> bool:
        """Check if the translator service is properly configured."""
        return bool(self.key and self.endpoint)

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
    ) -> TranslationResult:
        """
        Translate text to target language.

        Args:
            text: Text to translate
            target_language: Target language code (e.g., 'es', 'fr', 'de')
            source_language: Optional source language code (auto-detected if not provided)

        Returns:
            TranslationResult with translated text
        """
        if not self.is_configured:
            raise ValueError("Translator service is not configured")

        url = f"{self.endpoint}translate"
        params = {
            "api-version": "3.0",
            "to": target_language,
        }

        if source_language:
            params["from"] = source_language

        body = [{"text": text}]

        headers = {**self.headers, "X-ClientTraceId": str(uuid.uuid4())}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=30.0,
                )
                response.raise_for_status()

                result = response.json()

                if result and len(result) > 0:
                    translation = result[0]
                    detected_language = translation.get("detectedLanguage", {})
                    translations = translation.get("translations", [])

                    if translations:
                        return TranslationResult(
                            original_text=text,
                            translated_text=translations[0].get("text", ""),
                            source_language=source_language or detected_language.get("language", "unknown"),
                            target_language=target_language,
                            confidence=detected_language.get("score", 1.0),
                        )

                raise ValueError("No translation returned from service")

            except httpx.HTTPStatusError as e:
                logger.error(f"Translation HTTP error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Translation error: {e}")
                raise

    async def translate_batch(
        self,
        texts: List[str],
        target_language: str,
        source_language: Optional[str] = None,
    ) -> List[TranslationResult]:
        """
        Translate multiple texts to target language.

        Args:
            texts: List of texts to translate
            target_language: Target language code
            source_language: Optional source language code

        Returns:
            List of TranslationResult objects
        """
        if not self.is_configured:
            raise ValueError("Translator service is not configured")

        url = f"{self.endpoint}translate"
        params = {
            "api-version": "3.0",
            "to": target_language,
        }

        if source_language:
            params["from"] = source_language

        body = [{"text": t} for t in texts]

        headers = {**self.headers, "X-ClientTraceId": str(uuid.uuid4())}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=60.0,
                )
                response.raise_for_status()

                results = response.json()
                translation_results = []

                for i, result in enumerate(results):
                    detected_language = result.get("detectedLanguage", {})
                    translations = result.get("translations", [])

                    if translations:
                        translation_results.append(TranslationResult(
                            original_text=texts[i],
                            translated_text=translations[0].get("text", ""),
                            source_language=source_language or detected_language.get("language", "unknown"),
                            target_language=target_language,
                            confidence=detected_language.get("score", 1.0),
                        ))

                return translation_results

            except httpx.HTTPStatusError as e:
                logger.error(f"Batch translation HTTP error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Batch translation error: {e}")
                raise

    async def detect_language(self, text: str) -> DetectionResult:
        """
        Detect the language of text.

        Args:
            text: Text to analyze

        Returns:
            DetectionResult with detected language information
        """
        if not self.is_configured:
            raise ValueError("Translator service is not configured")

        url = f"{self.endpoint}detect"
        params = {"api-version": "3.0"}
        body = [{"text": text}]

        headers = {**self.headers, "X-ClientTraceId": str(uuid.uuid4())}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=30.0,
                )
                response.raise_for_status()

                result = response.json()

                if result and len(result) > 0:
                    detection = result[0]
                    return DetectionResult(
                        language=detection.get("language", "unknown"),
                        confidence=detection.get("score", 0.0),
                        is_translation_supported=detection.get("isTranslationSupported", False),
                    )

                raise ValueError("No detection result returned from service")

            except httpx.HTTPStatusError as e:
                logger.error(f"Language detection HTTP error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Language detection error: {e}")
                raise

    async def get_supported_languages(self, scope: str = "translation") -> Dict[str, Any]:
        """
        Get list of supported languages.

        Args:
            scope: Scope of languages to return ('translation', 'transliteration', 'dictionary')

        Returns:
            Dictionary of supported languages
        """
        url = f"{self.endpoint}languages"
        params = {
            "api-version": "3.0",
            "scope": scope,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, params=params, timeout=30.0)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                logger.error(f"Get languages HTTP error: {e.response.status_code}")
                raise
            except Exception as e:
                logger.error(f"Get languages error: {e}")
                raise

    async def transliterate(
        self,
        text: str,
        language: str,
        from_script: str,
        to_script: str,
    ) -> str:
        """
        Transliterate text from one script to another.

        Args:
            text: Text to transliterate
            language: Language code
            from_script: Source script (e.g., 'Latn')
            to_script: Target script (e.g., 'Arab')

        Returns:
            Transliterated text
        """
        if not self.is_configured:
            raise ValueError("Translator service is not configured")

        url = f"{self.endpoint}transliterate"
        params = {
            "api-version": "3.0",
            "language": language,
            "fromScript": from_script,
            "toScript": to_script,
        }
        body = [{"text": text}]

        headers = {**self.headers, "X-ClientTraceId": str(uuid.uuid4())}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=30.0,
                )
                response.raise_for_status()

                result = response.json()

                if result and len(result) > 0:
                    return result[0].get("text", "")

                return ""

            except httpx.HTTPStatusError as e:
                logger.error(f"Transliteration HTTP error: {e.response.status_code}")
                raise
            except Exception as e:
                logger.error(f"Transliteration error: {e}")
                raise


# Singleton instance - initialized lazily to avoid import failures
try:
    translator_service = TranslatorService()
except Exception as e:
    logger.warning(f"Failed to initialize TranslatorService: {e}")
    translator_service = None
