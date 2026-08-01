"""Pytest configuration and shared fixtures."""

import os
import pytest
from pathlib import Path


@pytest.fixture
def test_audio_dir():
    """Return path to test audio directory.

    Override with $PODCAST_TEST_AUDIO_DIR. Defaults to ./audios relative to
    the repo root. Tests that need audio will skip if no audio files are found.
    """
    return Path(os.environ.get("PODCAST_TEST_AUDIO_DIR", "audios"))


@pytest.fixture
def sample_audio_file(test_audio_dir):
    """Return a sample podcast audio file for testing."""
    # Find first available audio file (m4a or mp3)
    for pattern in ("*.m4a", "*.mp3"):
        for audio_file in test_audio_dir.rglob(pattern):
            return audio_file
    pytest.skip("No test audio files found; set PODCAST_TEST_AUDIO_DIR")


@pytest.fixture
def temp_output_file(tmp_path):
    """Return a temporary markdown output file."""
    return tmp_path / "output.md"
