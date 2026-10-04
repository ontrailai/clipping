from clipfactory import copywriter

BRAND = {"page_name": "GTA 6 Moments", "cta": "Follow for daily GTA 6 clips",
         "base_hashtags": ["#gta6", "#gta", "#gtavi", "#gaming", "#gtarp"], "hashtags_min": 8, "hashtags_max": 12}


def test_hashtags_normalized_deduped_and_capped():
    tags = copywriter.normalize_hashtags(["XQC", "#GTA6", "police chase", "#gta-rp", "#a", "#b", "#c", "#d", "#e", "#f"],
                                         BRAND["base_hashtags"])
    assert tags[0] == "#xqc"
    assert tags.count("#gta6") == 1
    assert "#policechase" in tags and "#gtarp" in tags
    assert len(tags) == 12


def test_assemble_always_credits_and_limits():
    raw = {"youtube_title": "x" * 150, "caption": "He did NOT see that coming 😭",
           "engagement_question": "Would you have run?", "hashtags": ["#xqc", "#policechase"]}
    copy = copywriter.assemble(raw, credit="@xqc", platform="kick", brand=BRAND, title_fallback="t")
    assert "🎥 @xqc on Kick" in copy["instagram_caption"]
    assert "🎥 @xqc on Kick" in copy["tiktok_caption"]
    assert copy["youtube_title"].endswith("#shorts") and len(copy["youtube_title"]) <= 100
    assert "Follow for daily GTA 6 clips" in copy["instagram_caption"]
    assert copy["instagram_caption"].count("#") <= 30


def test_assemble_without_claude_output_still_works():
    copy = copywriter.assemble({}, credit="@kaicenat", platform="twitch", brand=BRAND, title_fallback="Kai gets robbed")
    assert copy["youtube_title"].startswith("Kai gets robbed")
    assert "#gta6" in copy["tiktok_caption"]
