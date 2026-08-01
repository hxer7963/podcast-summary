"""Unit tests for API schemas."""

import pytest

from vibevoice_asr.schemas import (
    HealthResponse,
    TranscriptionResponse,
    TranscriptionSegment,
)


class TestTranscriptionSegment:
    """Test TranscriptionSegment schema."""

    @pytest.mark.unit
    def test_segment_creation(self):
        """Test creating a transcription segment."""
        seg = TranscriptionSegment(speaker=0, content="Hello world")
        assert seg.speaker == 0
        assert seg.content == "Hello world"

    @pytest.mark.unit
    def test_segment_serialization(self):
        """Test serializing segment to JSON."""
        seg = TranscriptionSegment(speaker=1, content="Test content")
        data = seg.model_dump()
        assert data == {"speaker": 1, "content": "Test content"}


class TestTranscriptionResponse:
    """Test TranscriptionResponse schema."""

    @pytest.mark.unit
    def test_response_creation(self):
        """Test creating a transcription response."""
        segments = [
            TranscriptionSegment(speaker=0, content="Hello"),
            TranscriptionSegment(speaker=1, content="World"),
        ]
        response = TranscriptionResponse(
            segments=segments, num_speakers=2, total_segments=2
        )
        assert len(response.segments) == 2
        assert response.num_speakers == 2
        assert response.total_segments == 2

    @pytest.mark.unit
    def test_response_serialization(self):
        """Test serializing response to JSON."""
        segments = [
            TranscriptionSegment(speaker=0, content="Test"),
        ]
        response = TranscriptionResponse(
            segments=segments, num_speakers=1, total_segments=1
        )
        data = response.model_dump()
        assert data["num_speakers"] == 1
        assert data["total_segments"] == 1
        assert len(data["segments"]) == 1


class TestHealthResponse:
    """Test HealthResponse schema."""

    @pytest.mark.unit
    def test_health_response_ok(self):
        """Test creating a healthy status response."""
        response = HealthResponse(
            status="ok", model_loaded=True, model_path="/path/to/model", gpu_count=4
        )
        assert response.status == "ok"
        assert response.model_loaded is True
        assert response.gpu_count == 4

    @pytest.mark.unit
    def test_health_response_loading(self):
        """Test creating a loading status response."""
        response = HealthResponse(
            status="loading", model_loaded=False, model_path="/path/to/model", gpu_count=0
        )
        assert response.status == "loading"
        assert response.model_loaded is False
