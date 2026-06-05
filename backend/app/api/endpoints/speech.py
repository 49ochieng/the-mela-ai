"""
Mela AI - Speech Endpoints
POST /speech/transcribe  — audio → text (STT)
POST /speech/synthesize  — text → MP3 (TTS)
GET  /speech/voices      — list available TTS voices
"""

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.security import get_current_user
from app.schemas.auth import UserInfo
from app.services.speech_service import speech_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_speech_service():
    """Raise 503 if Azure Speech is not configured."""
    if speech_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Speech service is not configured. "
                "Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
            ),
        )


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: str = "en-US",
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Transcribe uploaded audio to text.
    Accepts WAV, WebM/Opus, OGG, MP3.
    Returns { text, confidence, duration_ms }.
    """
    _require_speech_service()
    try:
        audio_data = await audio.read()
        if not audio_data:
            raise HTTPException(status_code=400, detail="Audio file is empty")

        content_type = audio.content_type or "audio/wav"
        result = await speech_service.transcribe(
            audio_data=audio_data,
            content_type=content_type,
            language=language,
        )
        return {
            "text": result.text,
            "confidence": result.confidence,
            "duration_ms": result.duration_ms,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcription failed: {e}",
        )


@router.post("/synthesize")
async def synthesize_speech(
    text: str,
    voice: str = "en-US-JennyNeural",
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Convert text to speech. Returns MP3 audio bytes.

    text  — plain text to speak (max 4000 chars)
    voice — Azure Neural voice name (default: en-US-JennyNeural)
    """
    _require_speech_service()
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="text parameter is required")

    try:
        audio_data = await speech_service.synthesize(
            text=text[:4000],
            voice=voice,
        )
        if not audio_data:
            logger.error(
                "TTS returned empty bytes for voice=%s — check AZURE_SPEECH_KEY/REGION",
                voice,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Speech synthesis returned no audio. "
                    "Check AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
                ),
            )
        return StreamingResponse(
            io.BytesIO(audio_data),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=\"speech.mp3\"",
                "Content-Length": str(len(audio_data)),
                "Cache-Control": "no-store",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Speech synthesis failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Speech synthesis failed: {e}",
        )


@router.get("/voices")
async def list_voices(
    current_user: UserInfo = Depends(get_current_user),
):
    """List available TTS voices."""
    return [
        {"id": "en-US-JennyNeural",  "name": "Jenny",  "language": "en-US", "gender": "Female"},
        {"id": "en-US-GuyNeural",    "name": "Guy",    "language": "en-US", "gender": "Male"},
        {"id": "en-US-AriaNeural",   "name": "Aria",   "language": "en-US", "gender": "Female"},
        {"id": "en-US-DavisNeural",  "name": "Davis",  "language": "en-US", "gender": "Male"},
        {"id": "en-GB-SoniaNeural",  "name": "Sonia",  "language": "en-GB", "gender": "Female"},
        {"id": "en-GB-RyanNeural",   "name": "Ryan",   "language": "en-GB", "gender": "Male"},
    ]


@router.post("/stream")
async def stream_synthesis(
    text: str,
    voice: str = "en-US-JennyNeural",
    current_user: UserInfo = Depends(get_current_user),
):
    """Stream synthesized speech in chunks."""
    _require_speech_service()
    try:
        async def generate():
            async for chunk in speech_service.synthesize_stream(text=text, voice=voice):
                yield chunk

        return StreamingResponse(generate(), media_type="audio/mpeg")
    except Exception as e:
        logger.error("Speech streaming failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Speech streaming failed: {e}",
        )
