#!/usr/bin/env python3
"""Offline test script for VibeVoice ASR model.

Usage:
    uv run python scripts/test_offline.py [path/to/audio.m4a]

If no audio path provided, uses a short test segment from existing podcasts.
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import torch


def convert_to_wav(input_path: Path, output_path: Path) -> None:
    """Convert audio to 24kHz mono WAV."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ar", "24000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        sys.exit(1)


def extract_segment(input_path: Path, output_path: Path, duration: int = 120) -> None:
    """Extract first N seconds for a quick test."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", str(duration),
        "-ar", "24000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr}")
        sys.exit(1)


def find_test_audio() -> Path | None:
    """Find an existing podcast audio file for testing.

    Looks in $PODCAST_TEST_AUDIO_DIR or ./audios by default. Override the
    directory with the environment variable if your audio collection lives
    elsewhere.
    """
    import os
    audio_dir = Path(os.environ.get("PODCAST_TEST_AUDIO_DIR", "audios"))
    if audio_dir.exists():
        for audio_file in audio_dir.rglob("*.m4a"):
            return audio_file
        for audio_file in audio_dir.rglob("*.mp3"):
            return audio_file
    return None


def main():
    import os
    model_path = os.environ.get(
        "VIBEVOICE_MODEL_PATH",
        "/workspace/models/VibeVoice-ASR",
    )

    # Determine audio input
    if len(sys.argv) > 1:
        audio_input = Path(sys.argv[1])
        if not audio_input.exists():
            print(f"Error: {audio_input} not found")
            sys.exit(1)
    else:
        audio_input = find_test_audio()
        if audio_input is None:
            print("Error: No audio file found. Provide a path as argument.")
            sys.exit(1)

    print(f"Audio input: {audio_input}")
    print(f"Model path:  {model_path}")
    print(f"GPUs:        {torch.cuda.device_count()} x {torch.cuda.get_device_name(0)}")
    print()

    # Convert to WAV (first 2 minutes for quick test)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    print("Converting to 24kHz WAV (first 2 minutes)...")
    extract_segment(audio_input, wav_path, duration=120)
    print(f"WAV file: {wav_path} ({wav_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print()

    # Load model
    print("Loading model...")
    t0 = time.time()

    from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration

    processor = AutoProcessor.from_pretrained(model_path)
    model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
        model_path,
        device_map="auto",
        torch_dtype=torch.bfloat16,
    )
    load_time = time.time() - t0
    print(f"Model loaded in {load_time:.1f}s")
    print(f"Device: {model.device}")
    print()

    # Run inference
    print("Running transcription...")
    t0 = time.time()

    inputs = processor.apply_transcription_request(audio=str(wav_path)).to(
        model.device, model.dtype
    )
    output_ids = model.generate(**inputs)
    generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
    transcription = processor.decode(generated_ids, return_format="parsed")[0]

    infer_time = time.time() - t0
    print(f"Inference completed in {infer_time:.1f}s")
    print()

    # Display results
    print("=" * 80)
    print("TRANSCRIPTION RESULTS")
    print("=" * 80)

    speakers = set()
    for seg in transcription:
        speaker = seg.get("Speaker", 0)
        content = seg.get("Content", "")
        speakers.add(speaker)
        print(f"[Speaker {speaker}] {content}")
        print()

    print("=" * 80)
    print(f"Total segments: {len(transcription)}")
    print(f"Unique speakers: {len(speakers)} ({sorted(speakers)})")
    print(f"Inference speed: {120 / infer_time:.1f}x realtime")
    print("=" * 80)

    # Cleanup
    wav_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
