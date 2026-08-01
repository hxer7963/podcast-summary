"""Unit tests for audio processing module."""

import tempfile
from pathlib import Path

import pytest

from vibevoice_asr.audio import convert_to_wav, ensure_temp_dir


class TestAudioConversion:
    """Test audio conversion functionality."""

    @pytest.mark.unit
    def test_ensure_temp_dir_creates_directory(self, tmp_path):
        """Test that temp directory is created if it doesn't exist."""
        # Mock the settings to use our temp directory
        import vibevoice_asr.config
        original_temp_dir = vibevoice_asr.config.settings.temp_dir
        
        test_temp = tmp_path / "vibevoice"
        assert not test_temp.exists()
        
        vibevoice_asr.config.settings.temp_dir = str(test_temp)
        try:
            result = ensure_temp_dir()
            assert result.exists()
            assert result.is_dir()
            assert result == test_temp
        finally:
            vibevoice_asr.config.settings.temp_dir = original_temp_dir

    @pytest.mark.unit
    def test_ensure_temp_dir_idempotent(self, tmp_path):
        """Test that temp directory creation is idempotent."""
        import vibevoice_asr.config
        original_temp_dir = vibevoice_asr.config.settings.temp_dir
        
        test_temp = tmp_path / "vibevoice"
        vibevoice_asr.config.settings.temp_dir = str(test_temp)
        try:
            result1 = ensure_temp_dir()
            result2 = ensure_temp_dir()
            assert result1 == result2
            assert result1.exists()
        finally:
            vibevoice_asr.config.settings.temp_dir = original_temp_dir

    @pytest.mark.unit
    def test_convert_to_wav_file_not_found(self):
        """Test that convert_to_wav raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            convert_to_wav("/nonexistent/file.m4a")

    @pytest.mark.integration
    def test_convert_to_wav_with_real_audio(self, sample_audio_file, tmp_path):
        """Test WAV conversion with real audio file."""
        output_wav = tmp_path / "output.wav"
        
        result = convert_to_wav(str(sample_audio_file), str(output_wav))
        
        assert result.exists()
        assert result.suffix == ".wav"
        assert result.stat().st_size > 0

    @pytest.mark.integration
    def test_convert_to_wav_auto_output_path(self, sample_audio_file, tmp_path):
        """Test WAV conversion with auto-generated output path."""
        import vibevoice_asr.config
        original_temp_dir = vibevoice_asr.config.settings.temp_dir
        
        vibevoice_asr.config.settings.temp_dir = str(tmp_path)
        try:
            result = convert_to_wav(str(sample_audio_file))
            assert result.exists()
            assert result.suffix == ".wav"
            assert "24k" in result.name
        finally:
            vibevoice_asr.config.settings.temp_dir = original_temp_dir
