"""Unit tests for configuration module."""

import pytest

from vibevoice_asr.config import Settings, settings


class TestSettings:
    """Test configuration settings."""

    @pytest.mark.unit
    def test_settings_defaults(self):
        """Test that settings have sensible defaults."""
        s = Settings()
        assert s.model_path == "/workspace/models/VibeVoice-ASR"
        assert s.host == "0.0.0.0"
        assert s.port == 8900
        assert s.max_concurrent == 1
        assert s.temp_dir == "/tmp/vibevoice-asr"

    @pytest.mark.unit
    def test_settings_from_env(self):
        """Test that settings can be overridden via environment variables."""
        import os
        
        os.environ["VIBEVOICE_PORT"] = "9000"
        os.environ["VIBEVOICE_HOST"] = "127.0.0.1"
        
        try:
            s = Settings()
            assert s.port == 9000
            assert s.host == "127.0.0.1"
        finally:
            del os.environ["VIBEVOICE_PORT"]
            del os.environ["VIBEVOICE_HOST"]

    @pytest.mark.unit
    def test_global_settings_instance(self):
        """Test that global settings instance is available."""
        assert settings is not None
        assert isinstance(settings, Settings)
        assert hasattr(settings, "model_path")
        assert hasattr(settings, "port")
