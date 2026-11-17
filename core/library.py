"""offline_playlist/core/library.py -- on-disk library layout and audio tagging.

Files land in  <library_dir>/<artist>/<album>/<artist> - <title>.<ext>
Tags are written with mutagen when available; without it a sidecar .json keeps the
metadata so the library UI stays truthful.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .. import config

try:  # pragma: no cover
    import mutagen
    from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TCON
    from mutagen.mp4 import MP4, MP4Cover
except Exception:  # pragma: no cover
    mutagen = None  # type: ignore
    ID3 = ID3NoHeaderError = TIT2 = TPE1 = TALB = TDRC = TCON = None  # type: ignore
    MP4 = MP4Cover = None  # type: ignore


def library_root(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or config.load()
    root = Path(cfg["library_dir"]).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def target_dir(cfg: dict[str, Any], artist: str | None, album: str | None) -> Path:
    root = library_root(cfg)
    artist_dir = root / config.sanitize_filename(artist or "Artistas Desconhecidos", "Desconhecidos")
    album_dir = artist_dir / config.sanitize_filename(album or "Singles", "Singles")
    album_dir.mkdir(parents=True, exist_ok=True)
    return album_dir


def final_path(cfg: dict[str, Any], artist: str | None, album: str | None, title: str, ext: str) -> Path:
    folder = target_dir(cfg, artist, album)
    stem = config.sanitize_filename(f"{artist or 'Desconhecido'} - {title}", "faixa")
    ext = (ext or "mp3").lower().lstrip(".")
    return folder / f"{stem}.{ext}"


def unique_path(path: Path) -> Path:
    """Append (2), (3) ... when a file with the same name already exists."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    index = 2
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def tag_file(path: Path, meta: dict[str, Any]) -> bool:
    """Write ID3/MP4 tags. Returns True when real tags were written."""
    if mutagen is None:
        write_sidecar(path, meta)
        return False
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            if meta.get("title"):
                tags.setall("TIT2", [TIT2(encoding=3, text=[meta["title"]])])
            if meta.get("artist"):
                tags.setall("TPE1", [TPE1(encoding=3, text=[meta["artist"]])])
            if meta.get("album"):
                tags.setall("TALB", [TALB(encoding=3, text=[meta["album"]])])
            if meta.get("year"):
                tags.setall("TDRC", [TDRC(encoding=3, text=[str(meta["year"])])])
            if meta.get("genre"):
                tags.setall("TCON", [TCON(encoding=3, text=[meta["genre"]])])
            tags.save(path, v2_version=3)
        elif suffix in {".m4a", ".mp4", ".aac"}:
            audio = MP4(path)
            if meta.get("title"):
                audio["\xa9nam"] = [meta["title"]]
            if meta.get("artist"):
                audio["\xa9ART"] = [meta["artist"]]
            if meta.get("album"):
                audio["\xa9alb"] = [meta["album"]]
            if meta.get("year"):
                audio["\xa9day"] = [str(meta["year"])]
            if meta.get("genre"):
                audio["\xa9gen"] = [meta["genre"]]
            audio.save()
        else:
            write_sidecar(path, meta)
            return False
    except Exception:
        write_sidecar(path, meta)
        return False
    write_sidecar(path, meta)
    return True


def write_sidecar(path: Path, meta: dict[str, Any]) -> None:
    """Mirror the metadata next to the file (used by the UI and as a mutagen fallback)."""
    try:
        sidecar = path.with_suffix(path.suffix + ".json")
        sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_sidecar(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.with_suffix(path.suffix + ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def move_into_place(source: Path, destination: Path) -> Path:
    """Move a freshly downloaded file to its final library path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.move(str(source), str(destination))
    return destination
