# clipfactory — the GTA 6 clip factory

Every day it finds new GTA videos and streams from the biggest creators, picks the best moments
(funny, chaotic, clutch, betrayals, fails), cuts them into vertical clips with big animated captions
and a hook banner, writes captions and hashtags, checks them, and posts **3 clips a day** to TikTok,
Instagram Reels and YouTube Shorts. Hands-off once it's set up.

> GTA 6 launches **November 19, 2026**. Until then the factory clips GTA V / GTA RP (still a top-5
> category on Twitch and bigger on Kick) plus GTA 6 trailer and reaction streams, so the page is
> warmed up and the pipeline is proven before launch week. See [docs/PLAYBOOK.md](docs/PLAYBOOK.md).

## The crew

| | Role | What it does |
|---|---|---|
| 🔭 | **Scout** | Checks your creator list every few hours: YouTube uploads (RSS), Twitch VODs (Helix API), Kick VODs. Keeps only GTA content (game category or title keywords) and grabs the community clips viewers made. |
| 🧠 | **Analyst** | Scores every second of a video/VOD from 4 signals: **community clips**, YouTube's **most-replayed heatmap**, **Twitch chat spikes** (KEKW / OMEGALUL / "CLIP IT" walls), and **audio spikes** (yelling, laughing, explosions). Downloads only the top windows, transcribes them (Whisper), then **Claude** looks at frames + transcript + chat and keeps only real bangers, trimmed tight from setup to payoff. |
| ✂️ | **Editor** | Renders 1080×1920: facecam-on-top/gameplay-below when it finds a facecam (Claude locates it once per creator), otherwise blurred-background fill. Word-by-word pop captions with the spoken word highlighted, a white hook banner, creator credit, loudness normalised to −14 LUFS. |
| ✍️ | **Copywriter** | Claude writes the caption, the YouTube title, a comment-bait question and moment-specific hashtags. The code always adds the creator credit, your call to action and base hashtags, and enforces each platform's length limits. |
| 🛡️ | **QC** | Technical checks (resolution, length, audio not silent) plus a Claude review: accurate hook, no slurs/hate/sexual content, creator credited. Fails → not posted. |
| 📤 | **Publisher** | Posts at your 3 time slots to every enabled platform and always saves a ready-to-post folder (`out/ready/…`) as a manual fallback. |
| 🎬 | **Producer** | Runs the crew: `clipfactory tick` does whatever is due (scout → keep the queue stocked → post if a slot is due). |

```
 every 3h                 when the queue is low                                12:00 · 17:00 · 21:00
 ┌────────┐   new VODs   ┌─────────┐ moments ┌────────┐ clip ┌───────────┐ ┌────┐ approved ┌───────────┐
 │ Scout  │ ───────────▶ │ Analyst │ ──────▶ │ Editor │ ───▶ │Copywriter │▶│ QC │ ───────▶ │ Publisher │
 └────────┘              └─────────┘         └────────┘      └───────────┘ └────┘  queue   └───────────┘
```

## Quick start (your computer)

You need Python 3.10+ and ffmpeg (`brew install ffmpeg` · `sudo apt install ffmpeg` · `winget install ffmpeg`).

```bash
git clone https://github.com/ontrailai/clipping && cd clipping
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all]"
clipfactory init          # creates config/settings.yaml, config/creators.yaml, .env
```

1. Put your keys in `.env`: at minimum `ANTHROPIC_API_KEY`, plus `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` ([free app](https://dev.twitch.tv/console/apps)).
2. Edit `config/creators.yaml` (who to clip) and `config/settings.yaml` (timezone, post times, brand name, hashtags).
3. `clipfactory doctor` → everything green?
4. **Try it on one video:** `clipfactory clip "https://www.twitch.tv/videos/…"` → clips land in `out/clips/`.
5. Turn on platforms in `settings.yaml` (see below), then run it for real: `clipfactory daemon`.

Until a platform is enabled, every clip is still saved to `out/ready/<slot>_<creator>_<id>/`
(video, cover, captions), so you can post by hand while the API approvals come through.

## Connecting the platforms

| Platform | What you need | Gotchas |
|---|---|---|
| **Instagram Reels** | Instagram professional account + Meta app with `instagram_content_publish`; put `IG_USER_ID` + long-lived `IG_ACCESS_TOKEN` in `.env`. | Uploads the file directly (no hosting needed). ~50–100 API posts/day limit, so 3 is fine. Long-lived tokens last 60 days; refresh them. |
| **TikTok** | App on [developers.tiktok.com](https://developers.tiktok.com) with Content Posting API → `.env` keys → `clipfactory auth tiktok`. | Default `mode: inbox`: clips land in your TikTok drafts and you post each with one tap (add a trending sound while you're there). **Until TikTok audits your app, direct posts are private**, so switch to `mode: direct` + `audited: true` only after approval. |
| **YouTube Shorts** | Google Cloud project → YouTube Data API v3 → OAuth client (Desktop) → `secrets/youtube_client_secret.json` → `clipfactory auth youtube`. | Unverified API projects get uploads **locked to private** until Google's audit. Default quota allows ~6 uploads/day. |

Each platform is independent. A clip counts as posted when at least one social platform accepts it.
Failures are retried twice. An expired login never burns clips: they wait in the queue until you reconnect.

## Where to run it

- **Your computer (recommended to start):** `clipfactory daemon` in a terminal, or install it as a background
  service: `deploy/macos/…plist`, `deploy/linux/clipfactory.service`, `deploy/windows/run-tick.bat`.
  Residential internet = YouTube downloads just work. Keep the machine awake at post times.
- **A small VPS / home server (24/7):** `docker compose up -d` (state lives in `./factory`). A 4-core / 8 GB box is plenty.
  YouTube may ask datacenter IPs to "confirm you're not a bot". Set `download.cookies_from_browser` or `cookiefile`.
- **GitHub Actions (free, no server):** `.github/workflows/clip-factory.yml` runs `tick` every 30 min. It's off until
  you set the repo variable `CLIPFACTORY_ENABLED=true`. Read the header of that file first. Twitch/Kick work great there;
  YouTube downloads often get blocked from GitHub's IPs.

Plain cron works too: `*/15 * * * * cd ~/clipping && .venv/bin/clipfactory tick`. A lock file stops overlapping
ticks, so a long production run never double-posts.

## Commands

```
clipfactory tick               do whatever is due (run on a schedule)
clipfactory daemon             loop tick every 5 min
clipfactory status             queue, today's slots, recent posts
clipfactory clip URL           clip one YouTube/Twitch/Kick video now (no posting)
clipfactory produce -n 3       fill the queue now
clipfactory publish --now      post the next approved clip immediately
clipfactory review / approve ID / reject ID     (when qc.approval: manual)
clipfactory render FILE --start 12 --end 48 --hook "..."   style-test on a local file
clipfactory doctor             check tools, keys, creators, logins
```

## Tuning

Everything is in `config/settings.yaml`, commented. The knobs you'll actually touch:

- `clips_per_day`, `post_times`, `timezone`
- `analysis.min_virality_score` (default 65). Raise it for fewer, better clips.
- `analysis.target_clip_seconds` / `max_clip_seconds` (default ~35 / 58)
- `edit.*`: fonts, caption colours, words per caption chunk, hook duration, layout
- `brand.*`: page name, CTA, base hashtags
- `qc.approval: manual`: review every clip before it posts (`clipfactory review`). A good idea for week one.
- `discovery.require_permission: true`: only clip creators marked `granted` / `program` / `open`

## Cost

Claude (default `claude-opus-5-5`) runs about **$0.50–$1.50 per day** at 3 clips/day: the judge looks at roughly 8
windows per video with 4 frames each, plus small copy/QC calls. Whisper runs locally for free. The Twitch API is free.
Change `llm.model` to trade quality for cost.

## Creators, credit and permission

Clip pages live or die on staying in creators' good graces. Every clip credits the creator on screen and in the
caption. Many big streamers actively **pay** clippers (Whop Content Rewards and Discord clip programs).
Clipping those creators gets you both permission and per-view payouts. Mark them `permission: program` in
`creators.yaml` and consider `require_permission: true`. Instagram and TikTok also down-rank pure reposts: the
captions, hook and reframing are what make these clips transformative, so don't turn them off.

## Project layout

```
clipfactory/
  scout/       youtube.py · twitch.py · kick.py        discovery
  analyst/     signals.py · audio.py · chat.py         per-second highlight signals
               transcript.py · judge.py                Whisper + Claude moment selection
  editor/      layout.py · captions.py · facecam.py    ffmpeg 9:16 render, ASS captions, facecam finder
  copywriter.py · qc.py                                captions/hashtags, quality gate
  publisher/   local · youtube · instagram · tiktok
  scheduler.py · producer.py · cli.py · db.py          slots, orchestration, CLI, SQLite state
config/        settings.example.yaml · creators.example.yaml
assets/fonts/  Montserrat Black/ExtraBold, Anton (SIL Open Font License)
deploy/        launchd · systemd · Windows task
docs/          PLAYBOOK.md: research + launch strategy
```

Run the tests with `pip install -e ".[dev]" && pytest`.
