"""Per-second "something just happened" timelines, fused into one score, then peak-picked.

Every signal ends up in [0, 1] on a 1-second grid:
  community_clips  viewers literally clipped this — the strongest signal there is
  heatmap          YouTube "most replayed"
  chat             message velocity + reaction emotes vs. the stream's own baseline
  audio            loudness spikes vs. the local baseline (yelling, laughing, explosions)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EPS = 1e-9


def smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(x) == 0:
        return x.astype(float)
    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def block_baseline(x: np.ndarray, block: int = 300) -> np.ndarray:
    """Cheap rolling median: median per block, linearly interpolated back to full length."""
    n = len(x)
    if n == 0:
        return x
    centers, medians = [], []
    for i in range(0, n, block):
        seg = x[i : i + block]
        centers.append(i + len(seg) / 2)
        medians.append(float(np.median(seg)))
    if len(centers) == 1:
        return np.full(n, medians[0])
    return np.interp(np.arange(n), centers, medians)


def robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    return (x - med) / (mad + EPS)


def to_unit(z: np.ndarray, cap: float = 6.0) -> np.ndarray:
    return np.clip(z, 0, cap) / cap


# ---------------------------------------------------------------------------- individual signals
def audio_signal(db_per_sec: np.ndarray) -> np.ndarray:
    if len(db_per_sec) == 0:
        return db_per_sec
    rel = db_per_sec - block_baseline(db_per_sec, 300)
    return to_unit(robust_z(smooth(rel, 3)), cap=5.0)


def chat_signal(counts_per_sec: np.ndarray, delay: int = 7) -> np.ndarray:
    """counts_per_sec: (weighted) chat messages per second over the VOD."""
    if len(counts_per_sec) == 0 or counts_per_sec.sum() == 0:
        return np.zeros_like(counts_per_sec, dtype=float)
    rate = smooth(counts_per_sec.astype(float), 10)
    if delay > 0:  # chat reacts after the moment: shift the curve earlier
        rate = np.concatenate([rate[delay:], np.zeros(delay)])
    baseline = block_baseline(rate, 600) + 0.5
    ratio = rate / baseline
    return to_unit(robust_z(np.log1p(ratio)), cap=5.0)


def clips_signal(clips: list[dict], duration: int) -> np.ndarray:
    """Each community clip adds weight over its span, more toward the end (where the payoff is)."""
    x = np.zeros(duration)
    for c in clips:
        if c.get("offset") is None or c.get("is_gta") is False:
            continue
        start = int(max(0, c["offset"]))
        length = int(max(5, c.get("duration") or 30))
        end = min(duration, start + length)
        if end <= start:
            continue
        weight = 1.0 + math.log1p(c.get("views") or 0)
        ramp = np.linspace(0.5, 1.0, end - start)
        x[start:end] += weight * ramp
    if x.max() > 0:
        x = x / x.max()
    return smooth(x, 5)


def heatmap_signal(heatmap: list[dict] | None, duration: int) -> np.ndarray:
    x = np.zeros(duration)
    if not heatmap:
        return x
    for h in heatmap:
        s, e = int(h.get("start_time", 0)), int(math.ceil(h.get("end_time", 0)))
        x[max(0, s) : min(duration, e)] = h.get("value", 0)
    # the heatmap's floor is "normal watching"; only the bumps matter
    if x.max() > 0:
        x = np.clip((x - np.median(x)) / (x.max() - np.median(x) + EPS), 0, 1)
    return x


def game_mask(clips: list[dict], duration: int) -> np.ndarray | None:
    """When a VOD mixes games, label each second by its nearest community clip's game."""
    labeled = sorted((c["offset"], bool(c.get("is_gta", True))) for c in clips if c.get("offset") is not None)
    if not labeled or all(g for _, g in labeled):
        return None
    offsets = np.array([o for o, _ in labeled])
    flags = np.array([g for _, g in labeled])
    t = np.arange(duration)
    idx = np.clip(np.searchsorted(offsets, t), 1, len(offsets) - 1) if len(offsets) > 1 else np.zeros(duration, int)
    if len(offsets) > 1:
        left = idx - 1
        nearest = np.where(np.abs(t - offsets[left]) <= np.abs(offsets[idx] - t), left, idx)
    else:
        nearest = idx
    return flags[nearest]


# ---------------------------------------------------------------------------- fusion + peaks
def fuse(signals: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    active = {k: v for k, v in signals.items() if v is not None and len(v) and v.max() > 0}
    if not active:
        n = max((len(v) for v in signals.values() if v is not None), default=0)
        return np.zeros(n)
    n = max(len(v) for v in active.values())
    total = np.zeros(n)
    wsum = 0.0
    for name, sig in active.items():
        w = weights.get(name, 0.2)
        padded = np.zeros(n)
        padded[: len(sig)] = sig
        total += w * padded
        wsum += w
    return total / (wsum + EPS)


@dataclass
class Peak:
    t: int
    start: float
    end: float
    score: float


def pick_peaks(
    score: np.ndarray,
    k: int,
    window: float,
    skip_start: float = 0,
    skip_end: float = 60,
    mask: np.ndarray | None = None,
    lead: float = 0.7,
) -> list[Peak]:
    """Greedy non-max suppression. The window leans before the peak (setup → payoff)."""
    n = len(score)
    if n == 0:
        return []
    s = score.astype(float).copy()
    s[: int(min(n, skip_start))] = 0
    if skip_end and n > skip_end * 4:
        s[max(0, n - int(skip_end)) :] = 0
    if mask is not None:
        s[: len(mask)] *= mask[:n].astype(float)
    peaks: list[Peak] = []
    min_sep = int(window * 0.9)
    while len(peaks) < k:
        t = int(np.argmax(s))
        val = float(s[t])
        if val <= 0.02:
            break
        start = max(0.0, t - window * lead)
        end = min(float(n), start + window)
        start = max(0.0, end - window)
        peaks.append(Peak(t=t, start=start, end=end, score=val))
        s[max(0, t - min_sep) : min(n, t + min_sep)] = 0
    return peaks
