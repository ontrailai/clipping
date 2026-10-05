"""Review page: every clip with its video, hook, captions, the judge's reasoning, and what got
thrown out — so tuning is "watch, rate, tweak" instead of digging through folders.

Writes out/gallery.html (a plain local file; open it in any browser)."""

from __future__ import annotations

import html
import os
from pathlib import Path

from .db import DB, loads


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def source_link(source: dict, seconds: float) -> str:
    t = max(0, int(seconds))
    url = source["url"]
    if source["platform"] == "youtube":
        return f"{url}&t={t}s" if "?" in url else f"{url}?t={t}s"
    if source["platform"] == "twitch":
        return f"{url}?t={t // 3600}h{t % 3600 // 60}m{t % 60}s"
    if source["platform"] == "kick":
        return f"{url}?t={t}"
    return url


CSS = """
:root { --bg:#0e0f12; --card:#17191e; --line:#262a31; --text:#e9ebef; --muted:#9aa1ad; --good:#3ad07a; --bad:#ff5a5f; --accent:#ffd60a; }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
header { padding:20px 24px 8px; } h1 { margin:0 0 4px; font-size:22px; } .sub { color:var(--muted); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:16px; padding:16px 24px 32px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; display:flex; flex-direction:column; }
video { width:100%; aspect-ratio:9/16; background:#000; display:block; }
.body { padding:12px 14px 14px; display:flex; flex-direction:column; gap:8px; }
.meta { display:flex; flex-wrap:wrap; gap:6px; } .pill { border:1px solid var(--line); border-radius:999px; padding:1px 8px; color:var(--muted); font-size:12px; }
.score { color:#000; background:var(--accent); border-color:var(--accent); font-weight:700; }
.good { color:var(--good); border-color:var(--good); } .bad { color:var(--bad); border-color:var(--bad); }
.hook { font-weight:800; font-size:15px; } .why { color:var(--muted); } a { color:#7cc4ff; }
details { color:var(--muted); } details pre { white-space:pre-wrap; font:12px/1.4 ui-monospace,Menlo,monospace; color:var(--text); }
code { font:12px ui-monospace,Menlo,monospace; background:#0b0c0f; border:1px solid var(--line); border-radius:6px; padding:2px 6px; display:block; margin-top:4px; overflow-x:auto; white-space:nowrap; }
section { padding:0 24px 32px; } table { width:100%; border-collapse:collapse; } td,th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
"""


def build(db: DB, out_dir: Path, limit: int = 60) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "gallery.html"
    clips = db.all("SELECT * FROM clips ORDER BY id DESC LIMIT ?", [limit])
    ratings = {r["clip_id"]: r for r in db.all("SELECT * FROM feedback")}
    cards = []
    for c in clips:
        m = db.one("SELECT * FROM moments WHERE id = ?", [c["moment_id"]]) or {}
        s = db.one("SELECT * FROM sources WHERE id = ?", [m.get("source_id")]) or {}
        copy = loads(c.get("copy"), {}) or {}
        qc = loads(c.get("qc"), {}) or {}
        render = loads(c.get("render"), {}) or {}
        video = Path(c["path"])
        rel = os.path.relpath(video, out_dir).replace(os.sep, "/") if video.exists() else ""
        poster = os.path.relpath(c["cover_path"], out_dir).replace(os.sep, "/") if c.get("cover_path") and Path(c["cover_path"]).exists() else ""
        start_global = (m.get("local_offset") or 0) + (m.get("clip_start") or 0)
        rating = ratings.get(c["id"])
        rating_pill = f'<span class="pill {"good" if rating["rating"] == "good" else "bad"}">{"👍" if rating["rating"] == "good" else "👎"} {_esc(rating.get("note") or "")}</span>' if rating else ""
        issues = "; ".join(qc.get("issues") or [])
        cards.append(f"""
<div class="card">
  {f'<video controls preload="metadata" src="{_esc(rel)}" poster="{_esc(poster)}"></video>' if rel else '<div style="aspect-ratio:9/16;display:grid;place-items:center;color:#666">file cleaned up</div>'}
  <div class="body">
    <div class="meta"><span class="pill score">{c.get("score") or 0:.0f}</span><span class="pill">#{c["id"]}</span>
      <span class="pill">{_esc(m.get("category"))}</span><span class="pill">{(c.get("duration") or 0):.0f}s</span>
      <span class="pill">{_esc(c.get("status"))}</span><span class="pill">{_esc(render.get("preset", ""))}</span>{rating_pill}</div>
    <div class="hook">{_esc(m.get("hook"))}</div>
    <div>{_esc(s.get("creator"))} · <a href="{_esc(source_link(s, start_global))}" target="_blank" rel="noopener">source @ {int(start_global) // 60}:{int(start_global) % 60:02d}</a></div>
    <div class="why">{_esc(m.get("reason"))}</div>
    {f'<div class="why" style="color:var(--bad)">QC: {_esc(issues)}</div>' if issues else ''}
    <details><summary>caption &amp; title</summary><pre>{_esc(copy.get("youtube_title"))}\n\n{_esc(copy.get("instagram_caption"))}</pre></details>
    <details><summary>rate / tweak</summary>
      <code>clipfactory rate {c["id"]} good "what you liked"</code>
      <code>clipfactory rate {c["id"]} bad "what was wrong"</code>
      <code>clipfactory rerender {c["id"]} --trim-start 1.5 --trim-end -2</code>
      <code>clipfactory rerender {c["id"]} --hook "NEW HOOK TEXT" --preset boxed</code>
      <code>clipfactory styles {c["id"]}</code>
    </details>
  </div>
</div>""")

    rejected = db.all(
        "SELECT m.*, s.creator, s.title AS source_title FROM moments m JOIN sources s ON s.id = m.source_id"
        " WHERE m.status = 'rejected' ORDER BY m.id DESC LIMIT 40"
    )
    rows = "".join(
        f"<tr><td>{r.get('score') if r.get('score') is not None else '–'}</td><td>{_esc(r['creator'])}</td>"
        f"<td>{_esc(r.get('title') or '')}</td><td>{_esc(r.get('reason') or '')}</td><td>{_esc(r.get('error') or '')}</td></tr>"
        for r in rejected
    )
    n_good = sum(1 for r in ratings.values() if r["rating"] == "good")
    page.write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Clip Review</title><style>{CSS}</style></head><body>
<header><h1>Clip review</h1><div class="sub">{len(clips)} clips · {len(ratings)} rated ({n_good} 👍) · newest first.
Rate clips with the commands under each card: ratings and config/style.md steer what Claude picks next.</div></header>
<div class="grid">{''.join(cards) or '<p class="sub">No clips yet — run <b>clipfactory clip URL</b>.</p>'}</div>
<section><h2>Thrown out by the judge</h2><p class="sub">Tune analysis.min_virality_score or config/style.md if good moments land here.</p>
<table><tr><th>score</th><th>creator</th><th>moment</th><th>why</th><th>flags</th></tr>{rows}</table></section>
</body></html>""", encoding="utf-8")
    return page
