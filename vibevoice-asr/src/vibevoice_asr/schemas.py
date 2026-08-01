"""Pydantic schemas for API request/response models."""

from pydantic import BaseModel


class TranscriptionSegment(BaseModel):
    speaker: int
    content: str


class TranscriptionResponse(BaseModel):
    segments: list[TranscriptionSegment]
    num_speakers: int
    total_segments: int


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_path: str
    gpu_count: int
