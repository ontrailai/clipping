"""clipfactory — the GTA 6 clip factory.

  clipfactory init                 create config/settings.yaml, config/creators.yaml, .env
  clipfactory doctor               check tools, keys, creators and platform logins
  clipfactory tick                 do whatever is due (scout / produce / post) — run this on a schedule
  clipfactory daemon               run tick every few minutes forever
  clipfactory discover             scout now
  clipfactory produce [-n 3]       analyze + edit + QC until N new clips are ready
  clipfactory publish [--now]      post the due slot (or the next clip immediately)
  clipfactory status               queue, today's slots, recent posts
  clipfactory clip URL [URL...]    clip any YouTube / Twitch / Kick video right now (no posting)
  clipfactory gallery              review page: every clip, the judge's reasoning, what got thrown out
  clipfactory rate ID good|bad "note"   teach the judge your taste
  clipfactory rerender ID ...      re-trim / new hook / new caption preset, no re-analysis
  clipfactory styles ID            the same clip in every caption preset, side by side
  clipfactory render FILE ...      render a vertical clip from a local file (style test)
  clipfactory review | approve ID | reject ID
  clipfactory auth youtube|tiktok
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import log
from .config import PACKAGE_ROOT, load_config, project_home


def _producer(args):
    from .producer import Producer

    cfg = load_config(args.settings, args.creators)
    return Producer(cfg)


# ---------------------------------------------------------------------------- commands
def cmd_init(args) -> None:
    home = project_home()
    (home / "config").mkdir(exist_ok=True)
    for name, ext in (("settings", "yaml"), ("creators", "yaml"), ("style", "md")):
        dst = home / "config" / f"{name}.{ext}"
        if not dst.exists():
            shutil.copy(PACKAGE_ROOT / "config" / f"{name}.example.{ext}", dst)
            print(f"created {dst.relative_to(home)}")
    env = home / ".env"
    if not env.exists():
        shutil.copy(PACKAGE_ROOT / ".env.example", env)
        print("created .env — add your API keys")
    for d in ("data", "work", "out", "secrets"):
        (home / d).mkdir(exist_ok=True)
    print("next: fill in .env, edit config/creators.yaml, then run `clipfactory doctor`")


def cmd_doctor(args) -> None:
    from . import publisher
    from .db import DB
    from .scout import kick, twitch, youtube

    cfg = load_config(args.settings, args.creators)
    ok = lambda b: "✅" if b else "❌"  # noqa: E731
    print("— tools")
    for tool in ("ffmpeg", "ffprobe"):
        print(f"  {ok(shutil.which(tool))} {tool}")
    try:
        import faster_whisper  # noqa: F401
        print(f"  {ok(True)} faster-whisper")
    except ImportError:
        print(f"  {ok(False)} faster-whisper (pip install faster-whisper)")
    from .fetch import js_runtimes

    rts = js_runtimes()
    hint = ", ".join(rts) or "install deno: pip install 'yt-dlp[deno]'"
    print(f"  {ok(rts)} JavaScript runtime for YouTube ({hint})")
    print(f"  {ok(shutil.which('TwitchDownloaderCLI'))} TwitchDownloaderCLI (optional, full Twitch chat)")
    fonts = list(cfg.fonts_dir.glob("*.ttf"))
    print(f"  {ok(fonts)} caption fonts ({len(fonts)} in {cfg.fonts_dir})")

    print("— keys")
    print(f"  {ok(os.environ.get('ANTHROPIC_API_KEY'))} ANTHROPIC_API_KEY")
    tw = twitch.Twitch()
    print(f"  {ok(tw.configured)} TWITCH_CLIENT_ID / SECRET")

    print("— publishers")
    for pub in publisher.enabled(cfg):
        ready, why = pub.ready()
        print(f"  {ok(ready)} {pub.name} {why}")

    if args.offline:
        return
    print("— creators")
    db = DB(cfg.path("data") / "state.db")
    from . import fetch

    opts = fetch.ytdlp_opts(cfg)
    users = {}
    if tw.configured:
        try:
            users = tw.users([s for c in cfg.creators for s in c.source_ids("twitch")])
        except Exception as e:
            print(f"  ❌ twitch lookup failed: {e}")
    for c in cfg.creators:
        for s in c.sources:
            try:
                if s.platform == "youtube":
                    youtube.resolve_channel_id(db, s.id, opts)
                    good = True
                elif s.platform == "twitch":
                    good = s.id.lower() in users if tw.configured else None
                elif s.platform == "kick":
                    kick.videos(s.id)
                    good = True
                else:
                    good = False
            except Exception:
                good = False
            mark = "➖" if good is None else ok(good)
            print(f"  {mark} {c.name:<16} {s.platform:<8} {s.id}  [{c.permission}]")


def cmd_tick(args) -> None:
    summary = _producer(args).tick()
    print(json.dumps(summary))


def cmd_daemon(args) -> None:
    p = _producer(args)
    log.get("producer").info("daemon started — tick every %ss", args.interval)
    while True:
        try:
            p.tick()
        except Exception:
            log.get("producer").exception("tick crashed; continuing")
        time.sleep(args.interval)


def cmd_discover(args) -> None:
    from . import scout

    p = _producer(args)
    print(f"{scout.discover(p.cfg, p.db)} new sources")


def cmd_produce(args) -> None:
    print(f"{_producer(args).produce(args.n)} clips ready")


def cmd_publish(args) -> None:
    p = _producer(args)
    clip_id = p.publish_due(force=args.now)
    print(f"posted clip #{clip_id}" if clip_id else "nothing posted (no due slot or no approved clip)")


def cmd_status(args) -> None:
    from . import scheduler
    from .db import loads

    p = _producer(args)
    db, cfg = p.db, p.cfg
    counts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
    srcs = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM sources GROUP BY status")}
    print(f"sources: {srcs or '{}'}")
    print(f"clips:   {counts or '{}'}")
    now = datetime.now(timezone.utc)
    print(f"today ({cfg['timezone']}):")
    for key, at in scheduler.slots_for(cfg, now.astimezone(cfg.tz).date()):
        row = db.one("SELECT clip_id FROM slots WHERE slot_key = ?", [key])
        print(f"  {at:%H:%M}  {'posted clip #' + str(row['clip_id']) if row else 'open'}")
    print("up next:")
    for c in p.ready_clips()[:5]:
        copy = loads(c["copy"], {})
        print(f"  #{c['id']:<4} score {c['score']:<4.0f} {c['creator']:<14} {copy.get('youtube_title', '')}")
    print("recent posts:")
    for r in db.all("SELECT * FROM posts ORDER BY id DESC LIMIT 8"):
        print(f"  clip #{r['clip_id']:<4} {r['platform']:<10} {r['status']:<10} {r['url'] or r['error'] or ''}")


def cmd_clip(args) -> None:
    from . import analyst, gallery

    p = _producer(args)
    a = p.cfg.raw["analysis"]
    a["max_clips_per_source"] = max(a["max_clips_per_source"], args.n)
    if args.min_score is not None:
        a["min_virality_score"] = args.min_score
    for url in args.urls:
        sid = p.add_url(url, args.creator)
        source = p.db.one("SELECT * FROM sources WHERE id = ?", [sid])
        selected = analyst.analyze_source(p.cfg, p.db, p.claude, source)
        for mid in selected[: args.n]:
            p._render_and_finish(p.db.one("SELECT * FROM moments WHERE id = ?", [mid]))
        for c in p.db.all("SELECT c.* FROM clips c JOIN moments m ON m.id = c.moment_id WHERE m.source_id = ?", [sid]):
            print(f"#{c['id']} [{c['status']}] {c['path']}")
    page = gallery.build(p.db, p.cfg.path("out"))
    print(f"\nreview them: {page}")


def cmd_gallery(args) -> None:
    import webbrowser

    from . import gallery

    p = _producer(args)
    page = gallery.build(p.db, p.cfg.path("out"))
    print(page)
    if not args.no_open:
        webbrowser.open(page.as_uri())


def cmd_rate(args) -> None:
    p = _producer(args)
    if not p.db.clip(args.id):
        sys.exit(f"no clip #{args.id}")
    note = " ".join(args.note) or None
    p.db.rate(args.id, args.rating, note)
    print(f"clip #{args.id} rated {args.rating}" + (f": {note}" if note else "") + " — the judge will take this into account")


def cmd_rerender(args) -> None:
    from .db import loads
    from .editor import rerender

    p = _producer(args)
    clip = p.db.clip(args.id)
    if not clip:
        sys.exit(f"no clip #{args.id}")
    params = loads(clip.get("render"), {}) or {}
    changes = {"preset": args.preset, "hook": args.hook, "hook_emphasis": args.hook_emphasis, "layout": args.layout}
    if args.trim_start or args.trim_end:
        changes["start"] = round(max(0.0, params.get("start", 0) + args.trim_start), 2)
        changes["end"] = round(params.get("end", 0) + args.trim_end, 2)
    print(rerender(p.cfg, p.db, args.id, **changes))


def cmd_styles(args) -> None:
    from .editor import presets, rerender
    from .fetch import extract_frame, run_ffmpeg

    p = _producer(args)
    clip = p.db.clip(args.id)
    if not clip:
        sys.exit(f"no clip #{args.id}")
    out_dir = p.cfg.path("out") / "styles"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    names = list(presets.PRESETS)
    for name in names:
        video = rerender(p.cfg, p.db, args.id, out=out_dir / f"clip{args.id}_{name}.mp4", preset=name)
        frames.append(extract_frame(video, args.at if args.at is not None else (clip["duration"] or 10) * 0.4,
                                    out_dir / f"clip{args.id}_{name}.jpg", 360))
        print(f"{name:>7}: {video}")
    sheet = out_dir / f"clip{args.id}_compare.jpg"
    inputs = [x for f in frames for x in ("-i", str(f))]
    run_ffmpeg([*inputs, "-filter_complex", "".join(f"[{i}]" for i in range(len(frames))) + f"hstack=inputs={len(frames)}", str(sheet)])
    print(f"side by side ({' | '.join(names)}): {sheet}")
    print("pick one: set edit.preset in config/settings.yaml (or clipfactory rerender ID --preset NAME)")


def cmd_render(args) -> None:
    from .editor import render

    cfg = load_config(args.settings, args.creators)
    src = Path(args.file)
    words = []
    if args.words:
        words = json.loads(Path(args.words).read_text())
    elif not args.no_captions:
        from .analyst import transcript

        words = transcript.transcribe(src, cfg["analysis"]["whisper_model"], cfg["analysis"].get("language"))
    out = Path(args.out or src.with_name(src.stem + "_vertical.mp4"))
    box = json.loads(args.facecam) if args.facecam else None
    render(cfg, src, args.start, args.end, words, out, args.hook, args.credit, args.layout, box)
    print(out)


def cmd_review(args) -> None:
    from .db import loads

    p = _producer(args)
    for c in p.db.clips("pending_review"):
        copy = loads(c["copy"], {})
        print(f"#{c['id']} score {c['score']:.0f} {c['creator']} — {copy.get('youtube_title')}\n    {c['path']}")


def cmd_approve(args) -> None:
    _producer(args).db.update("clips", args.id, status="approved")
    print(f"clip #{args.id} approved")


def cmd_reject(args) -> None:
    _producer(args).db.update("clips", args.id, status="rejected")
    print(f"clip #{args.id} rejected")


def cmd_auth(args) -> None:
    cfg = load_config(args.settings, args.creators)
    if args.platform == "youtube":
        from .publisher.youtube import authorize
    else:
        from .publisher.tiktok import authorize
    print(f"saved {authorize(cfg)}")


# ---------------------------------------------------------------------------- entry
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="clipfactory", description="GTA 6 short-form clip factory")
    ap.add_argument("--settings", help="path to settings.yaml")
    ap.add_argument("--creators", help="path to creators.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    d = sub.add_parser("doctor")
    d.add_argument("--offline", action="store_true", help="skip network checks")
    d.set_defaults(fn=cmd_doctor)
    sub.add_parser("tick").set_defaults(fn=cmd_tick)
    dm = sub.add_parser("daemon")
    dm.add_argument("--interval", type=int, default=300)
    dm.set_defaults(fn=cmd_daemon)
    sub.add_parser("discover").set_defaults(fn=cmd_discover)
    pr = sub.add_parser("produce")
    pr.add_argument("-n", type=int, default=3)
    pr.set_defaults(fn=cmd_produce)
    pb = sub.add_parser("publish")
    pb.add_argument("--now", action="store_true", help="post the next approved clip immediately")
    pb.set_defaults(fn=cmd_publish)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    cl = sub.add_parser("clip", help="clip one or more video URLs now (no posting)")
    cl.add_argument("urls", nargs="+", metavar="URL")
    cl.add_argument("--creator")
    cl.add_argument("-n", type=int, default=3, help="max clips per video")
    cl.add_argument("--min-score", type=int, help="override analysis.min_virality_score for this run")
    cl.set_defaults(fn=cmd_clip)
    g = sub.add_parser("gallery", help="build and open the clip review page")
    g.add_argument("--no-open", action="store_true")
    g.set_defaults(fn=cmd_gallery)
    rt = sub.add_parser("rate", help="rate a clip good/bad with a note — steers future picks")
    rt.add_argument("id", type=int)
    rt.add_argument("rating", choices=["good", "bad"])
    rt.add_argument("note", nargs="*")
    rt.set_defaults(fn=cmd_rate)
    rr = sub.add_parser("rerender", help="re-render a clip with a new trim / hook / caption preset")
    rr.add_argument("id", type=int)
    rr.add_argument("--trim-start", type=float, default=0.0, help="seconds to move the start (+ later, - earlier)")
    rr.add_argument("--trim-end", type=float, default=0.0, help="seconds to move the end (+ later, - earlier)")
    rr.add_argument("--hook")
    rr.add_argument("--hook-emphasis", help="phrase in the hook to colour")
    rr.add_argument("--preset", help="punchy | boxed | loud | clean")
    rr.add_argument("--layout", choices=["blur_fill", "facecam_split", "center_crop"])
    rr.set_defaults(fn=cmd_rerender)
    st = sub.add_parser("styles", help="render one clip in every caption preset, side by side")
    st.add_argument("id", type=int)
    st.add_argument("--at", type=float, help="second to grab for the comparison image")
    st.set_defaults(fn=cmd_styles)
    r = sub.add_parser("render")
    r.add_argument("file")
    r.add_argument("--start", type=float, required=True)
    r.add_argument("--end", type=float, required=True)
    r.add_argument("--hook")
    r.add_argument("--credit")
    r.add_argument("--layout", default="blur_fill", choices=["blur_fill", "facecam_split", "center_crop"])
    r.add_argument("--facecam", help='JSON box, e.g. \'{"x":0,"y":0.7,"w":0.25,"h":0.3}\'')
    r.add_argument("--words", help="JSON word timestamps instead of running whisper")
    r.add_argument("--no-captions", action="store_true")
    r.add_argument("-o", "--out")
    r.set_defaults(fn=cmd_render)
    sub.add_parser("review").set_defaults(fn=cmd_review)
    a = sub.add_parser("approve")
    a.add_argument("id", type=int)
    a.set_defaults(fn=cmd_approve)
    rj = sub.add_parser("reject")
    rj.add_argument("id", type=int)
    rj.set_defaults(fn=cmd_reject)
    au = sub.add_parser("auth")
    au.add_argument("platform", choices=["youtube", "tiktok"])
    au.set_defaults(fn=cmd_auth)

    args = ap.parse_args(argv)
    import logging

    home = project_home()
    logfile = None
    if args.cmd in ("tick", "daemon", "produce", "publish", "discover", "clip"):
        (home / "data").mkdir(exist_ok=True)
        logfile = str(home / "data" / "factory.log")
    log.setup(logging.DEBUG if args.verbose else logging.INFO, logfile)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
