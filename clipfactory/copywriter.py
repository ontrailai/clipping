"""✍️ Copywriter — post captions, YouTube title, hashtags, credit line.

Claude writes the creative bits (caption line, title, moment-specific hashtags, comment bait);
the credit line, CTA, base hashtags and platform length limits are enforced in code so they
can never be forgotten.
"""

from __future__ import annotations

import re

from .llm import Claude

PLATFORM_NAMES = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}

SYSTEM = """You write post copy for "{page_name}", a GTA clip page (TikTok, Instagram Reels, YouTube Shorts).

Write for scrollers: short, punchy, curiosity-first, accurate. Never invent things that don't happen \
in the clip. No hashtags inside the caption itself. At most two emojis in the caption.

Return:
- youtube_title: under 70 characters, hooky, names the creator, no hashtags.
- caption: one or two lines that make people want to watch to the end.
- engagement_question: a short question that invites comments (e.g. "Would you have stayed in the car? 👀").
- hashtags: {n_specific} specific hashtags for THIS clip — the creator's name, the game, the kind of \
moment (e.g. #xqc #gta6 #gtarp #policechase). Lowercase, no spaces."""

SCHEMA = {
    "type": "object",
    "properties": {
        "youtube_title": {"type": "string"},
        "caption": {"type": "string"},
        "engagement_question": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
}


def normalize_hashtags(tags: list[str], base: list[str], lo: int = 8, hi: int = 12) -> list[str]:
    out: list[str] = []
    for t in [*tags, *base]:
        clean = re.sub(r"[^a-z0-9_]", "", t.lower().lstrip("#"))
        if clean and f"#{clean}" not in out:
            out.append(f"#{clean}")
    return out[:hi] if len(out) >= lo else out


def credit_line(credit: str, platform: str) -> str:
    return f"🎥 {credit} on {PLATFORM_NAMES.get(platform, platform)}"


def assemble(raw: dict, *, credit: str, platform: str, brand: dict, title_fallback: str) -> dict:
    """Turn Claude's pieces into final per-platform copy with hard limits applied."""
    tags = normalize_hashtags(raw.get("hashtags", []), brand.get("base_hashtags", []),
                              brand.get("hashtags_min", 8), brand.get("hashtags_max", 12))
    caption = (raw.get("caption") or title_fallback).strip()
    question = (raw.get("engagement_question") or "").strip()
    credit_txt = credit_line(credit, platform)
    cta = brand.get("cta", "").strip()

    body = "\n\n".join(p for p in [caption, question, credit_txt, cta] if p)
    social = f"{body}\n\n{' '.join(tags)}"

    title = (raw.get("youtube_title") or title_fallback).strip()
    title = re.sub(r"\s+", " ", title.replace("<", "").replace(">", ""))
    if len(title) > 88:
        title = title[:87].rstrip() + "…"
    yt_title = f"{title} #shorts"

    return {
        "youtube_title": yt_title[:100],
        "youtube_description": social[:4900],
        "youtube_tags": [t.lstrip("#") for t in tags][:15],
        "instagram_caption": _limit_hashtags(social, 30)[:2200],
        "tiktok_caption": social[:2200],
        "hashtags": tags,
        "credit": credit_txt,
        "caption": caption,
    }


def _limit_hashtags(text: str, limit: int) -> str:
    count = 0

    def keep(m: re.Match) -> str:
        nonlocal count
        count += 1
        return m.group(0) if count <= limit else ""

    return re.sub(r"#\w+", keep, text).strip()


def write_copy(claude: Claude, cfg, moment: dict, source: dict, credit: str, transcript_text: str) -> dict:
    brand = cfg["brand"]
    n_specific = max(3, brand["hashtags_max"] - len(brand["base_hashtags"]))
    system = SYSTEM.format(page_name=brand["page_name"], n_specific=n_specific)
    if cfg.house_style:
        system += "\n\nHouse style from the page owner (follow it):\n" + cfg.house_style
    prompt = (
        f"Creator: {source['creator']} (credit as {credit}) on {PLATFORM_NAMES.get(source['platform'], source['platform'])}\n"
        f"Moment type: {moment.get('category')}\n"
        f"On-screen hook: {moment.get('hook')}\n"
        f"What happens: {moment.get('summary')}\n"
        f"Source title: {source.get('title')}\n"
        f"What's said in the clip: {transcript_text[:1500]}"
    )
    raw = claude.json(system, prompt, SCHEMA, effort=cfg["llm"]["effort_copy"], max_tokens=4000)
    return assemble(raw, credit=credit, platform=source["platform"], brand=brand, title_fallback=moment.get("title") or "")
