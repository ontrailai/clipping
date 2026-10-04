"""ffmpeg filtergraphs that turn a 16:9 stream into a 1080x1920 vertical frame."""

from __future__ import annotations

from pathlib import Path

W, H = 1080, 1920
CAM_H = 700  # facecam panel height in facecam_split


def _even(v: float) -> int:
    return int(round(v / 2) * 2)


def ff_escape(path: str | Path) -> str:
    """Quote a path for use as a filter option value.

    The quotes protect it from the filtergraph parser; the option parser then unescapes
    the \\: so Windows drive letters (C:/...) survive. Apostrophes can't be expressed
    inside the quotes, so keep project paths free of them.
    """
    p = str(Path(path).resolve()).replace("\\", "/")
    if "'" in p:
        raise ValueError(f"path contains an apostrophe, which ffmpeg filters can't take: {p}")
    return "'" + p.replace(":", r"\:") + "'"


def blur_fill(zoom: float = 1.18) -> str:
    fw = _even(W * max(1.0, zoom))
    return (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
        "boxblur=10:2,scale=1080:1920,eq=brightness=-0.10:saturation=1.15[bgb];"
        f"[fg]scale={fw}:-2,crop={W}:ih[fgc];"
        "[bgb][fgc]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
    )


def facecam_split(box: dict) -> str:
    x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    game_h = H - CAM_H
    ratio = W / game_h  # width/height of the gameplay crop
    return (
        "[0:v]split=2[c][g];"
        f"[c]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
        f"scale={W}:{CAM_H}:force_original_aspect_ratio=increase,crop={W}:{CAM_H}[cam];"
        f"[g]crop=ih*{ratio:.4f}:ih:(iw-ih*{ratio:.4f})/2:0,scale={W}:{game_h}[game];"
        "[cam][game]vstack=inputs=2,setsar=1[base]"
    )


def center_crop() -> str:
    return "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,setsar=1[base]"


def graph(layout: str, ass_path: Path, fonts_dir: Path, fps: int, zoom: float = 1.18, facecam: dict | None = None, lufs: float = -14) -> str:
    if layout == "facecam_split" and facecam:
        video = facecam_split(facecam)
    elif layout == "center_crop":
        video = center_crop()
    else:
        video = blur_fill(zoom)
    return (
        f"{video};"
        f"[base]ass=filename={ff_escape(ass_path)}:fontsdir={ff_escape(fonts_dir)},fps={fps},format=yuv420p[v];"
        f"[0:a]loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[a]"
    )
