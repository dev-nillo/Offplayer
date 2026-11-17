"""offline_playlist/server.py -- local HTTP server: JSON API + static UI + range streaming.

Run:  python run.py   ->  http://127.0.0.1:8722
Only stdlib is used for the server itself; yt-dlp/mutagen are needed for downloading.
"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import shutil
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import config
from .core import db, downloader, library, meta as meta_mod

WEB_DIR = config.WEB_DIR


class Handler(BaseHTTPRequestHandler):
    server_version = "Offplayer/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ plumbing

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console clean
        if os.environ.get("OFFLINE_VERBOSE"):
            super().log_message(fmt, *args)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _serve_static(self, relative: str) -> None:
        rel = posixpath.normpath(unquote(relative)).lstrip("/")
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._send_error_json("caminho inválido", HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self._send_error_json("não encontrado", HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(target.name)
        self._send_file(target, mime or "application/octet-stream")

    def _send_file(self, path: Path, mime: str) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._send_error_json("arquivo indisponível", HTTPStatus.NOT_FOUND)
            return

        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if range_header and range_header.startswith("bytes="):
            spec = range_header.split("=", 1)[1].split(",")[0].strip()
            try:
                if spec.startswith("-"):
                    length = int(spec[1:])
                    if length <= 0:
                        raise ValueError
                    start = max(0, size - length)
                else:
                    first, _, last = spec.partition("-")
                    start = int(first) if first else 0
                    end = int(last) if last else size - 1
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # -------------------------------------------------------------------- routing

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in ("/", "/index.html"):
                self._serve_static("index.html")
                return
            if route.startswith("/static/"):
                self._serve_static(route[len("/static/") :])
                return
            if route in ("/manifest.json", "/sw.js"):
                self._serve_static(route.lstrip("/"))
                return
            if route == "/api/state":
                self._send_json(self._state())
                return
            if route.startswith("/api/playlist/"):
                playlist_id = route.split("/")[3] if len(route.split("/")) > 3 else ""
                playlist = db.get_playlist(playlist_id)
                if not playlist:
                    self._send_error_json("playlist não encontrada", HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"ok": True, "playlist": playlist, "tracks": db.list_tracks(playlist_id)})
                return
            if route.startswith("/api/track/"):
                parts = route.split("/")
                if len(parts) >= 5 and parts[4] == "file":
                    self._serve_track_file(parts[3])
                    return
                if len(parts) >= 5 and parts[4] == "cover":
                    self._serve_track_cover(parts[3])
                    return
            if route.startswith("/api/cover/"):
                self._serve_cover(route.split("/")[3])
                return
            if route == "/api/jobs":
                self._send_json({"ok": True, "jobs": db.list_jobs(30), "queued": downloader.instance().queue_depth()})
                return
            self._send_error_json("rota desconhecida", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error_json(f"{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        payload = self._read_json()
        try:
            if route == "/api/import":
                self._import_playlist(payload)
                return
            if route.startswith("/api/playlist/") and route.endswith("/download"):
                playlist_id = route.split("/")[3]
                job_id = downloader.instance().enqueue_playlist(
                    playlist_id, redownload=bool(payload.get("redownload"))
                )
                self._send_json({"ok": True, "job": db.get_job(job_id)})
                return
            if route.startswith("/api/playlist/") and route.endswith("/cancel"):
                job_id = payload.get("job_id") or ""
                downloader.instance().cancel(job_id)
                self._send_json({"ok": True})
                return
            if route.startswith("/api/track/") and route.endswith("/download"):
                track_id = route.split("/")[3]
                job_id = downloader.instance().enqueue_single(track_id, payload.get("youtube_id"))
                self._send_json({"ok": True, "job": db.get_job(job_id)})
                return
            if route == "/api/settings":
                cfg = config.save(payload.get("settings") or {})
                self._send_json({"ok": True, "config": cfg})
                return
            if route == "/api/shutdown":
                self._send_json({"ok": True})
                threading.Thread(target=self._shutdown, daemon=True).start()
                return
            self._send_error_json("rota desconhecida", HTTPStatus.NOT_FOUND)
        except meta_mod.MetadataError as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            self._send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route.startswith("/api/playlist/"):
                playlist_id = route.split("/")[3]
                db.delete_playlist(playlist_id)
                self._send_json({"ok": True})
                return
            if route.startswith("/api/track/"):
                track_id = route.split("/")[3]
                track = db.get_track(track_id)
                if track and track.get("file_path"):
                    path = Path(track["file_path"])
                    try:
                        if path.exists():
                            path.unlink()
                    except OSError:
                        pass
                db.delete_track(track_id)
                self._send_json({"ok": True})
                return
            self._send_error_json("rota desconhecida", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    # ------------------------------------------------------------------- handlers

    def _state(self) -> dict[str, Any]:
        cfg = config.load()
        return {
            "ok": True,
            "config": cfg,
            "deps": config.dependency_report(),
            "playlists": db.list_playlists(),
            "tracks": db.list_tracks(),
            "jobs": db.list_jobs(12),
            "stats": db.stats(),
            "queued": downloader.instance().queue_depth(),
            "server_time": time.time(),
        }

    def _import_playlist(self, payload: dict[str, Any]) -> None:
        url = (payload.get("url") or "").strip()
        if not url:
            self._send_error_json("informe o link da playlist", HTTPStatus.BAD_REQUEST)
            return
        cfg = config.load()
        resolved = meta_mod.resolve_playlist(url, cfg)
        db.upsert_playlist(
            resolved["id"],
            resolved["title"],
            resolved.get("artist"),
            resolved["source_url"],
            resolved.get("cover_path"),
        )
        db.replace_tracks(resolved["id"], resolved["tracks"])
        job_id = None
        if payload.get("auto_download", True):
            job_id = downloader.instance().enqueue_playlist(resolved["id"])
        self._send_json(
            {
                "ok": True,
                "playlist": db.get_playlist(resolved["id"]),
                "tracks": db.list_tracks(resolved["id"]),
                "job": db.get_job(job_id) if job_id else None,
                "resolver": resolved.get("resolver"),
                "truncated": resolved.get("truncated", False),
            }
        )

    def _serve_track_file(self, track_id: str) -> None:
        track = db.get_track(track_id)
        if not track or not track.get("file_path"):
            self._send_error_json("faixa não baixada", HTTPStatus.NOT_FOUND)
            return
        path = Path(track["file_path"])
        if not path.exists():
            self._send_error_json("arquivo sumiu do disco", HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(path.name)
        self._send_file(path, mime or "audio/mpeg")

    def _serve_track_cover(self, track_id: str) -> None:
        track = db.get_track(track_id)
        if not track or not track.get("cover_path"):
            self._send_error_json("sem capa", HTTPStatus.NOT_FOUND)
            return
        path = Path(track["cover_path"])
        if not path.exists():
            self._send_error_json("sem capa", HTTPStatus.NOT_FOUND)
            return
        self._send_file(path, "image/jpeg")

    def _serve_cover(self, playlist_id: str) -> None:
        playlist = db.get_playlist(playlist_id)
        if not playlist or not playlist.get("cover_path"):
            self._send_error_json("sem capa", HTTPStatus.NOT_FOUND)
            return
        path = Path(playlist["cover_path"])
        if not path.exists():
            self._send_error_json("sem capa", HTTPStatus.NOT_FOUND)
            return
        self._send_file(path, "image/jpeg")

    def _shutdown(self) -> None:
        time.sleep(0.3)
        os._exit(0)


def serve(host: str | None = None, port: int | None = None, open_browser: bool | None = None) -> None:
    """Start the local server and block."""
    cfg = config.load()
    config.ensure_dirs(cfg)
    db.init()
    # trabalhos interrompidos por um reinicio anterior nao terminam sozinhos
    with db.connect() as conn:
        conn.execute(
            "UPDATE tracks SET status='pending', progress=0 WHERE status='downloading'"
        )
        conn.execute(
            "UPDATE jobs SET state='failed', message='servidor reiniciado' "
            "WHERE state IN ('running','queued')"
        )
    host = host or cfg["host"]
    port = int(port or cfg["port"])
    should_open = cfg.get("auto_open_browser", True) if open_browser is None else open_browser

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"

    deps = config.dependency_report()
    print(f"Offplayer em {url}")
    print(f"  biblioteca: {cfg['library_dir']}")
    if deps["ready"]:
        print(f"  yt-dlp {deps['yt_dlp_version']} + ffmpeg ok")
    else:
        missing = []
        if not deps["yt_dlp"]:
            missing.append("yt-dlp")
        if not deps["ffmpeg"]:
            missing.append("ffmpeg")
        print(f"  FALTANDO: {', '.join(missing)} -- rode: python bootstrap.py")
    print("  ctrl+c para encerrar")

    if should_open:
        threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando")
    finally:
        httpd.server_close()
        shutil.rmtree(Path(config.DATA_DIR) / "tmp", ignore_errors=True)
