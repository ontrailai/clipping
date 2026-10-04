"""Animated word-by-word captions + hook banner + credit, written as an ASS subtitle file.

The look: big heavy uppercase words, 2-4 at a time, the spoken word lit up in the
highlight colour, each chunk popping in. Hook = black text on white boxes up top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

W, H = 1080, 1920


@dataclass
class Style:
    font: str = "Montserrat Black"
    size: int = 92
    color: str = "#FFFFFF"
    highlight: str = "#FFE400"
    words_per_chunk: int = 3
    max_chars: int = 15
    uppercase: bool = True
    hook_font: str = "Montserrat Black"
    hook_size: int = 64
    credit_font: str = "Montserrat ExtraBold"
    credit_size: int = 36


@dataclass
class Positions:
    caption_y: int = 1400
    hook_y: int = 300
    hook_align: int = 8  # ASS numpad alignment: 8 = top-center, 5 = middle-center
    credit_y: int = 1515


LAYOUT_POSITIONS = {
    "blur_fill": Positions(caption_y=1420, hook_y=300, hook_align=8, credit_y=1530),
    "facecam_split": Positions(caption_y=1400, hook_y=700, hook_align=5, credit_y=1520),
    "center_crop": Positions(caption_y=1400, hook_y=300, hook_align=8, credit_y=1520),
}


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


_NON_BMP = re.compile(r"[\U00010000-\U0010FFFF]")


def clean(text: str) -> str:
    """Strip emoji (libass can't draw colour emoji) and ASS control characters."""
    text = _NON_BMP.sub("", text or "")
    text = text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def chunk_words(words: list[dict], max_words: int = 3, max_chars: int = 16, max_gap: float = 0.45) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        text = clean(w["w"])
        if not text:
            continue
        w = {**w, "w": text}
        cur_chars = sum(len(x["w"]) + 1 for x in cur)
        breaks = cur and (
            len(cur) >= max_words
            or cur_chars + len(text) > max_chars
            or w["s"] - cur[-1]["e"] > max_gap
            or re.search(r"[.!?]$", cur[-1]["w"])
        )
        if breaks:
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _header(style: Style) -> str:
    white, black = ass_color("#FFFFFF"), ass_color("#000000")
    shadow = ass_color("#000000", 0x64)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{style.font},{style.size},{ass_color(style.color)},{white},{black},{shadow},-1,0,0,0,100,100,0,0,1,7,3,5,70,70,0,1
Style: Hook,{style.hook_font},{style.hook_size},{black},{black},{white},{black},-1,0,0,0,100,100,0,0,3,16,0,8,90,90,0,1
Style: Credit,{style.credit_font},{style.credit_size},{white},{white},{black},{shadow},-1,0,0,0,100,100,0,0,1,3,1,8,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(
    words: list[dict],
    duration: float,
    style: Style,
    positions: Positions,
    hook: str | None = None,
    hook_seconds: float = 0,
    credit: str | None = None,
) -> str:
    lines = [_header(style)]
    base = ass_color(style.color)
    hi = ass_color(style.highlight)
    x, y = W // 2, positions.caption_y

    for chunk in chunk_words(words, style.words_per_chunk, style.max_chars):
        texts = [c["w"].upper() if style.uppercase else c["w"] for c in chunk]
        chunk_end = min(duration, chunk[-1]["e"] + 0.12)
        for i, word in enumerate(chunk):
            start = word["s"]
            end = chunk[i + 1]["s"] if i + 1 < len(chunk) else chunk_end
            if end - start < 0.04:
                continue
            parts = []
            for j, t in enumerate(texts):
                if j == i:
                    parts.append(f"{{\\c{hi}\\fscx108\\fscy108}}{t}{{\\c{base}\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            pop = "\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
            lines.append(f"Dialogue: 1,{ts(start)},{ts(end)},Caption,,0,0,0,,{{\\an5\\pos({x},{y}){pop}}}{' '.join(parts)}")

    if hook:
        end = duration if not hook_seconds else min(duration, hook_seconds)
        lines.append(
            f"Dialogue: 2,{ts(0)},{ts(end)},Hook,,0,0,0,,"
            f"{{\\an{positions.hook_align}\\pos({x},{positions.hook_y})\\fad(120,0)}}{clean(hook)}"
        )
    if credit:
        lines.append(f"Dialogue: 0,{ts(0)},{ts(duration)},Credit,,0,0,0,,{{\\an8\\pos({x},{positions.credit_y})}}{clean(credit)}")
    return "\n".join(lines) + "\n"
