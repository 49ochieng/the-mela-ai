"""
Mela AI - Azure Speech Service
"""

import logging
import re
from collections import OrderedDict
from typing import AsyncGenerator
import azure.cognitiveservices.speech as speechsdk
import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings


def clean_text_for_tts(text: str) -> str:
    """Strip markdown and citation markup that would sound awkward when spoken.

    Rules (applied in order):
    - Inline citation refs like [1], [2], [1,2] → removed
    - Numbered footnote links like [1]: https://... → removed entire line
    - Markdown links [text](url) → keep only the text
    - SharePoint/source attribution lines → rewritten naturally
    - Markdown bold/italic (**text**, *text*, __text__, _text_) → plain text
    - Markdown headers (##, ###) → plain text
    - Markdown code fences (``` ... ```) → replaced with "code block"
    - Inline code backticks → removed
    - Horizontal rules (---, ***) → removed
    - Multiple blank lines → single newline
    """
    # Remove numbered footnote definition lines  e.g. "[1]: https://..."
    text = re.sub(r"^\s*\[\d+\]:\s*https?://\S+.*$", "", text, flags=re.MULTILINE)

    # Remove bare citation number references like [1], [2,3], [1][2]
    text = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", text)

    # Markdown links → just the link text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)

    # Markdown headers → plain text (strip # prefix)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Code fences
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)

    # Inline backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Horizontal rules
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Collapse extra blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level LRU cache for TTS synthesis results (keyed by (text, voice)).
# Avoids repeated Azure calls for identical phrases (greetings, short answers).
# ---------------------------------------------------------------------------
_TTS_CACHE_MAX = 30
_tts_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()


def _tts_cache_get(text: str, voice: str) -> bytes | None:
    key = (text, voice)
    if key in _tts_cache:
        _tts_cache.move_to_end(key)
        return _tts_cache[key]
    return None


def _tts_cache_put(text: str, voice: str, data: bytes) -> None:
    key = (text, voice)
    _tts_cache[key] = data
    _tts_cache.move_to_end(key)
    if len(_tts_cache) > _TTS_CACHE_MAX:
        _tts_cache.popitem(last=False)


class TranscriptionResult:
    """Result from speech transcription."""
    def __init__(self, text: str, confidence: float = 1.0, duration_ms: int = 0):
        self.text = text
        self.confidence = confidence
        self.duration_ms = duration_ms


class SpeechService:
    """Service for Azure Speech operations."""

    def __init__(self):
        if settings.AZURE_SPEECH_KEY:
            self.speech_config = speechsdk.SpeechConfig(
                subscription=settings.AZURE_SPEECH_KEY,
                region=settings.AZURE_SPEECH_REGION,
            )
            self.speech_config.speech_recognition_language = settings.AZURE_SPEECH_LANGUAGE
            self.speech_config.speech_synthesis_language = settings.AZURE_SPEECH_LANGUAGE
            self.speech_config.speech_synthesis_voice_name = "en-US-JennyNeural"
        else:
            self.speech_config = None

        # 8 workers so pre-fetch of next sentence chunk can overlap with playback
        self.executor = ThreadPoolExecutor(max_workers=8)

    async def transcribe(
        self,
        audio_data: bytes,
        content_type: str = "audio/wav",
        language: str = "en-US",
    ) -> TranscriptionResult:
        """Transcribe audio to text using Azure Speech Services.
        Handles WAV as well as compressed browser formats (WebM/Opus, OGG, MP3).
        """
        if not self.speech_config:
            return TranscriptionResult(text="", confidence=0, duration_ms=0)

        # Override language per-request
        self.speech_config.speech_recognition_language = language

        def _recognize():
            ct_lower = content_type.lower()
            # Choose compressed stream format for non-WAV browser audio
            if "webm" in ct_lower or "opus" in ct_lower:
                try:
                    fmt = speechsdk.audio.AudioStreamFormat.get_compressed_format_default_bitrate(
                        speechsdk.audio.AudioStreamContainerFormat.ANY
                    )
                    stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
                except Exception:
                    stream = speechsdk.audio.PushAudioInputStream()
            elif "ogg" in ct_lower:
                try:
                    fmt = speechsdk.audio.AudioStreamFormat.get_compressed_format_default_bitrate(
                        speechsdk.audio.AudioStreamContainerFormat.OGG_OPUS
                    )
                    stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
                except Exception:
                    stream = speechsdk.audio.PushAudioInputStream()
            elif "mp3" in ct_lower or "mpeg" in ct_lower:
                try:
                    fmt = speechsdk.audio.AudioStreamFormat.get_compressed_format_default_bitrate(
                        speechsdk.audio.AudioStreamContainerFormat.MP3
                    )
                    stream = speechsdk.audio.PushAudioInputStream(stream_format=fmt)
                except Exception:
                    stream = speechsdk.audio.PushAudioInputStream()
            else:
                # Default: raw PCM / WAV
                stream = speechsdk.audio.PushAudioInputStream()

            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config,
            )

            stream.write(audio_data)
            stream.close()

            result = recognizer.recognize_once()

            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                duration = int(result.duration / 10000) if result.duration else 0
                return TranscriptionResult(
                    text=result.text,
                    confidence=1.0,
                    duration_ms=duration,
                )
            elif result.reason == speechsdk.ResultReason.NoMatch:
                logger.warning("No speech could be recognized")
                return TranscriptionResult(text="", confidence=0, duration_ms=0)
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation = result.cancellation_details
                logger.error(f"Speech recognition canceled: {cancellation.reason}")
                if cancellation.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"Error details: {cancellation.error_details}")
                return TranscriptionResult(text="", confidence=0, duration_ms=0)
            return TranscriptionResult(text="", confidence=0, duration_ms=0)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, _recognize)


    async def speech_to_text(self, audio_data: bytes) -> str:
        """Convert speech to text (backward compatible)."""
        result = await self.transcribe(audio_data)
        return result.text

    def _get_synth_config(self, voice: str) -> speechsdk.SpeechConfig:
        """Return a SpeechConfig for the given voice, reusing the base config."""
        config = speechsdk.SpeechConfig(
            subscription=settings.AZURE_SPEECH_KEY,
            region=settings.AZURE_SPEECH_REGION,
        )
        # Use mp3 output for smaller payload and faster transfer
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
        )
        config.speech_synthesis_voice_name = voice
        return config

    async def synthesize(
        self,
        text: str,
        voice: str = "en-US-JennyNeural",
    ) -> bytes:
        """Convert text to speech, returning MP3 audio bytes.

        Citation markup and markdown formatting are stripped before synthesis
        so the voice output sounds natural without reading raw URLs or brackets.
        """
        if not self.speech_config:
            return b""

        text = clean_text_for_tts(text)
        if not text:
            return b""

        # Return cached result if available (avoids repeated Azure round-trips)
        cached = _tts_cache_get(text, voice)
        if cached is not None:
            return cached

        def _synthesize():
            config = self._get_synth_config(voice)
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=config,
                audio_config=None,
            )
            result = synthesizer.speak_text_async(text).get()

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return result.audio_data
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation = result.cancellation_details
                logger.error(f"Speech synthesis canceled: {cancellation.reason}")
                if cancellation.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"Error details: {cancellation.error_details}")
                return b""

            return b""

        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(self.executor, _synthesize)
        if audio:
            _tts_cache_put(text, voice, audio)
        return audio

    async def text_to_speech(self, text: str) -> bytes:
        """Convert text to speech (backward compatible)."""
        return await self.synthesize(text)

    async def synthesize_stream(
        self,
        text: str,
        voice: str = "en-US-JennyNeural",
    ) -> AsyncGenerator[bytes, None]:
        """Stream synthesized speech in chunks."""
        audio_data = await self.synthesize(text, voice)

        chunk_size = 4096
        for i in range(0, len(audio_data), chunk_size):
            yield audio_data[i:i + chunk_size]

    async def stream_text_to_speech(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream text to speech (backward compatible)."""
        async for chunk in self.synthesize_stream(text):
            yield chunk

    def get_supported_voices(self) -> list:
        """Get list of supported voices."""
        return [
            {"name": "en-US-JennyNeural", "gender": "Female", "locale": "en-US"},
            {"name": "en-US-GuyNeural", "gender": "Male", "locale": "en-US"},
            {"name": "en-US-AriaNeural", "gender": "Female", "locale": "en-US"},
            {"name": "en-US-DavisNeural", "gender": "Male", "locale": "en-US"},
            {"name": "en-GB-SoniaNeural", "gender": "Female", "locale": "en-GB"},
            {"name": "en-GB-RyanNeural", "gender": "Male", "locale": "en-GB"},
        ]

    def set_voice(self, voice_name: str) -> None:
        """Set the synthesis voice."""
        if self.speech_config:
            self.speech_config.speech_synthesis_voice_name = voice_name


# Singleton instance - initialized lazily to avoid import failures
try:
    speech_service = SpeechService()
except Exception as e:
    logger.warning(f"Failed to initialize SpeechService: {e}")
    speech_service = None
