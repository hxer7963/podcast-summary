"""Audio preprocessing: convert any audio format to 24kHz mono WAV via ffmpeg."""

import subprocess
import tempfile
from pathlib import Path

from vibevoice_asr.config import settings


def ensure_temp_dir() -> Path:
    """Ensure temp directory exists."""
    path = Path(settings.temp_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def convert_to_wav(input_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Convert audio file to 24kHz mono WAV format.

    Supports m4a, mp3, wav, flac, ogg, and other ffmpeg-compatible formats.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Audio file not found: {input_path}")

    if output_path is None:
        temp_dir = ensure_temp_dir()
        output_path = temp_dir / f"{input_path.stem}_24k.wav"
    else:
        output_path = Path(output_path)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ar", "24000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr}")

    return output_path
