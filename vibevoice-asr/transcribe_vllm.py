#!/usr/bin/env python3
"""VibeVoice ASR transcription via the vLLM service (HTTP client).

Drop-in alternative to transcribe.py that offloads inference to the running
vLLM server (see serve_vllm.sh). Output transcript.md is byte-format-identical
to transcribe.py, so all downstream podcast-pipeline stages consume it unchanged.

Robustness (long-form audio):
- Streaming with real-time repetition-loop detection + auto-recovery
  (retry from the last complete segment with a higher temperature). Ported from
  vllm_plugin/tests/test_api_auto_recover.py — the model can enter repetition
  loops on long audio because the acoustic tokenizer samples (non-deterministic).
- Long audio is split into 60-min overlapping chunks (30s overlap) and transcribed
  in parallel (default concurrency 4, auto-capped to the chunk count). The tp=4 server
  batches concurrent chunks via vLLM continuous batching (~3x throughput vs serial).
  Measured at util=0.85: peak encoder memory is a FIXED ~22.7GB/GPU regardless of chunk
  size or concurrency (it sits outside the KV budget), and 4×60min leaves 1.8GB headroom
  (KV 44%). So both larger chunks and higher util than 0.85 are OOM-forbidden — see
  serve_vllm.sh. Results are merged in temporal order.

Usage:
    python3 transcribe_vllm.py input.m4a -o transcript.md
    python3 transcribe_vllm.py input.m4a -o out.md --hotwords "Temu,拼多多"
    python3 transcribe_vllm.py a.m4a b.mp3 --output-dir ./out/
"""

import argparse
import base64
import json
import logging
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CHUNK_SECONDS = 3600      # 60-min chunks. Measured safe on the tp=4 server at
                          # gpu-memory-utilization=0.85: the fp32 audio-encoder peak is a
                          # FIXED ~22.7GB/GPU regardless of chunk size (60min==80min) or
                          # concurrency, sitting on top of the LM weights outside vLLM's KV
                          # budget. 60-min is the sweet spot — larger chunks don't lower
                          # peak memory (only lengthen runtime), and 60min keeps ~4 chunks
                          # for a 3-4h episode so parallelism stays useful. KV cache at
                          # 0.85: 3×60min→26%, 4×60min→44% (both well clear of saturation).
                          # The old 10-min limit was a loop-risk hedge that 60min does not trip
                          # (verified: clean segments, no repetition-loop recovery on 60-80min).
OVERLAP_SECONDS = 30
DEFAULT_URL = "http://localhost:8000"
MODEL_NAME = "vibevoice"
REQUEST_TIMEOUT = 3600
MAX_TOKENS = 32768
MAX_RETRIES = 3
# Mild repetition penalty as cheap insurance against loops. 1.1 keeps content
# intact (verified: ~same length/accuracy as greedy) while discouraging the
# degenerate repeats the model occasionally falls into on long generations.
# NOTE: do NOT use frequency_penalty here — it truncates real content.
REPETITION_PENALTY = 1.1

SYSTEM_PROMPT = "You are a helpful assistant that transcribes audio input into text output in JSON format."


# ── Audio utilities ─────────────────────────────────────────────────────────

def get_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr}")
    return float(json.loads(out.stdout)["format"]["duration"])


def slice_to_mp3(src: Path, start: float, dur: float) -> Path:
    """Extract [start, start+dur] as mono 24kHz mp3 (re-encode: avoids AAC
    edit-list/priming that decodes to an empty array server-side)."""
    dst = Path(tempfile.mktemp(suffix=".mp3"))
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(dur),
           "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-q:a", "4", str(dst)]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg slice failed: {r.stderr.decode('utf-8', 'replace')[-400:]}")
    return dst


def compute_chunks(duration: float) -> list[tuple[float, float]]:
    if duration <= CHUNK_SECONDS + 60:  # tolerance: don't split a ≤~61min file
        return [(0.0, duration)]
    chunks, start = [], 0.0
    while start < duration:
        end = min(start + CHUNK_SECONDS, duration)
        chunks.append((start, end - start))
        if end >= duration:
            break
        start = end - OVERLAP_SECONDS
    return chunks


def mime_of(path: Path) -> str:
    return {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
            ".flac": "audio/flac", ".ogg": "audio/ogg"}.get(path.suffix.lower(), "audio/mpeg")


# ── Repetition detection (ported from test_api_auto_recover.py) ───────────────

def detect_repetition(text: str, min_pattern_len=10, min_repeats=10, window=400):
    """Return (is_looping, good_end_pos). Detects a tail pattern repeated >= min_repeats."""
    if len(text) < min_pattern_len * min_repeats:
        return False, len(text)
    w = text[-window:] if len(text) > window else text
    for plen in range(min_pattern_len, len(w) // min_repeats + 1):
        pat = w[-plen:]
        count, pos = 0, len(w)
        while pos >= plen and w[pos - plen:pos] == pat:
            count += 1
            pos -= plen
        if count >= min_repeats:
            rep_start = len(text) - count * plen
            meaningful = len(set(pat.strip())) >= 3
            return True, rep_start + (plen if meaningful else 0)
    return False, len(text)


def _last_segment_boundary(text: str, before: int | None = None) -> int:
    """Position just after the last '},' (complete segment), optionally before `before`."""
    s = text if before is None else text[:before]
    pos = s.rfind("},")
    return pos + 1 if pos != -1 else -1  # keep the '}', drop trailing ','


def stream_transcribe(url: str, base_messages: list, debug: bool = False) -> str:
    """Stream a transcription request with repetition-loop auto-recovery.

    Returns the accumulated JSON-array text (may need closing-bracket repair,
    handled by parse_segments)."""
    def log(m):
        if debug:
            print(m, file=sys.stderr)

    accumulated = ""       # good text carried across retries (at a segment boundary)
    retry = 0
    is_recovery = False

    while retry <= MAX_RETRIES:
        messages = list(base_messages)
        if accumulated:
            messages.append({"role": "assistant", "content": accumulated})
        temp = (0.1 + 0.1 * retry) if is_recovery else 0.0
        top_p = 0.95 if is_recovery else 1.0
        payload = {"model": MODEL_NAME, "messages": messages, "max_tokens": MAX_TOKENS,
                   "temperature": temp, "top_p": top_p, "repetition_penalty": REPETITION_PENALTY,
                   "stream": True}

        resp = requests.post(f"{url}/v1/chat/completions", json=payload,
                             stream=True, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"server {resp.status_code}: {resp.text[:300]}")

        new_text, seen = "", ""
        looped = False
        for line in resp.iter_lines():
            if not line:
                continue
            dl = line.decode("utf-8")
            if not dl.startswith("data: "):
                continue
            js = dl[6:].strip()
            if js == "[DONE]":
                return accumulated + new_text
            try:
                delta = json.loads(js)["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            content = delta.get("content", "")
            if not content:
                continue
            # Stream may send incremental deltas or full accumulated text.
            to_add = content[len(seen):] if content.startswith(seen) else content
            if not to_add:
                continue
            seen += to_add
            new_text += to_add

            # When continuing from an assistant prefix, strip re-emitted array
            # framing so the concatenation stays a valid JSON array.
            if accumulated:
                st = new_text.lstrip()
                for pre in ("[{", "[", "},", "}"):
                    if st.startswith(pre) and not (pre == "}" and st.startswith("}]")):
                        new_text = st[len(pre):]
                        break

            is_loop, good_end = detect_repetition(accumulated + new_text)
            if is_loop:
                log(f"[loop detected @ {good_end}]")
                combined = accumulated + new_text
                boundary = _last_segment_boundary(combined, good_end)
                accumulated = combined[:boundary] if boundary > 0 else ""
                is_recovery = True
                retry += 1
                looped = True
                resp.close()
                break
        if not looped:
            # stream ended without [DONE] and without a loop
            return accumulated + new_text
        if retry > MAX_RETRIES:
            log("[max retries reached]")
            return accumulated
    return accumulated


# ── JSON parsing (tolerant of unterminated / truncated arrays) ────────────────

def parse_segments(text: str) -> list[dict]:
    text = text.strip()
    lo = text.find("[")
    if lo > 0:
        text = text[lo:]
    for cand in (text, text.rstrip().rstrip(",")):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    # Truncated: close after the last complete object.
    pos = text.rfind("},")
    if pos != -1:
        try:
            return json.loads(text[:pos + 1] + "]")
        except json.JSONDecodeError:
            pass
    pos = text.rfind("}")
    if pos != -1:
        try:
            return json.loads(text[:pos + 1] + "]")
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"could not parse JSON segments from: {text[:200]}")


# ── Merge + output (format-identical to transcribe.py) ────────────────────────

def merge_chunks(chunk_segments: list[list[dict]]) -> list[dict]:
    if len(chunk_segments) == 1:
        return chunk_segments[0]
    merged = []
    for idx, segs in enumerate(chunk_segments):
        for seg in segs:
            content = (seg.get("Content") or "").strip()
            if not content:
                continue
            if idx > 0 and float(seg.get("Start", 0.0)) < OVERLAP_SECONDS:
                continue  # overlap region already covered by previous chunk
            merged.append({"Speaker": seg.get("Speaker", 0), "Content": content})
    return merged


def write_output(segments: list[dict], output_path: Path, title: str,
                 elapsed: float, duration: float) -> None:
    speakers = set(s.get("Speaker", 0) for s in segments)
    with_content = [s for s in segments if (s.get("Content") or "").strip()]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"> VibeVoice ASR (vLLM) | {len(speakers)} speakers | "
                f"{len(with_content)} segments | {duration/60:.1f}min audio | "
                f"{elapsed:.1f}s inference\n\n")
        f.write("---\n\n")
        for s in segments:
            content = (s.get("Content") or "").strip()
            if content:
                f.write(f"**Speaker {s.get('Speaker', 0)}:** {content}\n\n")
    logger.info(f"  -> {output_path} ({len(with_content)} segments, {len(speakers)} speakers)")


# ── Core ───────────────────────────────────────────────────────────────────

def build_messages(clip: Path, chunk_dur: float, hotwords: str | None) -> list:
    b64 = base64.b64encode(clip.read_bytes()).decode("utf-8")
    data_url = f"data:{mime_of(clip)};base64,{b64}"
    keys = "Start time, End time, Speaker ID, Content"
    if hotwords and hotwords.strip():
        text = (f"This is a {chunk_dur:.2f} seconds audio, with extra info: {hotwords.strip()}\n\n"
                f"Please transcribe it with these keys: {keys}")
    else:
        text = f"This is a {chunk_dur:.2f} seconds audio, please transcribe it with these keys: {keys}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "audio_url", "audio_url": {"url": data_url}},
            {"type": "text", "text": text},
        ]},
    ]


def transcribe_file(input_path: Path, output_path: Path, url: str,
                    hotwords: str | None, debug: bool, concurrency: int = 1) -> None:
    duration = get_duration(input_path)
    chunks = compute_chunks(duration)
    logger.info(f"{input_path.name}: {duration/60:.1f} min, {len(chunks)} chunk(s), concurrency={concurrency}")

    t0 = time.time()

    def do_chunk(i_start_dur):
        i, (start, dur) = i_start_dur
        clip = slice_to_mp3(input_path, start, dur)
        try:
            logger.info(f"  chunk {i+1}/{len(chunks)}: start={start:.0f}s dur={dur:.0f}s")
            raw = stream_transcribe(url, build_messages(clip, dur, hotwords), debug=debug)
            segs = parse_segments(raw)
            logger.info(f"    chunk {i+1} -> {len(segs)} segments")
            return segs
        finally:
            clip.unlink(missing_ok=True)

    if concurrency > 1 and len(chunks) > 1:
        # Send chunks in parallel. The tp=4 *single-replica* server batches them via
        # vLLM continuous batching (NOT data-parallel replicas) — measured ~3x throughput
        # vs serial. concurrency is capped at min(concurrency, len(chunks)) so a short
        # file never over-subscribes. At util=0.85 the measured safe ceiling is 4 parallel
        # 60-min chunks (KV 44%, 1.8GB headroom) — do not raise concurrency above 4.
        workers = min(concurrency, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            chunk_segments = list(ex.map(do_chunk, enumerate(chunks)))
    else:
        chunk_segments = [do_chunk((i, c)) for i, c in enumerate(chunks)]

    merged = merge_chunks(chunk_segments)
    elapsed = time.time() - t0
    write_output(merged, output_path, input_path.stem, elapsed, duration)
    print(f"TRANSCRIPT={output_path}")


def main():
    p = argparse.ArgumentParser(description="Transcribe audio via the VibeVoice vLLM service.")
    p.add_argument("inputs", nargs="+", type=Path)
    p.add_argument("-o", "--output", type=Path, default=None, help="output .md (single-file mode)")
    p.add_argument("--output-dir", type=Path, default=None, help="output dir (multi-file mode)")
    p.add_argument("--url", default=DEFAULT_URL, help=f"vLLM server URL (default {DEFAULT_URL})")
    p.add_argument("--hotwords", default=None, help="comma-separated proper nouns / terms")
    p.add_argument("--concurrency", type=int, default=4,
                   help="parallel chunk requests (default 4, capped at the chunk count). The "
                        "tp=4 single-replica server batches concurrent requests via vLLM "
                        "continuous batching — measured ~3x vs serial. 4×60min at util=0.85 "
                        "is the measured safe ceiling (KV 44%, 1.8GB headroom); do not exceed 4.")
    p.add_argument("--debug", action="store_true", help="log recovery info to stderr")
    args = p.parse_args()

    for f in args.inputs:
        if not f.exists():
            logger.error(f"file not found: {f}"); sys.exit(1)
    try:
        if requests.get(f"{args.url}/health", timeout=10).status_code != 200:
            raise RuntimeError("unhealthy")
    except Exception as e:
        logger.error(f"vLLM service not reachable at {args.url} ({e}). "
                     f"Start it:  bash serve_vllm.sh start")
        sys.exit(1)

    single = len(args.inputs) == 1 and args.output_dir is None
    failed = 0
    if single:
        out = args.output or args.inputs[0].with_suffix(".md")
        try:
            transcribe_file(args.inputs[0], out, args.url, args.hotwords, args.debug, args.concurrency)
        except Exception as e:
            logger.error(f"failed: {e}"); failed += 1
    else:
        out_dir = args.output_dir or Path(".")
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in args.inputs:
            try:
                transcribe_file(f, out_dir / f"{f.stem}.md", args.url, args.hotwords, args.debug, args.concurrency)
            except Exception as e:
                logger.error(f"failed {f.name}: {e}"); failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
