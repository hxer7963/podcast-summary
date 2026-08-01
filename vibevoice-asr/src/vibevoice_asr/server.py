"""FastAPI server for VibeVoice ASR transcription."""

import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from vibevoice_asr.audio import convert_to_wav, ensure_temp_dir
from vibevoice_asr.config import settings
from vibevoice_asr.model import asr_model
from vibevoice_asr.schemas import (
    HealthResponse,
    TranscriptionResponse,
    TranscriptionSegment,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model on startup, cleanup on shutdown."""
    logger.info("Starting VibeVoice ASR server...")
    asr_model.load()
    ensure_temp_dir()
    yield
    logger.info("Shutting down VibeVoice ASR server.")


app = FastAPI(
    title="VibeVoice ASR",
    description="Podcast transcription with speaker diarization",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok" if asr_model.is_loaded else "loading",
        model_loaded=asr_model.is_loaded,
        model_path=settings.model_path,
        gpu_count=torch.cuda.device_count(),
    )


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    audio: UploadFile = File(..., description="Audio file (m4a, mp3, wav, flac)"),
    prompt: str | None = Form(None, description="Optional context/hotwords for better accuracy"),
):
    """Transcribe audio with speaker diarization.

    Returns structured segments with speaker ID and content.
    """
    if not asr_model.is_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    # Save uploaded file to temp location
    temp_dir = ensure_temp_dir()
    suffix = Path(audio.filename).suffix if audio.filename else ".m4a"
    temp_input = temp_dir / f"upload_{id(audio)}{suffix}"

    try:
        content = await audio.read()
        temp_input.write_bytes(content)

        # Convert to 24kHz WAV
        logger.info(f"Converting {audio.filename} ({len(content) / 1024 / 1024:.1f} MB) to WAV...")
        wav_path = convert_to_wav(temp_input)

        # Run inference
        logger.info(f"Starting transcription...")
        raw_segments = await asr_model.transcribe(str(wav_path), prompt=prompt)

        # Build response (Who + What only, no timestamps)
        segments = [
            TranscriptionSegment(
                speaker=seg.get("Speaker", 0),
                content=seg.get("Content", ""),
            )
            for seg in raw_segments
            if seg.get("Content", "").strip()
        ]

        speakers = set(s.speaker for s in segments)
        logger.info(
            f"Transcription complete: {len(segments)} segments, {len(speakers)} speakers"
        )

        return TranscriptionResponse(
            segments=segments,
            num_speakers=len(speakers),
            total_segments=len(segments),
        )

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        # Cleanup temp files
        if temp_input.exists():
            temp_input.unlink()
        wav_candidate = temp_dir / f"{temp_input.stem}_24k.wav"
        if wav_candidate.exists():
            wav_candidate.unlink()


def main():
    """Entry point for the server."""
    uvicorn.run(
        "vibevoice_asr.server:app",
        host=settings.host,
        port=settings.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
