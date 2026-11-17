"""offline_playlist/core/db.py -- SQLite persistence for playlists, tracks and jobs.

One connection per call (SQLite is cheap to open); WAL mode so the HTTP threads and the
download worker never block each other.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    artist       TEXT,
    source_url   TEXT,
    cover_path   TEXT,
    track_count  INTEGER DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id           TEXT PRIMARY KEY,
    playlist_id  TEXT,
    pos          INTEGER DEFAULT 0,
    external_id  TEXT,
    title        TEXT NOT NULL,
    artist       TEXT,
    album        TEXT,
    duration     REAL,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending|downloading|done|error
    progress     REAL DEFAULT 0,
    error        TEXT,
    file_path    TEXT,
    cover_path   TEXT,
    source_url   TEXT,
    channel      TEXT,
    youtube_id   TEXT,
    added_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    playlist_id  TEXT,
    state        TEXT NOT NULL DEFAULT 'queued',    -- queued|running|done|failed|cancelled
    total        INTEGER DEFAULT 0,
    completed    INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    message      TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracks_playlist ON tracks(playlist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_status   ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_jobs_playlist   ON jobs(playlist_id);
"""


def connect() -> sqlite3.Connection:
    """Open a short-lived connection with row access by name."""
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init() -> None:
    """Create the schema if it is missing."""
    with connect() as conn:
        conn.executescript(SCHEMA)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- playlists

def upsert_playlist(
    playlist_id: str,
    title: str,
    artist: str | None,
    source_url: str,
    cover_path: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO playlists (id, title, artist, source_url, cover_path, track_count, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, artist=excluded.artist,
                source_url=excluded.source_url, cover_path=excluded.cover_path
            """,
            (playlist_id, title, artist, source_url, cover_path, time.time()),
        )


def replace_tracks(playlist_id: str, tracks: list[dict[str, Any]]) -> None:
    """Re-insere a tracklist da playlist preservando o que ja foi baixado."""
    with connect() as conn:
        previous = {
            row["external_id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM tracks WHERE playlist_id = ? AND status = 'done'", (playlist_id,)
            ).fetchall()
            if row["external_id"]
        }
        conn.execute("DELETE FROM tracks WHERE playlist_id = ?", (playlist_id,))
        now = time.time()
        for pos, track in enumerate(tracks):
            track_id = track.get("id") or str(uuid.uuid4())
            status = "pending"
            progress = 0
            file_path = None
            keep = previous.get(track.get("external_id"))
            if keep:
                status = "done"
                progress = 1.0
                file_path = keep.get("file_path")
            conn.execute(
                """
                INSERT INTO tracks (id, playlist_id, pos, external_id, title, artist, album,
                                    duration, status, progress, file_path, source_url, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track_id,
                    playlist_id,
                    pos,
                    track.get("external_id"),
                    track.get("title") or "Sem título",
                    track.get("artist"),
                    track.get("album"),
                    track.get("duration"),
                    status,
                    progress,
                    file_path,
                    track.get("source_url"),
                    now,
                ),
            )
        conn.execute(
            "UPDATE playlists SET track_count = ? WHERE id = ?",
            (len(tracks), playlist_id),
        )


def list_playlists() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM tracks t WHERE t.playlist_id = p.id) AS total_tracks,
                   (SELECT COUNT(*) FROM tracks t WHERE t.playlist_id = p.id AND t.status = 'done') AS done_tracks
            FROM playlists p
            ORDER BY p.created_at DESC
            """
        ).fetchall()
    return rows_to_dicts(rows)


def get_playlist(playlist_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    return dict(row) if row else None


def delete_playlist(playlist_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM jobs WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))


# ------------------------------------------------------------------------------ tracks

def list_tracks(playlist_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if playlist_id:
            rows = conn.execute(
                "SELECT * FROM tracks WHERE playlist_id = ? ORDER BY pos", (playlist_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tracks ORDER BY added_at DESC, pos").fetchall()
    return rows_to_dicts(rows)


def get_track(track_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return dict(row) if row else None


def update_track(track_id: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "status", "progress", "error", "file_path", "cover_path", "source_url",
        "channel", "youtube_id", "artist", "album", "duration", "title",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    assignments = ", ".join(f"{key} = ?" for key in sets)
    with connect() as conn:
        conn.execute(
            f"UPDATE tracks SET {assignments} WHERE id = ?",
            (*sets.values(), track_id),
        )


def delete_track(track_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))


def reset_tracks(playlist_id: str) -> None:
    """Mark every track of a playlist back to pending (used by 'redownload')."""
    with connect() as conn:
        conn.execute(
            "UPDATE tracks SET status='pending', progress=0, error=NULL WHERE playlist_id = ?",
            (playlist_id,),
        )


def stats() -> dict[str, int]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM playlists) AS playlists,
              (SELECT COUNT(*) FROM tracks)    AS tracks,
              (SELECT COUNT(*) FROM tracks WHERE status='done') AS ready,
              (SELECT COUNT(*) FROM tracks WHERE status='error') AS failed,
              (SELECT COUNT(*) FROM tracks WHERE status='downloading') AS active
            """
        ).fetchone()
    return dict(row) if row else {}


# -------------------------------------------------------------------------------- jobs

def create_job(playlist_id: str, total: int) -> str:
    job_id = str(uuid.uuid4())
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, playlist_id, state, total, completed, failed, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, 0, 0, ?, ?)
            """,
            (job_id, playlist_id, total, now, now),
        )
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    allowed = {"state", "total", "completed", "failed", "message"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    assignments = ", ".join(f"{key} = ?" for key in sets)
    with connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
            (*sets.values(), time.time(), job_id),
        )


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return rows_to_dicts(rows)


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None
