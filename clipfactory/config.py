"""Settings + creator roster loading.

settings.yaml is deep-merged over settings.example.yaml, so a partial user file still
gets every default. Paths are resolved relative to the project home (CLIPFACTORY_HOME or cwd).
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def project_home() -> Path:
    return Path(os.environ.get("CLIPFACTORY_HOME", os.getcwd())).resolve()


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _example(name: str) -> Path:
    local = project_home() / "config" / name
    return local if local.exists() else PACKAGE_ROOT / "config" / name


@dataclass
class Source:
    platform: str  # youtube | twitch | kick
    id: str


@dataclass
class Creator:
    name: str
    sources: list[Source]
    priority: int = 2
    permission: str = "unverified"
    credit: str | None = None
    facecam: dict | None = None
    enabled: bool = True

    @property
    def credit_handle(self) -> str:
        return self.credit or self.name

    @property
    def permitted(self) -> bool:
        return self.permission in ("granted", "program", "open")

    def source_ids(self, platform: str) -> list[str]:
        return [s.id for s in self.sources if s.platform == platform]


@dataclass
class Config:
    raw: dict
    creators: list[Creator]
    home: Path = field(default_factory=project_home)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw["timezone"])

    def path(self, name: str) -> Path:
        p = Path(self.raw["paths"][name])
        p = p if p.is_absolute() else self.home / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def fonts_dir(self) -> Path:
        local = self.home / "assets" / "fonts"
        return local if local.exists() else PACKAGE_ROOT / "assets" / "fonts"

    @property
    def house_style(self) -> str:
        """config/style.md — plain-English taste rules fed to the judge and copywriter."""
        path = self.home / "config" / "style.md"
        if not path.exists():
            path = _example("style.example.md")
        if not path.exists():
            return ""
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("# ")]
        return "\n".join(lines).strip()

    def creator(self, name: str) -> Creator | None:
        for c in self.creators:
            if c.name.lower() == name.lower():
                return c
        return None

    def creator_for(self, platform: str, source_id: str) -> Creator | None:
        sid = source_id.lower().lstrip("@")
        for c in self.creators:
            for s in c.sources:
                if s.platform == platform and s.id.lower().lstrip("@") == sid:
                    return c
        return None

    @property
    def enabled_creators(self) -> list[Creator]:
        cs = [c for c in self.creators if c.enabled]
        if self.raw["discovery"].get("require_permission"):
            cs = [c for c in cs if c.permitted]
        return sorted(cs, key=lambda c: c.priority)


def parse_creators(data: dict) -> list[Creator]:
    creators = []
    for entry in (data or {}).get("creators", []) or []:
        sources = [Source(platform=s["platform"].lower(), id=str(s["id"])) for s in entry.get("sources", [])]
        creators.append(
            Creator(
                name=entry["name"],
                sources=sources,
                priority=int(entry.get("priority", 2)),
                permission=entry.get("permission", "unverified"),
                credit=entry.get("credit"),
                facecam=entry.get("facecam"),
                enabled=entry.get("enabled", True),
            )
        )
    return creators


def load_config(settings_path: str | Path | None = None, creators_path: str | Path | None = None) -> Config:
    home = project_home()
    load_dotenv(home / ".env")

    defaults = yaml.safe_load(_example("settings.example.yaml").read_text())
    sp = Path(settings_path) if settings_path else home / "config" / "settings.yaml"
    user = yaml.safe_load(sp.read_text()) if sp.exists() else {}
    raw = _deep_merge(defaults, user or {})

    cp = Path(creators_path) if creators_path else home / "config" / "creators.yaml"
    if not cp.exists():
        cp = _example("creators.example.yaml")
    creators = parse_creators(yaml.safe_load(cp.read_text()))
    return Config(raw=raw, creators=creators, home=home)
