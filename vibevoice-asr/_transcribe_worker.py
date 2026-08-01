#!/usr/bin/env python3
"""Worker subprocess for DP transcription.

This script is launched by transcribe.py as an independent process.
Each worker:
  1. Loads the model onto its assigned GPU (via CUDA_VISIBLE_DEVICES)
  2. Processes its assigned WAV chunks (batch if multiple)
  3. Writes JSON results to output files

Usage (called by transcribe.py, not directly):
    python3 _transcribe_worker.py \
        --gpu 0 \
        --model $VIBEVOICE_MODEL_PATH \
        --wavs '["chunk0.wav", "chunk1.wav"]' \
        --outputs '["result0.json", "result1.json"]' \
        --prompt "投资播客"
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Set LD_LIBRARY_PATH for NVIDIA packages (main project venv)
VENV_DIR = Path(__file__).resolve().parent.parent / ".venv/lib/python3.12/site-packages/nvidia"
NVIDIA_LIBS = [
    "cusparselt/lib", "cudnn/lib", "cublas/lib", "cuda_runtime/lib",
    "nvjitlink/lib", "cufft/lib", "cusolver/lib", "cusparse/lib",
    "curand/lib", "nccl/lib",
]
nvidia_paths = ":".join(str(VENV_DIR / lib) for lib in NVIDIA_LIBS if (VENV_DIR / lib).exists())
os.environ["LD_LIBRARY_PATH"] = f"{nvidia_paths}:/usr/local/cuda/lib64:{os.environ.get('LD_LIBRARY_PATH', '')}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--wavs", type=str, required=True, help="JSON list of WAV file paths")
    parser.add_argument("--outputs", type=str, required=True, help="JSON list of output JSON paths")
    parser.add_argument("--prompt", type=str, default=None)
    args = parser.parse_args()

    wav_paths = [Path(p) for p in json.loads(args.wavs)]
    output_paths = [Path(p) for p in json.loads(args.outputs)]

    assert len(wav_paths) == len(output_paths), "wavs and outputs must have same length"

    # CUDA_VISIBLE_DEVICES is set by parent, so device is always "cuda:0"
    device = "cuda:0"

    import torch
    from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration

    t0 = time.time()
    processor = AutoProcessor.from_pretrained(args.model)
    model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, device_map=device,
    )
    model.eval()
    load_time = time.time() - t0
    print(f"model loaded in {load_time:.1f}s", flush=True)

    # Process each WAV file sequentially on this GPU
    # (could batch if multiple chunks assigned, but sequential is simpler
    #  and avoids memory spikes from batching long audio)
    total_segments = 0
    t0 = time.time()

    for wav_path, output_path in zip(wav_paths, output_paths):
        audio_list = [str(wav_path)]

        if args.prompt:
            prompts = [args.prompt]
            inputs = processor.apply_transcription_request(
                audio=audio_list, prompt=prompts
            ).to(model.device, model.dtype)
        else:
            inputs = processor.apply_transcription_request(
                audio=audio_list
            ).to(model.device, model.dtype)

        with torch.no_grad():
            output_ids = model.generate(**inputs, max_new_tokens=8192)
        generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
        try:
            results = processor.decode(generated_ids, return_format="parsed")
            segments = results[0]
        except Exception as decode_err:
            # Fallback: decode as raw text to avoid total loss on JSON parse failure
            print(f"WARNING: parsed decode failed ({decode_err}), falling back to raw text", file=sys.stderr, flush=True)
            raw_text = processor.tokenizer.decode(generated_ids[0], skip_special_tokens=True)
            segments = [{"text": raw_text, "speaker": "unknown", "start": 0, "end": 0}]

        total_segments += len(segments)

        # Write results as JSON
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(segments, ensure_ascii=False),
            encoding="utf-8",
        )

    elapsed = time.time() - t0
    print(f"inference done: {total_segments} segments in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
