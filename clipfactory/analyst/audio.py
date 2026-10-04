"""Loudness envelope (dB per second) streamed through ffmpeg — no giant WAV on disk."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

RATE = 8000


def loudness_per_second(path: str | Path) -> np.ndarray:
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
    )
    out: list[float] = []
    chunk_bytes = RATE * 2 * 60  # one minute at a time
    leftover = b""
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(chunk_bytes)
        if not buf:
            break
        buf = leftover + buf
        usable = len(buf) - (len(buf) % (RATE * 2))
        leftover = buf[usable:]
        samples = np.frombuffer(buf[:usable], dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size:
            frames = samples.reshape(-1, RATE)
            rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
            out.extend((20 * np.log10(rms)).tolist())
    proc.wait()
    return np.array(out, dtype=float)
