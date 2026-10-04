from clipfactory import scout
from clipfactory.scout import kick, twitch, youtube

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <yt:videoId>abc123def45</yt:videoId>
  <title>I Spent 24 Hours In GTA 6</title>
  <published>2026-11-20T15:00:00+00:00</published>
  <media:group><media:community><media:statistics views="123456"/></media:community></media:group>
 </entry>
 <entry>
  <yt:videoId>zzz</yt:videoId>
  <title>Minecraft but...</title>
  <published>2026-11-20T10:00:00+00:00</published>
 </entry>
</feed>"""


def test_parse_feed():
    entries = youtube.parse_feed(FEED)
    assert entries[0].video_id == "abc123def45"
    assert entries[0].views == 123456
    assert entries[0].published.year == 2026
    assert entries[1].views is None


def test_keyword_and_category_matching():
    kw = ["gta", "gta 6", "nopixel"]
    assert scout.title_matches("I Spent 24 Hours In GTA 6", kw)
    assert scout.title_matches("NoPixel 4.0 day 3", kw)
    assert not scout.title_matches("Minecraft but gtamazing", kw)
    games = ["Grand Theft Auto VI", "Grand Theft Auto V"]
    assert scout.is_gta_category("Grand Theft Auto V", games)
    assert scout.is_gta_category("GTA VI", games)
    assert not scout.is_gta_category("Just Chatting", games)


def test_twitch_duration():
    assert twitch.parse_duration("3h2m1s") == 10921
    assert twitch.parse_duration("45m") == 2700
    assert twitch.parse_duration("") == 0


def test_kick_clip_offset():
    vod = {"start_time": "2026-11-19 20:00:00"}
    assert kick.clip_offset({"started_at": "2026-11-19T21:30:00Z"}, vod) == 5400
    assert kick.clip_offset({"created_at": "2026-11-19T20:10:30Z", "duration": 30}, vod) == 600
    assert kick.clip_offset({}, vod) is None
