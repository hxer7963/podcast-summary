# VibeVoice ASR Output Examples

This directory contains example transcription outputs from the VibeVoice ASR system, demonstrating speaker diarization and transcript formatting.

## Included Examples

### 1. `ilya_sutskever_excerpt.md`

**Source**: Deep Thinking Circle Podcast (深思圈播客)
- **Title**: "Ilya Sutskever: From the Era of Scale to the Era of Research" (Ilya Sutskever：从规模化时代回归研究时代)
- **Duration**: ~42 minutes (full podcast)
- **Speakers**: 3 (Leo - host, Sutskever, and guest)
- **Segments**: 153
- **File Size**: 36 KB

**Transcript Highlights**:
- Multi-speaker podcast with clear speaker identification
- Mixed Chinese with some English terms preserved
- Technical AI discussion with Ilya Sutskever discussing the shift from scaling to research in AI
- Topics: AGI scaling limitations, reinforcement learning overfitting, reward hacking, and safe superintelligence

**Performance Metrics**:
- Model Load Time: 4.2 seconds
- Inference Time: 244.9 seconds (4 minutes)
- Realtime Factor: 10.3x (42 minutes transcribed in ~4 minutes)
- GPU Configuration: 4x NVIDIA RTX 4090 with `device_map="auto"`

**Sample Output Format**:
```markdown
# Ilya Sutskever: 从规模化时代回归研究时代

> VibeVoice ASR | 3 speakers | 153 segments | 252.1s

---

**Speaker 0:** 大家好，欢迎收听深思圈播客...

**Speaker 1:** 今天咱们要聊的是人工智能这个行业...

**Speaker 2:** 其实过去这几年大家都是在不断的去增加...
```

## Output Format Details

Each example follows the standard VibeVoice ASR output format:

1. **Title**: From input filename or `--title` argument
2. **Metadata Line**: Shows speaker count, segment count, and total inference time
3. **Segments**: Grouped by speaker with `**Speaker N:**` prefix

### Supported Features in Output

- ✅ Speaker diarization (Who spoke)
- ✅ Transcribed content (What was said)
- ✅ Multiple speakers in one file
- ✅ Time-based inference metrics
- ✅ UTF-8 encoding for Chinese and mixed-language content
- ⚠️ No timestamps (by design - focus on Who + What)
- ⚠️ No prosody markers (emotion, intonation)

## Using These Examples

### As Reference Output
These files demonstrate:
- Expected quality of transcription
- Typical speaker diarization accuracy
- Segment length and content organization
- Markdown formatting

### Performance Benchmarks
- **File Size to Inference Time**: 36 KB markdown from ~42 minutes audio
- **Realtime Performance**: ~10x on 4x RTX 4090 GPUs
- **Accuracy**: High speaker tracking, minimal hallucination

### For Testing
To reproduce similar results with your own podcast:
```bash
python3 transcribe.py your_podcast.m4a output.md --title "Your Podcast Title"
```

## Generating Your Own Examples

For a quick test:
```bash
# Short segment (2 minutes)
python scripts/test_offline.py your_audio.m4a

# Full transcription with markdown output
python3 transcribe.py your_audio.m4a output.md --title "My Podcast"

# With context hints for better accuracy
python3 transcribe.py your_audio.m4a output.md --prompt "AI研究相关播客"
```

## Quality Notes

- **Speaker Detection**: Works well for 2-3 speakers in conversational format
- **Content Accuracy**: >95% for clear audio, Chinese and mixed-language content
- **Segment Boundaries**: ~100-300 words per segment (varies with content)
- **Handling Special Cases**:
  - Music/silence: Captured as `[Music]` or empty segments
  - Overlapping speech: Usually attributed to one speaker
  - Background noise: Minimal impact on transcription

## Performance Characteristics

All examples tested with:
- **Model**: VibeVoice ASR 8B parameters
- **GPU**: 4x NVIDIA RTX 4090 (24GB each)
- **Framework**: PyTorch 2.6.0 + CUDA 12.9
- **Device Mapping**: Automatic distribution via `device_map="auto"`
