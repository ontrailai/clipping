"""Caption looks. Pick one with `edit.preset` and fine-tune with `edit.caption_overrides`.
Compare them on a real clip with `clipfactory styles <clip id>`."""

from __future__ import annotations

from dataclasses import fields, replace

from .captions import Style

PRESETS: dict[str, Style] = {
    # The classic streamer-clip look: heavy white caps, spoken word goes yellow and grows,
    # key words green, white hook boxes with the key phrase in red.
    "punchy": Style(),
    # Condensed caps, spoken word sits on a yellow block, two words at a time, yellow hook bar.
    "boxed": Style(
        font="Anton", size=124, highlight="#FFE400", highlight_mode="box", emphasis="#39FF14",
        words_per_chunk=2, max_chars=12, outline=6, shadow=2,
        hook_font="Anton", hook_size=72, hook_text="#000000", hook_box="#FFE400", hook_emphasis="#C8102E",
    ),
    # Big and loud: huge caps, spoken word turns green, key words yellow, one or two words at a time.
    "loud": Style(
        size=118, highlight="#39FF14", highlight_scale=114, emphasis="#FFE400",
        words_per_chunk=2, max_chars=11, outline=9, shadow=4, hook_size=66,
    ),
    # Calmer, more cinematic: sentence case, no pop, dark hook bar with white text.
    "clean": Style(
        font="Montserrat ExtraBold", size=86, highlight="#FFE400", highlight_scale=104, emphasis="#8CFF7A",
        words_per_chunk=4, max_chars=20, uppercase=False, pop=False, outline=5, shadow=4,
        hook_font="Montserrat ExtraBold", hook_size=60, hook_text="#FFFFFF", hook_box="#111111",
        hook_emphasis="#FFE400", hook_uppercase=False,
    ),
}


def style_for(preset: str, overrides: dict | None = None) -> Style:
    if preset not in PRESETS:
        raise ValueError(f"unknown caption preset '{preset}' — choose from {', '.join(PRESETS)}")
    valid = {f.name for f in fields(Style)}
    clean = {k: v for k, v in (overrides or {}).items() if k in valid}
    return replace(PRESETS[preset], **clean)
