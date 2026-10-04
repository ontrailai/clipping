"""Word-level transcripts via faster-whisper (runs locally, no per-minute fees)."""

from __future__ import annotations

from pathlib import Path

from .. import log

logger = log.get("analyst")

_models: dict[str, object] = {}


def _model(size: str):
    if size not in _models:
        from faster_whisper import WhisperModel  # heavy import, only when needed

        logger.info("loading whisper model '%s' (first run downloads it)", size)
        _models[size] = WhisperModel(size, device="auto", compute_type="int8")
    return _models[size]


def transcribe(path: str | Path, model_size: str = "small", language: str | None = "en") -> list[dict]:
    """Returns [{"w": word, "s": start, "e": end}, ...] in seconds relative to the file."""
    model = _model(model_size)
    segments, _info = model.transcribe(
        str(path), word_timestamps=True, vad_filter=True, language=language or None, beam_size=5,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": round(float(w.start), 3), "e": round(float(w.end), 3)})
    return words


def lines(words: list[dict], max_words: int = 14, max_gap: float = 0.9) -> list[tuple[float, str]]:
    """Group words into readable timestamped lines for prompts."""
    out: list[tuple[float, str]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["s"] - cur[-1]["e"] > max_gap):
            out.append((cur[0]["s"], " ".join(x["w"] for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        out.append((cur[0]["s"], " ".join(x["w"] for x in cur)))
    return out


def window(words: list[dict], start: float, end: float) -> list[dict]:
    """Words inside [start, end], re-timed so start = 0."""
    return [
        {"w": w["w"], "s": round(max(0.0, w["s"] - start), 3), "e": round(min(end, w["e"]) - start, 3)}
        for w in words
        if w["e"] > start + 0.05 and w["s"] < end - 0.05
    ]
