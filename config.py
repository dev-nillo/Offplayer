"""offline_playlist/config.py -- persistent settings, filesystem layout, dependency probe.

Python 3.11+ / Windows 11 / stdlib-first. No third-party import happens here so the
module can be imported by the bootstrap script and by the server before pip install.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Quando empacotado com PyInstaller, os assets (web/) ficam em _MEIPASS (somente leitura)
# e os dados mutaveis (config.json, data/) ficam ao lado do executavel.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = APP_DIR

DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "library.db"
CONFIG_PATH = APP_DIR / "config.json"
WEB_DIR = BUNDLE_DIR / "web"
LOG_DIR = DATA_DIR / "logs"

DEFAULTS: dict[str, Any] = {
    # where the downloaded audio lands
    "library_dir": str(Path.home() / "Music" / "Offplayer"),
    # mp3 | m4a | opus | source (source = keep the container yt-dlp gave us)
    "audio_format": "mp3",
    # 128 | 192 | 320
    "audio_quality": "192",
    # prefer YouTube auto-generated "- Topic" channels over re-uploads
    "prefer_topic": True,
    "host": "127.0.0.1",
    "port": 8722,
    "auto_open_browser": True,
    # seconds between progress writes to the database
    "progress_throttle": 0.6,
    # how many candidates to inspect per search
    "search_candidates": 8,
    # opcional: app do developers.spotify.com -> leitura completa da playlist (API oficial)
    "spotify_client_id": "",
    "spotify_client_secret": "",
}

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def sanitize_filename(name: str, fallback: str = "Sem título") -> str:
    """Make one filesystem-safe path component out of an arbitrary metadata string."""
    name = _INVALID_CHARS.sub("_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    if name.upper() in _RESERVED:
        name = f"_{name}"
    # Windows MAX_PATH safety: keep every component well below the limit
    return name[:120]


def load() -> dict[str, Any]:
    """Read config.json, merged over DEFAULTS. Missing/corrupt file falls back to defaults."""
    cfg = dict(DEFAULTS)
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        stored = json.loads(raw)
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in DEFAULTS and value is not None:
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        pass
    import os as _os
    env_library = _os.environ.get("OFFLINE_LIBRARY_DIR")
    if env_library and not CONFIG_PATH.exists():
        cfg["library_dir"] = env_library
    cfg["library_dir"] = str(Path(cfg["library_dir"]).expanduser())
    cfg["port"] = int(cfg["port"])
    cfg["audio_quality"] = str(cfg["audio_quality"])
    return cfg


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge `patch` into the stored config and persist it. Returns the effective config."""
    cfg = load()
    for key, value in (patch or {}).items():
        if key in DEFAULTS and value is not None:
            cfg[key] = value
    import os as _os
    env_library = _os.environ.get("OFFLINE_LIBRARY_DIR")
    if env_library and not CONFIG_PATH.exists():
        cfg["library_dir"] = env_library
    cfg["library_dir"] = str(Path(cfg["library_dir"]).expanduser())
    cfg["port"] = int(cfg["port"])
    ensure_dirs(cfg)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def ensure_dirs(cfg: dict[str, Any] | None = None) -> None:
    """Create every directory the app writes to."""
    cfg = cfg or load()
    for path in (DATA_DIR, LOG_DIR, WEB_DIR, Path(cfg["library_dir"]), DATA_DIR / "covers"):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def has_module(name: str) -> bool:
    """True when an importable module is present in the current interpreter."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def ffmpeg_path() -> str | None:
    """Absolute path to ffmpeg, or None. Also looks next to the interpreter (imageio-ffmpeg)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = APP_DIR / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if local.exists():
        return str(local)
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def dependency_report() -> dict[str, Any]:
    """Full health check surfaced in the UI so the user never wonders why nothing happens."""
    ff = ffmpeg_path()
    return {
        "python": sys.version.split()[0],
        "yt_dlp": has_module("yt_dlp"),
        "mutagen": has_module("mutagen"),
        "ffmpeg": bool(ff),
        "ffmpeg_path": ff,
        "yt_dlp_version": _module_version("yt_dlp"),
        "ready": has_module("yt_dlp") and bool(ff),
    }


def _module_version(name: str) -> str | None:
    """Versao do pacote como texto (nunca um objeto, para nao quebrar o JSON da API)."""
    try:
        mod = __import__(name)
        for attr in ("__version__", "version", "VERSION"):
            value = getattr(mod, attr, None)
            if isinstance(value, str):
                return value
            if value is not None and hasattr(value, "__version__"):
                inner = getattr(value, "__version__", None)
                if isinstance(inner, str):
                    return inner
    except Exception:
        return None
    return None


def pip_install(*packages: str) -> tuple[int, str]:
    """Install packages with the interpreter that runs the app. Returns (code, output)."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, "pip install timed out"
    except OSError as exc:
        return 1, f"pip failed: {exc}"
