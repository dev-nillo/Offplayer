"""offline_playlist/core/downloader.py -- job queue, source resolution and audio fetch.

Pipeline per track:
    resolve metadata -> search candidates -> score -> download best audio -> extract -> tag -> file

The resolver deliberately prefers YouTube "Artist - Topic" channels (auto-generated
official uploads) and falls back to duration/title similarity, so a wrong match is rare
and always re-pickable by hand through the API.
"""
from __future__ import annotations

import difflib
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import db, library, meta as meta_mod
from .. import config

try:  # pragma: no cover
    import yt_dlp
except Exception:  # pragma: no cover
    yt_dlp = None  # type: ignore


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "").lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"\[.*?\]", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _require_ytdlp() -> Any:
    if yt_dlp is None:
        raise meta_mod.MetadataError(
            "yt-dlp não está instalado. Rode:  python -m pip install -r requirements.txt"
        )
    return yt_dlp


class Downloader:
    """Single background worker draining a queue of download jobs."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._cancelled: set[str] = set()
        self._pending_pins: dict[str, tuple[str, str | None]] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="downloader", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ job control

    def enqueue_playlist(self, playlist_id: str, redownload: bool = False) -> str:
        """Queue every track of a playlist that is not downloaded yet."""
        if redownload:
            db.reset_tracks(playlist_id)
        tracks = [t for t in db.list_tracks(playlist_id) if t["status"] != "done"]
        job_id = db.create_job(playlist_id, len(tracks))
        self._queue.put(job_id)
        db.update_job(job_id, state="queued", message="na fila")
        return job_id

    def enqueue_single(self, track_id: str, youtube_id: str | None = None) -> str:
        """Queue one track; `youtube_id` pins a manually picked source."""
        track = db.get_track(track_id)
        if not track:
            raise KeyError("track não encontrada")
        job_id = db.create_job(track.get("playlist_id"), 1)
        self._queue.put(job_id)
        db.update_job(job_id, state="queued", message="na fila")
        self._pending_pins[job_id] = (track_id, youtube_id)
        return job_id

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancelled.add(job_id)
        db.update_job(job_id, state="cancelled", message="cancelado")

    def queue_depth(self) -> int:
        return self._queue.qsize()

    # ----------------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._process_job(job_id)
            except Exception as exc:  # keep the worker alive whatever happens
                db.update_job(job_id, state="failed", message=str(exc)[:400])
            finally:
                self._queue.task_done()

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def _process_job(self, job_id: str) -> None:
        job = db.get_job(job_id)
        if not job:
            return
        playlist_id = job["playlist_id"]
        tracks = db.list_tracks(playlist_id)
        pin = self._pending_pins.pop(job_id, None)
        if pin:
            tracks = [t for t in tracks if t["id"] == pin[0]]
        pending = [t for t in tracks if t["status"] != "done"] if not pin else tracks
        total = len(pending)
        db.update_job(job_id, state="running", total=total, completed=0, failed=0, message="baixando")
        completed = 0
        failed = 0
        for track in pending:
            if self._is_cancelled(job_id):
                db.update_job(job_id, state="cancelled", message="cancelado")
                return
            pinned_youtube_id = pin[1] if pin else None
            ok, message = self._process_track(track, pinned_youtube_id)
            if ok:
                completed += 1
            else:
                failed += 1
            db.update_job(job_id, completed=completed, failed=failed, total=total)
        final_state = "failed" if failed and not completed else "done"
        db.update_job(
            job_id,
            state=final_state,
            message=f"{completed} baixadas, {failed} com erro" if failed else f"{completed} baixadas",
        )

    # ------------------------------------------------------------------ track level

    def _process_track(self, track: dict[str, Any], pinned_youtube_id: str | None = None) -> tuple[bool, str]:
        cfg = config.load()
        track_id = track["id"]
        db.update_track(track_id, status="downloading", progress=0.0, error=None)

        details = meta_mod.track_details(track.get("source_url"), track["title"], track.get("artist"))
        artist = details.get("artist") or track.get("artist")
        title = details.get("title") or track["title"]
        album = details.get("album") or track.get("album") or "Singles"
        duration = details.get("duration") or track.get("duration")

        try:
            source = (
                {"id": pinned_youtube_id, "url": f"https://www.youtube.com/watch?v={pinned_youtube_id}", "channel": "manual"}
                if pinned_youtube_id
                else self._resolve_source(
                    artist,
                    title,
                    duration,
                    prefer_topic=bool(cfg.get("prefer_topic", True)),
                    candidates=int(cfg.get("search_candidates", 8)),
                )
            )
        except Exception as exc:
            db.update_track(track_id, status="error", error=f"busca falhou: {exc}"[:400])
            return False, str(exc)

        db.update_track(
            track_id,
            artist=artist,
            album=album,
            duration=duration,
            source_url=source["url"],
            channel=source.get("channel"),
            youtube_id=source.get("id"),
        )

        try:
            produced = self._fetch_audio(track_id, source, cfg, artist, album, title)
        except Exception as exc:
            db.update_track(track_id, status="error", error=str(exc)[:400])
            return False, str(exc)

        dest = library.final_path(cfg, artist, album, title, produced.suffix.lstrip("."))
        dest = library.unique_path(dest)
        library.move_into_place(produced, dest)
        library.tag_file(
            dest,
            {
                "title": title,
                "artist": artist,
                "album": album,
                "year": details.get("year"),
                "genre": None,
                "source": source["url"],
                "downloaded_at": time.time(),
            },
        )
        db.update_track(
            track_id,
            status="done",
            progress=1.0,
            file_path=str(dest),
            error=None,
        )
        return True, ""

    def _resolve_source(
        self,
        artist: str | None,
        title: str,
        duration: float | None,
        prefer_topic: bool = True,
        candidates: int = 8,
    ) -> dict[str, Any]:
        """Search, score and pick the best audio source for a track."""
        ydl_mod = _require_ytdlp()
        query = f"{artist} - {title}" if artist else title
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "extract_flat": True,
            "socket_timeout": 30,
            "retries": 5,
            "http_headers": {"User-Agent": USER_AGENT},
        }
        with ydl_mod.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max(1, candidates)}:{query}", download=False)
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError(f"nenhum resultado para '{query}'")

        best = None
        best_score = -1.0
        for entry in entries:
            score = self._score(entry, title, artist, duration, prefer_topic)
            if score > best_score:
                best, best_score = entry, score
        assert best is not None
        ident = best.get("id")
        if not ident:
            raise RuntimeError("resultado sem id")
        return {
            "id": ident,
            "url": best.get("webpage_url") or f"https://www.youtube.com/watch?v={ident}",
            "channel": best.get("channel") or best.get("uploader") or "",
            "score": round(best_score, 3),
        }

    @staticmethod
    def _score(
        entry: dict[str, Any],
        title: str,
        artist: str | None,
        duration: float | None,
        prefer_topic: bool,
    ) -> float:
        entry_title = _norm(entry.get("title"))
        want_title = _norm(title)
        score = difflib.SequenceMatcher(None, want_title, entry_title).ratio() * 2.0

        channel = (entry.get("channel") or entry.get("uploader") or "").strip()
        channel_norm = _norm(channel)
        if channel_norm.endswith("topic"):
            score += 1.2 if prefer_topic else 0.4
        if artist:
            artist_norm = _norm(artist)
            if artist_norm and (artist_norm in channel_norm or artist_norm in entry_title):
                score += 0.8

        length = entry.get("duration")
        if duration and length:
            delta = abs(float(length) - float(duration)) / max(float(duration), 1.0)
            score += max(0.0, 1.0 - delta * 3.0)
        elif duration and not length:
            score -= 0.5

        lowered = (entry.get("title") or "").lower()
        for noise in ("lyrics", "lyric video", "visualizer", "official video", "music video",
                      "remix", "cover band", "karaoke", "reaction", "sped up", "slowed"):
            if noise in lowered:
                score -= 0.3
        if entry.get("is_live"):
            score -= 1.0
        if channel_norm.endswith("topic"):
            score += 0.5
        return score

    def _fetch_audio(
        self,
        track_id: str,
        source: dict[str, Any],
        cfg: dict[str, Any],
        artist: str | None,
        album: str | None,
        title: str,
    ) -> Path:
        """Download the audio stream and extract it to the configured codec."""
        ydl_mod = _require_ytdlp()
        tmp_dir = Path(tempfile.mkdtemp(prefix="offline_dl_"))
        last_write = 0.0
        throttle = float(cfg.get("progress_throttle", 0.6))
        wanted_format = str(cfg.get("audio_format", "mp3")).lower()
        quality = str(cfg.get("audio_quality", "192")).lower()
        ffmpeg = config.ffmpeg_path()

        def progress_hook(data: dict[str, Any]) -> None:
            nonlocal last_write
            if data.get("status") != "downloading":
                return
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            ratio = (done / total) if total else 0.0
            now = time.time()
            if now - last_write >= throttle:
                last_write = now
                db.update_track(track_id, progress=round(min(max(ratio, 0.0), 0.999), 4))

        opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": str(tmp_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "socket_timeout": 30,
            "http_headers": {"User-Agent": USER_AGENT},
            "progress_hooks": [progress_hook],
            "overwrites": True,
        }
        # "best"/"source": sem re-encode, mantem o container original (maior fidelidade)
        if wanted_format in ("source", "best") or not ffmpeg:
            opts["format"] = "bestaudio/best"
        else:
            # qualidade "best" vira VBR maximo (0 = VBR mais alto no ffmpeg)
            preferred = "0" if quality == "best" else quality
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": wanted_format,
                    "preferredquality": preferred,
                }
            ]
            opts["ffmpeg_location"] = ffmpeg

        try:
            with ydl_mod.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source["url"], download=True)
                if info is None:
                    raise RuntimeError("download retornou vazio")
                requested = info.get("requested_downloads") or []
                if requested and requested[0].get("filepath"):
                    produced = Path(requested[0]["filepath"])
                else:
                    produced = Path(info.get("filepath") or "")
                if not produced.exists():
                    files = [p for p in tmp_dir.iterdir() if p.is_file() and p.suffix != ".part"]
                    if not files:
                        raise RuntimeError("nenhum arquivo de áudio gerado")
                    produced = max(files, key=lambda p: p.stat().st_size)
        finally:
            db.update_track(track_id, progress=0.97)

        if not produced.exists():
            raise RuntimeError("arquivo de áudio ausente após o download")
        return produced

    # ------------------------------------------------------------------- utilities

    def cleanup_temp(self) -> None:
        tmp_root = Path(tempfile.gettempdir())
        for child in tmp_root.glob("offline_dl_*"):
            shutil.rmtree(child, ignore_errors=True)


_instance: Downloader | None = None
_instance_lock = threading.Lock()


def instance() -> Downloader:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Downloader()
    return _instance

