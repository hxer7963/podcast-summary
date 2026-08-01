#!/usr/bin/env python3
"""VibeVoice offline transcription tool.

Transcribe audio files with speaker diarization using VibeVoice ASR.

Features:
- DP=4 data parallelism via independent subprocesses (no GIL limitation)
- Smart chunking aligned to GPU count for ~4x speedup on 4-GPU systems
- Automatic fallback to single-GPU mode with --dp 1
- Supports single file or batch (multiple files) transcription

Usage:
    # Single file (DP=4 default, 4 independent processes)
    python3 transcribe.py input.m4a output.md

    # With context hint
    python3 transcribe.py input.m4a output.md --prompt "AI研究相关播客"

    # Single GPU mode (for debugging or limited VRAM)
    python3 transcribe.py input.m4a output.md --dp 1

    # Multiple files -> output directory
    python3 transcribe.py file1.m4a file2.m4a file3.m4a --output-dir ./results/
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Set LD_LIBRARY_PATH for NVIDIA packages (main project venv)
VENV_DIR = Path(__file__).resolve().parent.parent / ".venv/lib/python3.12/site-packages/nvidia"
NVIDIA_LIBS = [
    "cusparselt/lib", "cudnn/lib", "cublas/lib", "cuda_runtime/lib",
    "nvjitlink/lib", "cufft/lib", "cusolver/lib", "cusparse/lib",
    "curand/lib", "nccl/lib",
]
nvidia_paths = ":".join(str(VENV_DIR / lib) for lib in NVIDIA_LIBS if (VENV_DIR / lib).exists())
os.environ["LD_LIBRARY_PATH"] = f"{nvidia_paths}:/usr/local/cuda/lib64:{os.environ.get('LD_LIBRARY_PATH', '')}"

# --- Constants ---
# Model context: 32768 tokens. Audio tokenized at 7.5 Hz.
# 45 min = 20250 audio tokens, leaving 12518 for output (safe margin).
MAX_SEGMENT_SECONDS = 45 * 60  # 45 minutes per segment
OVERLAP_SECONDS = 30  # 30s overlap between segments for speaker continuity
NUM_GPUS = 4  # Default number of GPUs for data parallelism


# ── Audio utilities ───────────────────────────────────────────────────────────

def get_audio_duration(file_path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def convert_to_wav(input_path: Path, output_path: Path | None = None,
                   start: float | None = None, duration: float | None = None) -> Path:
    """Convert audio to 24kHz mono WAV via ffmpeg."""
    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix=".wav"))

    cmd = ["ffmpeg", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(input_path)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(output_path)]

    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr.decode(chr(39)+chr(117)+chr(116)+chr(102)+chr(45)+chr(56)+chr(39), errors=chr(39)+chr(114)+chr(101)+chr(112)+chr(108)+chr(97)+chr(99)+chr(101)+chr(39))}")
    return output_path


# ── Chunking strategy ─────────────────────────────────────────────────────────

def compute_dp_chunks(duration: float, num_gpus: int,
                      max_segment: float = MAX_SEGMENT_SECONDS,
                      overlap: float = OVERLAP_SECONDS) -> list[tuple[float, float]]:
    """Compute chunk boundaries aligned to GPU count.

    Strategy:
      - Start with num_gpus chunks
      - If any chunk > max_segment, increase to next multiple of num_gpus
      - Add overlap between adjacent chunks for speaker continuity

    Returns list of (start_time, chunk_duration) tuples.
    """
    num_chunks = num_gpus
    while True:
        net_chunk = duration / num_chunks
        if net_chunk <= max_segment:
            break
        num_chunks += num_gpus

    # Build chunk list with overlap
    step = duration / num_chunks
    chunks = []
    for i in range(num_chunks):
        if i == 0:
            start = 0.0
        else:
            start = i * step - overlap

        if i == num_chunks - 1:
            end = duration
        else:
            end = (i + 1) * step + overlap

        start = max(0.0, start)
        end = min(duration, end)
        chunks.append((start, end - start))

    return chunks


# ── Subprocess-based DP inference ─────────────────────────────────────────────

def _worker_script_path() -> Path:
    """Return path to the worker subprocess script."""
    return Path(__file__).resolve().parent / "_transcribe_worker.py"


def transcribe_dp_subprocess(input_path: Path, num_gpus: int,
                             model_path: str, prompt: str | None = None,
                             ) -> tuple[list[dict], float]:
    """Transcribe using independent subprocesses, one per GPU.

    Pipeline:
      1. Compute chunks aligned to GPU count
      2. Pre-convert all chunks to WAV in parallel (ffmpeg)
      3. Launch N independent Python processes (each loads model on its own GPU)
      4. Each process writes JSON results to a temp file
      5. Main process reads results in chunk order and merges
    """
    duration = get_audio_duration(input_path)
    logger.info(f"  Duration: {duration:.1f}s ({duration/60:.1f} min)")

    # Short audio on single GPU — no splitting needed
    if duration <= MAX_SEGMENT_SECONDS and num_gpus <= 1:
        return _transcribe_single_process(input_path, model_path, prompt, gpu_id=0)

    # Compute GPU-aligned chunks
    chunks = compute_dp_chunks(duration, num_gpus)
    num_chunks = len(chunks)
    chunks_per_gpu = num_chunks // num_gpus

    logger.info(
        f"  DP splitting: {num_chunks} chunks across {num_gpus} GPUs "
        f"({chunks_per_gpu} per GPU, overlap={OVERLAP_SECONDS}s)"
    )

    # Pre-convert all chunks to WAV in parallel
    logger.info("  Converting chunks to WAV...")
    with ThreadPoolExecutor(max_workers=min(num_chunks, 8)) as executor:
        futures = [
            executor.submit(convert_to_wav, input_path, None, start, dur)
            for start, dur in chunks
        ]
        wav_paths = [f.result() for f in futures]

    # Assign chunks to GPUs (sequential assignment for balanced load)
    # GPU 0 → chunks [0, 4, ...], GPU 1 → [1, 5, ...], etc.
    gpu_assignments: list[list[int]] = [[] for _ in range(num_gpus)]
    for i in range(num_chunks):
        gpu_assignments[i % num_gpus].append(i)

    for gpu_id, assigned in enumerate(gpu_assignments):
        logger.info(f"    GPU {gpu_id}: chunks {assigned}")

    # Create temp output files for each chunk
    result_files = [Path(tempfile.mktemp(suffix=".json")) for _ in range(num_chunks)]

    # Launch independent subprocesses (one per GPU)
    worker_script = _worker_script_path()
    venv_python = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3"

    logger.info("  Launching worker processes...")
    t0 = time.time()

    processes = []
    for gpu_id in range(num_gpus):
        assigned_chunks = gpu_assignments[gpu_id]
        if not assigned_chunks:
            continue

        # Build worker args: wav files and output files for this GPU
        worker_wavs = [str(wav_paths[i]) for i in assigned_chunks]
        worker_outputs = [str(result_files[i]) for i in assigned_chunks]

        cmd = [
            str(venv_python), str(worker_script),
            "--gpu", str(gpu_id),
            "--model", model_path,
            "--wavs", json.dumps(worker_wavs),
            "--outputs", json.dumps(worker_outputs),
        ]
        if prompt:
            cmd += ["--prompt", prompt]

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        processes.append((gpu_id, proc))

    # Wait for all processes to complete
    for gpu_id, proc in processes:
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            logger.error(f"  GPU {gpu_id} worker failed (exit {proc.returncode}):")
            logger.error(f"    {stderr.decode()[-500:]}")
            raise RuntimeError(f"Worker on GPU {gpu_id} failed")
        # Parse worker timing from stdout
        for line in stdout.decode().splitlines():
            if line.strip():
                logger.info(f"    GPU {gpu_id}: {line.strip()}")

    elapsed = time.time() - t0
    logger.info(f"  DP inference complete: {elapsed:.1f}s")

    # Read results in chunk order and merge
    all_segments = []
    for i in range(num_chunks):
        result_file = result_files[i]
        if not result_file.exists():
            logger.warning(f"  Missing result for chunk {i}, skipping")
            all_segments.append([])
            continue
        data = json.loads(result_file.read_text(encoding="utf-8"))
        all_segments.append(data)
        result_file.unlink()

    # Cleanup WAV files
    for wav in wav_paths:
        wav.unlink(missing_ok=True)

    # Merge segments in chunk order (no speaker offset — same speakers throughout)
    chunk_starts = [start for start, _ in chunks]
    merged = merge_segments_dp(all_segments, chunk_starts, OVERLAP_SECONDS)

    return merged, elapsed


def _transcribe_single_process(input_path: Path, model_path: str,
                               prompt: str | None, gpu_id: int = 0,
                               ) -> tuple[list[dict], float]:
    """Transcribe with a single GPU in current process (fallback/short audio)."""
    import torch
    from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration

    device = f"cuda:{gpu_id}"
    logger.info(f"  Loading model → {device}...")
    processor = AutoProcessor.from_pretrained(model_path)
    model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
        model_path, torch_dtype=torch.bfloat16, device_map=device,
    )
    model.eval()

    duration = get_audio_duration(input_path)

    if duration <= MAX_SEGMENT_SECONDS:
        wav_path = convert_to_wav(input_path)
        try:
            t0 = time.time()
            segments = _run_inference(model, processor, [wav_path], prompt)[0]
            elapsed = time.time() - t0
            return segments, elapsed
        finally:
            wav_path.unlink(missing_ok=True)
    else:
        # Sequential splitting for single GPU
        step = MAX_SEGMENT_SECONDS - OVERLAP_SECONDS
        chunk_starts = []
        t = 0.0
        while t < duration:
            chunk_starts.append(t)
            t += step

        logger.info(f"  Sequential splitting: {len(chunk_starts)} chunks")

        all_segments = []
        total_elapsed = 0.0

        for i, start in enumerate(chunk_starts):
            chunk_dur = min(MAX_SEGMENT_SECONDS, duration - start)
            wav_path = convert_to_wav(input_path, start=start, duration=chunk_dur)
            try:
                t0 = time.time()
                segments = _run_inference(model, processor, [wav_path], prompt)[0]
                elapsed = time.time() - t0
                total_elapsed += elapsed
                logger.info(f"    Chunk {i+1}/{len(chunk_starts)}: {len(segments)} segments in {elapsed:.1f}s")
                all_segments.append(segments)
            finally:
                wav_path.unlink(missing_ok=True)

        merged = merge_segments_dp(all_segments, chunk_starts, OVERLAP_SECONDS)
        return merged, total_elapsed


def _run_inference(model, processor, wav_paths: list[Path],
                   prompt: str | None = None) -> list[list[dict]]:
    """Run inference on a list of WAV files."""
    import torch

    audio_list = [str(p) for p in wav_paths]
    if prompt:
        prompts = [prompt] * len(audio_list)
        inputs = processor.apply_transcription_request(
            audio=audio_list, prompt=prompts
        ).to(model.device, model.dtype)
    else:
        inputs = processor.apply_transcription_request(
            audio=audio_list
        ).to(model.device, model.dtype)

    with torch.no_grad():
        output_ids = model.generate(**inputs)
    generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
    results = processor.decode(generated_ids, return_format="parsed")
    return results


# ── Merge segments ────────────────────────────────────────────────────────────

def merge_segments_dp(all_segments: list[list[dict]], chunk_starts: list[float],
                      overlap: float) -> list[dict]:
    """Merge transcription segments from multiple chunks in temporal order.

    No speaker offset is applied — the same speakers appear across chunks.
    For overlap regions: prefer content from the earlier chunk
    (it has more preceding context, so speaker assignment is more stable).
    """
    if len(all_segments) == 1:
        return all_segments[0]

    merged = []
    for chunk_idx, segments in enumerate(all_segments):
        if chunk_idx > 0:
            overlap_end_time = overlap  # relative to this chunk's start
        else:
            overlap_end_time = 0.0

        for seg in segments:
            seg_start = seg.get("Start", 0.0)
            content = seg.get("Content", "").strip()

            if not content:
                continue

            # Skip segments in the overlap region (already captured by previous chunk)
            if chunk_idx > 0 and seg_start < overlap_end_time:
                continue

            merged.append({
                "Speaker": seg.get("Speaker", 0),
                "Content": content,
            })

    return merged


# ── Output ────────────────────────────────────────────────────────────────────

def write_output(transcription: list[dict], output_path: Path,
                 title: str, elapsed: float, duration: float) -> None:
    """Write transcription to markdown file."""
    speakers = set(seg.get("Speaker", 0) for seg in transcription)
    segments_with_content = [seg for seg in transcription if seg.get("Content", "").strip()]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(
            f"> VibeVoice ASR | {len(speakers)} speakers | "
            f"{len(segments_with_content)} segments | "
            f"{duration/60:.1f}min audio | {elapsed:.1f}s inference\n\n"
        )
        f.write("---\n\n")

        for seg in transcription:
            speaker = seg.get("Speaker", 0)
            content = seg.get("Content", "").strip()
            if content:
                f.write(f"**Speaker {speaker}:** {content}\n\n")

    logger.info(f"  -> {output_path} ({len(segments_with_content)} segments, {len(speakers)} speakers)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio with VibeVoice ASR (speaker diarization). "
                    "Uses DP=4 by default with independent processes for ~4x speedup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single file (DP=4 default, 4 processes in parallel)
  python3 transcribe.py podcast.m4a transcript.md

  # Single GPU mode (legacy, for debugging)
  python3 transcribe.py podcast.m4a transcript.md --dp 1

  # Multiple files -> output directory
  python3 transcribe.py ep1.m4a ep2.m4a ep3.m4a --output-dir ./transcripts/

  # Custom context hint
  python3 transcribe.py *.m4a --output-dir ./out/ --prompt "投资播客"
""",
    )
    parser.add_argument("inputs", nargs="+", type=Path,
                        help="Input audio file(s) (m4a, mp3, wav, flac)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output file path (single file mode only)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory (multi-file mode, generates <stem>.md per file)")
    parser.add_argument("--model", type=str, default="/workspace/models/VibeVoice-ASR",
                        help="Model path (default: /workspace/models/VibeVoice-ASR)")
    parser.add_argument("--prompt", type=str, default=None,
                        help="Optional context/hotwords for better accuracy")
    parser.add_argument("--dp", type=int, default=NUM_GPUS,
                        help=f"Number of GPUs for data parallelism (default: {NUM_GPUS}, use 1 for single GPU)")

    args = parser.parse_args()

    # Validate inputs
    for p in args.inputs:
        if not p.exists():
            logger.error(f"File not found: {p}")
            sys.exit(1)

    # Determine output mode
    single_file_mode = len(args.inputs) == 1 and args.output_dir is None

    if single_file_mode:
        output_path = args.output
        if output_path is None:
            output_path = args.inputs[0].with_suffix(".md")
    else:
        output_dir = args.output_dir or Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)

    # ── Single file mode ──────────────────────────────────────────────────────
    if single_file_mode:
        input_path = args.inputs[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        title = input_path.stem
        duration = get_audio_duration(input_path)

        logger.info(f"Input: {input_path.name} ({input_path.stat().st_size/1024/1024:.1f} MB)")

        if args.dp > 1:
            transcription, elapsed = transcribe_dp_subprocess(
                input_path, args.dp, args.model, args.prompt
            )
        else:
            transcription, elapsed = _transcribe_single_process(
                input_path, args.model, args.prompt, gpu_id=0
            )
        write_output(transcription, output_path, title, elapsed, duration)

    # ── Multi-file mode ───────────────────────────────────────────────────────
    else:
        logger.info(f"Multi-file mode: {len(args.inputs)} files, dp={args.dp}")

        for input_path in args.inputs:
            duration = get_audio_duration(input_path)
            out_path = output_dir / f"{input_path.stem}.md"
            title = input_path.stem

            logger.info(f"\n{input_path.name} ({duration/60:.1f} min):")

            if args.dp > 1:
                transcription, elapsed = transcribe_dp_subprocess(
                    input_path, args.dp, args.model, args.prompt
                )
            else:
                transcription, elapsed = _transcribe_single_process(
                    input_path, args.model, args.prompt, gpu_id=0
                )
            write_output(transcription, out_path, title, elapsed, duration)

        total_duration = sum(get_audio_duration(p) for p in args.inputs)
        logger.info(
            f"\n=== Done: {len(args.inputs)} files, "
            f"{total_duration/60:.1f} min total audio ==="
        )


if __name__ == "__main__":
    main()
