"""Claude as head editor: looks at each candidate (transcript + frames + crowd signals),
throws out the weak ones, and trims the keepers to a tight setup → payoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..llm import Claude, image_block, text_block
from . import transcript

CATEGORIES = ["funny", "chaos", "clutch", "fail", "betrayal", "rage", "wholesome", "rp_story", "skill", "reaction", "other"]
FLAGS = ["none", "slur_or_hate", "sexual", "real_world_violence", "personal_info", "self_harm",
         "sponsor_or_ad", "copyrighted_music", "not_gta"]

SYSTEM = """You are the head editor of "{page_name}", a short-form clip page that posts the best GTA moments \
(GTA 6, GTA V, GTA RP) from the biggest streamers and YouTubers to TikTok, Instagram Reels and YouTube Shorts.

You get several candidate windows from one video or stream. Each window was flagged by crowd signals \
(viewers clipping it, chat exploding, replay heatmap, audio spikes) — but signals are noisy, so judge \
every window yourself from the frames, the transcript and the chat sample.

What makes a clip worth posting:
- A clear payoff: a hilarious reaction, chaotic police chase, betrayal, clutch escape, insane stunt, \
epic fail, rage moment, absurd RP dialogue, wholesome moment, or a big achievement.
- Self-contained: a stranger scrolling with zero context understands it within two seconds.
- Starts right at the setup (1-3 s before the action), never on dead air or mid-sentence. \
Ends about a second after the payoff or reaction lands. No intros, no outros, no rambling.
- Length between {min_len} and {max_len} seconds; around {target_len} s is ideal. Tighter beats longer.

Reject (keep=false) windows that are: menus, loading screens, AFK, reading donations/sponsors, \
non-GTA content, moments needing minutes of backstory, or anything with slurs/hate, sexual content, \
real-world violence, self-harm or personal info. Flag copyrighted music if a song is clearly playing.

For keepers write:
- hook_text: the banner shown on top of the video. Max 8 words, plain text, no emojis, no hashtags. \
Curiosity plus accuracy — use the creator's name when it helps ("XQC GETS BETRAYED BY HIS OWN CREW"). \
Never claim something that doesn't happen.
- title: a short factual internal title.
- what_happens: one or two factual sentences.
- start/end: seconds on the candidate's LOCAL timeline (the transcript timestamps).

virality_score calibration: 90+ would go viral on any GTA page; 75-89 strong; 60-74 decent filler; \
under 60 don't post. Be harsh — most windows should score under 70. Return one entry per candidate."""

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "is_gta_gameplay": {"type": "boolean"},
                    "keep": {"type": "boolean"},
                    "virality_score": {"type": "integer"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "hook_text": {"type": "string"},
                    "title": {"type": "string"},
                    "what_happens": {"type": "string"},
                    "reason": {"type": "string"},
                    "content_flags": {"type": "array", "items": {"type": "string", "enum": FLAGS}},
                },
            },
        }
    },
}


@dataclass
class Candidate:
    id: int
    global_start: float
    global_end: float
    local_duration: float
    words: list[dict]
    frames: list[Path] = field(default_factory=list)
    frame_times: list[float] = field(default_factory=list)
    signal_notes: list[str] = field(default_factory=list)
    chat: dict | None = None


def _hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def build_content(source: dict, candidates: list[Candidate]) -> list[dict]:
    content: list[dict] = [text_block(
        f"Creator: {source['creator']}\nPlatform: {source['platform']}\nTitle: {source.get('title') or ''}\n"
        f"Type: {'livestream VOD' if source.get('kind') == 'vod' else 'uploaded video'}\n"
        f"{len(candidates)} candidate windows follow."
    )]
    for c in candidates:
        header = (
            f"\n=== CANDIDATE {c.id} — {_hms(c.global_start)} to {_hms(c.global_end)} of the source; "
            f"local timeline 0.0–{c.local_duration:.1f}s ===\n"
        )
        if c.signal_notes:
            header += "Signals: " + "; ".join(c.signal_notes) + "\n"
        if c.chat and c.chat.get("messages"):
            header += f"Chat during window: {c.chat['messages']} msgs, reactions {c.chat.get('kinds')}, " \
                      f"sample: {' | '.join(c.chat.get('top', [])[:8])}\n"
        if c.frames:
            header += "Frames at local " + ", ".join(f"{t:.0f}s" for t in c.frame_times) + ":"
        content.append(text_block(header))
        for f in c.frames:
            content.append(image_block(f))
        lines = transcript.lines(c.words)
        body = "\n".join(f"[{t:.1f}] {text}" for t, text in lines) or "(no speech detected)"
        content.append(text_block(f"Transcript (local seconds):\n{body}"))
    return content


def judge(claude: Claude, cfg, source: dict, candidates: list[Candidate]) -> list[dict]:
    a = cfg["analysis"]
    system = SYSTEM.format(
        page_name=cfg["brand"]["page_name"], min_len=a["min_clip_seconds"],
        max_len=a["max_clip_seconds"], target_len=a["target_clip_seconds"],
    )
    result = claude.json(system, build_content(source, candidates), SCHEMA, effort=cfg["llm"]["effort_judge"])
    by_id = {c.id: c for c in candidates}
    out = []
    for m in result.get("moments", []):
        cand = by_id.get(m.get("candidate_id"))
        if cand is None:
            continue
        start, end = refine_bounds(m["start"], m["end"], cand.words, cand.local_duration,
                                   a["min_clip_seconds"], a["max_clip_seconds"])
        m["start"], m["end"] = start, end
        flags = [f for f in m.get("content_flags", []) if f != "none"]
        m["content_flags"] = flags
        blocking = {"slur_or_hate", "sexual", "real_world_violence", "personal_info", "self_harm", "not_gta", "sponsor_or_ad"}
        m["keep"] = bool(m.get("keep")) and m.get("is_gta_gameplay", True) and not (set(flags) & blocking)
        out.append(m)
    return out


def refine_bounds(start: float, end: float, words: list[dict], duration: float, min_len: float, max_len: float) -> tuple[float, float]:
    """Clamp to the file, enforce length (keeping the payoff end), and avoid cutting mid-word."""
    start, end = max(0.0, float(start)), min(float(duration), float(end))
    if end <= start:
        end = min(duration, start + min_len)
    # don't start mid-word: snap back to the start of a word we'd otherwise slice
    for w in words:
        if w["s"] < start < w["e"]:
            start = max(0.0, w["s"] - 0.15)
            break
    # let the last word finish, plus a beat for the reaction
    for w in words:
        if w["s"] < end < w["e"]:
            end = min(duration, w["e"] + 0.35)
            break
    if end - start > max_len:
        start = end - max_len
    if end - start < min_len:
        start = max(0.0, end - min_len)
        if end - start < min_len:
            end = min(duration, start + min_len)
    return round(start, 2), round(end, 2)
