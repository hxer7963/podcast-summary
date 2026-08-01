"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_path: str = "/workspace/models/VibeVoice-ASR"
    host: str = "0.0.0.0"
    port: int = 8900
    max_concurrent: int = 1
    temp_dir: str = "/tmp/vibevoice-asr"

    model_config = {"env_prefix": "VIBEVOICE_"}


settings = Settings()
