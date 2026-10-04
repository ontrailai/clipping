# GTA 6 Clip Page Playbook

Research behind the factory's defaults, plus the plan for launch. Compiled October 4, 2026.

## 1. The calendar

| Date | What happens | What the factory should be doing |
|---|---|---|
| **Now → Nov 11** | Pre-launch. GTA V roleplay is still a top-5 Twitch category (~70k concurrent, almost all RP), and NoPixel is in closed beta with big Twitch and Kick streamers. | Clip GTA V / GTA RP and GTA 6 trailer/reaction streams. Build followers, tune `min_virality_score`, check that each platform posts cleanly. Run `qc.approval: manual` for the first week. |
| **Nov 12** | GTA 6 digital pre-load. | Make sure `Grand Theft Auto VI` is in `games:` (it is by default) and add every big streamer who has announced a launch stream. |
| **Nov 19** | **GTA 6 launches** (PS5 / Xbox Series X\|S). | Peak demand. Consider `clips_per_day: 5–6` for launch week, `discovery.interval_hours: 1`, `lookback_hours: 24` (freshness matters most). |
| Launch +2 weeks | Novelty phase: first heists, first police chases, map discoveries, bugs, first RP servers. | Lean toward "first time" moments. The judge already rewards self-contained payoffs. |

## 2. Where the biggest GTA streams are

- **Kick overtook Twitch for GTA in 2026.** In July 2026 GTA V averaged 112.3k viewers on Kick vs 68.6k on Twitch
  (82.5M hours watched on Kick). The Scout watches Kick, Twitch and YouTube.
- Most-watched GTA creators in 2026 include **3mr** (Kick's #1 GTA V streamer in H1 2026), **xQc** (Kick, #2 by watch time),
  **abuswe7l**, **Buddha** (Kick) and **drb7h**, plus English-language staples like **summit1g**.
  Twitch's #1 GTA V streamer by hours is Loud_coringa (Portuguese).
- For an English-speaking page, prioritise English creators and set `analysis.language: en`. For a Spanish or Arabic
  page, change `language` and the creator list (the Whisper captions and Claude copy follow the language of the clip).
- The starter `config/creators.yaml` includes xQc, Kai Cenat, summit1g, Buddha, CaseOh, IShowSpeed, Typical Gamer and
  MrBossFTW. Handles are best-effort, so run `clipfactory doctor` to confirm each one.

## 3. What a winning clip looks like

Drawn from large-sample analyses of viral short-form in 2026:

- **The first second does most of the work.** The hook banner is on screen from frame one and the judge starts clips
  1–3 s before the action, never on dead air.
- **Captions are mandatory.** About 80% of viral-tier clips use captions and about 79% animate them, hence the
  word-by-word pop captions with the active word highlighted.
- **Short wins.** The viral median is about 41 s, 18% shorter than average. Defaults: target 35 s, cap 58 s (under
  60 s keeps every platform treating it as a Short or Reel).
- **8–12 hashtags.** The viral tier averages about 10. Defaults: 8–12, with 5 base tags (#gta6 #gta #gtavi #gaming
  #gtarp) plus moment- and creator-specific ones.
- **Credit the creator** on screen and in the caption. It's the norm on clip pages and helps keep good standing with creators.
- **Hook formula:** name + action + twist, e.g. "XQC STEALS A COP CAR AND IMMEDIATELY REGRETS IT". Accurate, never fake.
  The QC step rejects misleading hooks.

## 4. How the factory finds moments (and why it works)

Crowd signals are the cheapest, most reliable highlight detector there is:

1. **Community clips** (Twitch, Kick): viewers literally pressed "clip". Highest weight. Clip titles are passed to the
   judge too ("xQc gets betrayed" is great context).
2. **YouTube "most replayed" heatmap:** where viewers rewatched.
3. **Chat velocity:** chat is "the live-stream laugh track". Message rate plus reaction emotes (KEKW, OMEGALUL, Pog,
   "CLIP IT") relative to that stream's own baseline, shifted about 7 s earlier because chat reacts late.
4. **Audio spikes:** sudden loudness relative to the last few minutes (yelling, laughing, explosions).

The fused score picks around 8 windows per video. Only those windows are downloaded and transcribed. Claude then judges
each one with frames, transcript and chat sample, rejects most, and trims the keepers.

## 5. Posting realities (APIs)

| | Limit / rule | Implication |
|---|---|---|
| Instagram Graph API | Professional account; about 50–100 API posts per rolling 24 h | 3/day is far under the limit. Resumable upload means no public hosting is needed. |
| TikTok Content Posting API | Unaudited apps can only post **private** (SELF_ONLY); creator_info must be queried before each post | Apply for the audit right away. Meanwhile use `mode: inbox` (drafts, one tap to post). |
| YouTube Data API | 1,600 quota units per upload, 10k/day by default (about 6 uploads); unverified projects upload as private | 3/day fits. Request the audit to post publicly via the API. |

If audits drag on, a third-party poster with already-audited apps (e.g. Ayrshare, Postiz, Blotato) can stand in. The
`out/ready` folders are built to be handed to any of them.

## 6. Making money and staying safe

- **Paid clipping campaigns:** Whop Content Rewards pays clippers per 1,000 views ($0.20–$6, around $1 on average) on
  campaigns funded by streamers and brands. It reportedly pays out more than $40k/day, and Adin Ross and N3on alone
  spend close to $1M/month on clipper payouts. Join the campaigns of GTA streamers who run them, mark those creators
  `permission: program`, and the factory becomes a revenue engine with explicit permission.
- **Copyright and reach:** clipping without permission risks takedowns, and IG/TikTok down-rank unedited reposts. Keep
  the transformative layer on (captions, hook, framing, credit). Prefer creators who allow or pay for clips. Mute or
  skip clips flagged `copyrighted_music` (the judge flags them).
- **Account health:** don't run several pages from one account on day one, keep posting times consistent, and use
  `qc.approval: manual` until you trust the output.

## Sources

- Release date: [Recharge](https://www.recharge.com/blog/en-gb/gta-6-release-date-fall-2026-launch-price-platforms) · [Forbes](https://www.forbes.com/sites/brianmazique/2026/05/12/grand-theft-auto-6-release-date-and-everything-confirmed/) · [TechRadar](https://www.techradar.com/gaming/grand-theft-auto-6-delayed-again-but-itll-still-ship-in-2026) · [Tom's Guide](https://www.tomsguide.com/us/grand-theft-auto-6-release-date-rumors,news-25578.html)
- Streaming: [win.gg — how Kick overtook Twitch for GTA](https://win.gg/gta-livestreaming-viewership-trends-kick-twitch/) · [Streams Charts — Kick GTA V streamers](https://streamscharts.com/channels?game=grand-theft-auto-v-gta&platform=kick) · [Streams Charts — top GTA streamers 2025](https://streamscharts.com/news/top-gta-streamers-2025) · [esports.net](https://www.esports.net/streaming/most-watched-gta-streamers/) · [EarlyGame](https://earlygame.com/news/entertainment/gta-v-streaming-shakeup-kick-leaves-twitch-in-the-dust)
- Clip anatomy: [OpusClip — anatomy of a viral TikTok in 2026 (13.5M clips)](https://www.opus.pro/blog/anatomy-of-a-viral-tiktok-2026) · [ClipSpeed hook formulas](https://www.clipspeed.ai/blog/viral-hook-formulas-shorts-tiktok.html)
- Highlight detection: [Eklipse](https://eklipse.gg/help/how-does-eklipse-automatically-create-clips/) · [OpusClip — chat spikes](https://www.opus.pro/mcp/inspiration/clip-the-moments-your-live-chat-exploded) · [stream-clipper (open source)](https://github.com/nirvagold/stream-clipper)
- APIs: [Instagram content publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing) · [TikTok direct post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) · [TikTok content sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) · [YouTube quota costs](https://developers.google.cn/youtube/v3/determine_quota_cost)
- Clipping economy: [OpusClip on Whop Content Rewards](https://www.opus.pro/blog/whop-content-rewards) · [Whop docs](https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards)
