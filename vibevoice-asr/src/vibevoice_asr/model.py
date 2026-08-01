"""Model loading and inference logic."""

import asyncio
import logging
from pathlib import Path

import torch
from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration

from vibevoice_asr.config import settings

logger = logging.getLogger(__name__)


class ASRModel:
    """Singleton wrapper for VibeVoice ASR model with async inference support."""

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent)
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        """Load model and processor from local path."""
        model_path = settings.model_path
        logger.info(f"Loading processor from {model_path}...")
        self._processor = AutoProcessor.from_pretrained(model_path)

        logger.info(f"Loading model from {model_path} with device_map='auto'...")
        self._model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
            model_path,
            device_map="auto",
            torch_dtype=torch.bfloat16,
        )
        self._loaded = True
        logger.info("Model loaded successfully.")

    def _transcribe_sync(self, audio_path: str, prompt: str | None = None) -> list[dict]:
        """Run transcription inference (blocking)."""
        kwargs = {"audio": audio_path}
        if prompt:
            kwargs["prompt"] = prompt

        inputs = self._processor.apply_transcription_request(**kwargs).to(
            self._model.device, self._model.dtype
        )

        output_ids = self._model.generate(**inputs)
        generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]

        transcription = self._processor.decode(generated_ids, return_format="parsed")[0]
        return transcription

    async def transcribe(self, audio_path: str, prompt: str | None = None) -> list[dict]:
        """Async transcription with concurrency control."""
        async with self._semaphore:
            result = await asyncio.to_thread(self._transcribe_sync, audio_path, prompt)
            return result


# Global singleton
asr_model = ASRModel()
