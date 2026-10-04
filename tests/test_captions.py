from clipfactory.editor import captions
from tests.conftest import fake_words


def test_chunking_respects_limits_and_pauses():
    words = fake_words("no way he just stole the cop car")
    words[4]["s"] += 2.0  # long pause before "stole"
    words[4]["e"] += 2.0
    chunks = captions.chunk_words(words, max_words=3, max_chars=15)
    assert all(len(c) <= 3 for c in chunks)
    assert all(sum(len(w["w"]) + 1 for w in c) - 1 <= 15 for c in chunks)
    assert any(c[0]["w"] == "stole" for c in chunks)


def test_clean_strips_emoji_and_ass_codes():
    assert captions.clean("LETS GO 😭🔥 {\\b1}hi") == "LETS GO (b1)hi"


def test_ass_color_is_bgr():
    assert captions.ass_color("#FFE400") == "&H0000E4FF"


def test_build_ass_has_highlight_hook_and_credit():
    words = fake_words("he really did that")
    ass = captions.build_ass(words, 5.0, captions.Style(), captions.LAYOUT_POSITIONS["blur_fill"],
                             hook="XQC GETS BETRAYED 😭", credit="@xqc • Kick")
    lines = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    caption_lines = [line for line in lines if ",Caption," in line]
    assert len(caption_lines) == 4  # one event per spoken word
    assert all("&H0000E4FF" in line for line in caption_lines)
    assert "REALLY" in ass  # uppercased
    hook = next(line for line in lines if ",Hook," in line)
    assert "XQC GETS BETRAYED" in hook and "😭" not in hook
    assert any(",Credit," in line and "@xqc" in line for line in lines)


def test_timestamps():
    assert captions.ts(0) == "0:00:00.00"
    assert captions.ts(61.234) == "0:01:01.23"
    assert captions.ts(3725.5) == "1:02:05.50"
