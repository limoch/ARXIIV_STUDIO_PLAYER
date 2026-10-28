"""DSP primitives for ARXIIV.STUDIO's future two-deck real-time audio engine.

This module deliberately has no Qt dependency so the audio math can be tested
headlessly. `decode_audio_ffmpeg` detects the actual container/codec via FFmpeg,
not the filename extension. `mix_stereo_blocks` mixes already-decoded float32 PCM.
The current GUI still uses QMediaPlayer; this module is the tested foundation for
replacing that playback layer, not yet a drop-in live-output backend.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class AudioInfo:
    path: str
    codec: str
    container: str
    sample_rate: int
    channels: int
    duration: float


def probe_audio(path: str | Path, ffprobe: str | None = None) -> AudioInfo:
    """Inspect media by content using ffprobe; extension is not trusted."""
    binary = ffprobe or shutil.which("ffprobe")
    if not binary:
        raise RuntimeError("ffprobe not found. Install FFmpeg and add it to PATH.")
    p = str(Path(path).expanduser().resolve())
    result = subprocess.run(
        [binary, "-v", "error", "-show_entries",
         "format=format_name,duration:stream=codec_type,codec_name,sample_rate,channels",
         "-of", "json", p], capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Cannot read audio: {p}")
    data = json.loads(result.stdout)
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not stream:
        raise ValueError(f"No audio stream found: {p}")
    fmt = data.get("format", {})
    return AudioInfo(
        path=p, codec=str(stream.get("codec_name", "unknown")),
        container=str(fmt.get("format_name", "unknown")),
        sample_rate=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        duration=max(0.0, float(fmt.get("duration") or 0.0)),
    )


def decode_audio_ffmpeg(path: str | Path, sample_rate: int = 44100,
                        ffmpeg: str | None = None) -> np.ndarray:
    """Decode any FFmpeg-supported audio to normalized float32 stereo PCM.

    Intended for offline analysis/tests and prebuffering. For long tracks, use
    the future streaming decoder rather than keeping the entire PCM array.
    """
    binary = ffmpeg or shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg not found. Install FFmpeg and add it to PATH.")
    p = str(Path(path).expanduser().resolve())
    cmd = [binary, "-nostdin", "-v", "error", "-i", p, "-map", "0:a:0",
           "-vn", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2",
           "-ar", str(int(sample_rate)), "pipe:1"]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace").strip() or "FFmpeg decode failed")
    usable = len(result.stdout) - (len(result.stdout) % 8)  # 2 float32 channels per frame
    if usable == 0:
        raise ValueError(f"Decoded audio is empty: {p}")
    return np.frombuffer(result.stdout[:usable], dtype="<f4").reshape(-1, 2).copy()


def crossfade_gains(position: float, curve: str = "equal_power") -> tuple[float, float]:
    """Return normalized A/B gains for position in [0, 1]."""
    t = float(np.clip(position, 0.0, 1.0))
    name = curve.lower().replace("-", "_").replace(" ", "_")
    if name in ("equal_power", "blend", "constant_power"):
        return float(np.cos(t * np.pi / 2)), float(np.sin(t * np.pi / 2))
    if name in ("linear", "fade"):
        return 1.0 - t, t
    if name == "cut":
        return (1.0, 0.0) if t < 0.5 else (0.0, 1.0)
    raise ValueError(f"Unknown crossfade curve: {curve}")


def _as_stereo(block: np.ndarray | None, frames: int) -> np.ndarray:
    if block is None:
        return np.zeros((frames, 2), dtype=np.float32)
    x = np.asarray(block, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] != 2:
        raise ValueError("Audio blocks must have shape (frames, 2)")
    if len(x) < frames:
        out = np.zeros((frames, 2), dtype=np.float32)
        out[:len(x)] = x
        return out
    return x[:frames]


def mix_stereo_blocks(deck_a: np.ndarray | None, deck_b: np.ndarray | None,
                      *, crossfader: float = 0.0, gain_a: float = 1.0,
                      gain_b: float = 1.0, master_gain: float = 1.0,
                      curve: str = "linear", limiter: bool = True) -> np.ndarray:
    """Mix two stereo float32 blocks. crossfader=-1 is A, +1 is B."""
    if deck_a is None and deck_b is None:
        return np.zeros((0, 2), dtype=np.float32)
    frames = len(deck_a) if deck_a is not None else len(deck_b)
    a, b = _as_stereo(deck_a, frames), _as_stereo(deck_b, frames)
    pan = float(np.clip((float(crossfader) + 1.0) / 2.0, 0.0, 1.0))
    ga, gb = crossfade_gains(pan, curve)
    out = a * (ga * max(0.0, float(gain_a))) + b * (gb * max(0.0, float(gain_b)))
    out *= max(0.0, float(master_gain))
    if limiter:
        # Soft clip avoids hard discontinuities from a simple np.clip limiter.
        out = np.tanh(out).astype(np.float32, copy=False)
    else:
        out = out.astype(np.float32, copy=False)
    return np.ascontiguousarray(out)


def iter_pcm_blocks(pcm: np.ndarray, block_frames: int = 1024) -> Iterator[np.ndarray]:
    """Yield bounded blocks from decoded stereo PCM for prebuffering/testing."""
    if block_frames <= 0:
        raise ValueError("block_frames must be positive")
    for start in range(0, len(pcm), block_frames):
        yield np.ascontiguousarray(pcm[start:start + block_frames], dtype=np.float32)
