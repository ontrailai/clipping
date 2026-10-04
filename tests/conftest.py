import shutil
import subprocess

import pytest

from clipfactory.config import load_config
from clipfactory.db import DB

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFACTORY_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def cfg(home):
    return load_config()


@pytest.fixture
def db(cfg):
    return DB(cfg.path("data") / "state.db")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
         "-t", "40", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out


def fake_words(text: str, start: float = 1.0, step: float = 0.4):
    words, t = [], start
    for w in text.split():
        words.append({"w": w, "s": round(t, 2), "e": round(t + step * 0.8, 2)})
        t += step
    return words
