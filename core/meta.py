"""offline_playlist/core/meta.py -- resolucao de playlist/track.

Tres caminhos, nesta ordem:

1. **Spotify Web API** (recomendado): credenciais client_id/secret do app em developers.spotify.com.
   Traz paginacao completa (100 por pagina), album, artistas estruturados e ISRC.
2. **Embed publico do Spotify** (sem credencial): raspa o __NEXT_DATA__ de
   https://open.spotify.com/embed/playlist/<id> -- titulo, artistas, duracao, uri e capa.
   Limite: o embed entrega no maximo ~50 faixas.
3. **yt-dlp** para URLs de YouTube / YouTube Music (playlists completas, sem limite).

O audio em si vem do YouTube (canais "- Topic" oficiais sao priorizados), resolvido em
core.downloader.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import config

try:  # pragma: no cover
    import yt_dlp
except Exception:  # pragma: no cover
    yt_dlp = None  # type: ignore


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

SPOTIFY_URL_RE = re.compile(
    r"(?:https?://open\.spotify\.com/(?P<kind>playlist|album|track)/(?P<id>[A-Za-z0-9]+)"
    r"|spotify:(?P<kind2>playlist|album|track):(?P<id2>[A-Za-z0-9]+))",
    re.IGNORECASE,
)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


class MetadataError(RuntimeError):
    """Erro de leitura de metadados -- a mensagem vai direto para a UI."""


# --------------------------------------------------------------------------- utils

def require_ytdlp() -> Any:
    if yt_dlp is None:
        raise MetadataError(
            "yt-dlp não está instalado. Rode:  python -m pip install -r requirements.txt"
        )
    return yt_dlp


def normalize_source(url: str) -> dict[str, Any]:
    """Classifica a URL colada pelo usuario."""
    url = (url or "").strip()
    if not url:
        raise MetadataError("URL vazia.")
    match = SPOTIFY_URL_RE.search(url)
    if match:
        kind = (match.group("kind") or match.group("kind2") or "playlist").lower()
        ident = match.group("id") or match.group("id2")
        return {"kind": "spotify", "type": kind, "id": ident, "url": f"https://open.spotify.com/{kind}/{ident}"}
    if not re.match(r"^https?://", url):
        raise MetadataError("URL inválida. Use um link de playlist do Spotify ou do YouTube.")
    return {"kind": "other", "type": "url", "id": url, "url": url}


def _http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _http_post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _walk(node: Any, key: str):
    """Percorre o JSON do embed procurando a primeira chave pedida."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                return v
            found = _walk(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk(item, key)
            if found is not None:
                return found
    return None


def _pick_cover(entity: dict[str, Any]) -> str | None:
    cover = entity.get("coverArt") or {}
    sources = cover.get("sources") if isinstance(cover, dict) else None
    if not sources:
        return None
    best = None
    for src in sources:
        if not isinstance(src, dict) or not src.get("url"):
            continue
        size = src.get("width") or 0
        if best is None or size > (best.get("width") or 0):
            best = src
    return best["url"] if best else None


def _cache_cover(cover_url: str | None, key: str) -> str | None:
    """Baixa a capa uma vez para a UI funcionar sem internet."""
    if not cover_url:
        return None
    covers = config.DATA_DIR / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    dest = covers / f"{config.sanitize_filename(key)}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    try:
        req = urllib.request.Request(cover_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read()
        if payload:
            dest.write_bytes(payload)
            return str(dest)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return None


def _stable_id(source: str) -> str:
    import hashlib

    return "pl_" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- Spotify: Web API

def _api_token(cfg: dict[str, Any]) -> str | None:
    client_id = (cfg.get("spotify_client_id") or "").strip()
    client_secret = (cfg.get("spotify_client_secret") or "").strip()
    if not client_id or not client_secret:
        return None
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    try:
        payload = _http_post_form(
            "https://accounts.spotify.com/api/token",
            {"grant_type": "client_credentials"},
            {"Authorization": f"Basic {basic}"},
        )
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return payload.get("access_token")


def _spotify_api_playlist(ident: str, cfg: dict[str, Any], token: str) -> dict[str, Any]:
    """Playlist completa via Web API, com paginacao."""
    headers = {"Authorization": f"Bearer {token}"}
    detail = _http_get_json(f"https://api.spotify.com/v1/playlists/{ident}", headers)
    tracks: list[dict[str, Any]] = []
    url = f"https://api.spotify.com/v1/playlists/{ident}/tracks?limit=100"
    while url:
        page = _http_get_json(url, headers)
        for item in page.get("items") or []:
            track = item.get("track") or {}
            if not track.get("name"):
                continue
            if track.get("is_local"):
                continue
            artists = ", ".join(a.get("name", "") for a in track.get("artists") or [] if a.get("name"))
            tracks.append(
                {
                    "external_id": track.get("id"),
                    "title": track["name"].strip(),
                    "artist": artists or None,
                    "album": (track.get("album") or {}).get("name"),
                    "duration": (track.get("duration_ms") or 0) / 1000.0 or None,
                    "source_url": f"https://open.spotify.com/track/{track.get('id')}",
                    "isrc": track.get("external_ids", {}).get("isrc"),
                }
            )
        url = page.get("next")
    images = detail.get("images") or []
    cover_url = images[0]["url"] if images else None
    return {
        "title": (detail.get("name") or "Playlist").strip(),
        "artist": (detail.get("owner") or {}).get("display_name"),
        "cover_url": cover_url,
        "tracks": tracks,
        "source": "api",
    }


# ------------------------------------------------------------- Spotify: embed page

def _spotify_embed(entity_kind: str, ident: str) -> dict[str, Any]:
    """Le o __NEXT_DATA__ da pagina de embed (sem credencial)."""
    url = f"https://open.spotify.com/embed/{entity_kind}/{ident}"
    html = _http_get(url)
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise MetadataError(
            "O embed público do Spotify não devolveu dados para essa playlist. "
            "Cadastre um app em developers.spotify.com e preencha client_id/client_secret "
            "em Configurações, ou tente uma playlist pública."
        )
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise MetadataError(f"resposta do Spotify ilegível: {exc}") from exc

    entity = _walk(data, "entity")
    if not isinstance(entity, dict):
        raise MetadataError("Estrutura da playlist inesperada no embed do Spotify.")
    track_list = entity.get("trackList") or []

    tracks: list[dict[str, Any]] = []
    for entry in track_list:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or "").strip()
        uri = entry.get("uri") or ""
        if not title or not uri.startswith("spotify:track:"):
            continue
        track_id = uri.split(":")[-1]
        duration_ms = entry.get("duration") or 0
        tracks.append(
            {
                "external_id": track_id,
                "title": title,
                "artist": (entry.get("subtitle") or None),
                "album": None,
                "duration": (duration_ms / 1000.0) if duration_ms else None,
                "source_url": f"https://open.spotify.com/track/{track_id}",
                "isrc": None,
            }
        )

    if not tracks:
        raise MetadataError(
            "A playlist veio sem faixas no embed (pode ser privada ou ter mais de 50 faixas). "
            "Use credenciais da API em Configurações para a lista completa."
        )

    return {
        "title": (entity.get("title") or "Playlist").strip(),
        "artist": None,
        "cover_url": _pick_cover(entity),
        "tracks": tracks,
        "source": "embed",
        "truncated": len(track_list) >= 50,
    }


def _spotify_single_track(ident: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Uma faixa vira uma playlist de um item (usado quando o usuario cola um link de track)."""
    token = _api_token(cfg)
    if token:
        try:
            headers = {"Authorization": f"Bearer {token}"}
            data = _http_get_json(f"https://api.spotify.com/v1/tracks/{ident}", headers)
            artists = ", ".join(a.get("name", "") for a in data.get("artists") or [] if a.get("name"))
            return {
                "title": (data.get("name") or "Faixa").strip(),
                "artist": artists or None,
                "cover_url": (data.get("album") or {}).get("images", [{}])[0].get("url"),
                "tracks": [
                    {
                        "external_id": ident,
                        "title": (data.get("name") or "Faixa").strip(),
                        "artist": artists or None,
                        "album": (data.get("album") or {}).get("name"),
                        "duration": (data.get("duration_ms") or 0) / 1000.0 or None,
                        "source_url": f"https://open.spotify.com/track/{ident}",
                        "isrc": (data.get("external_ids") or {}).get("isrc"),
                    }
                ],
                "source": "api",
            }
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
            pass

    html = _http_get(f"https://open.spotify.com/embed/track/{ident}")
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise MetadataError("Não consegui ler a faixa no embed do Spotify.")
    data = json.loads(match.group(1))
    entity = _walk(data, "entity") or {}
    artists = entity.get("artists") or []
    artist_names = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name"))
    duration_ms = entity.get("duration") or 0
    return {
        "title": (entity.get("name") or "Faixa").strip(),
        "artist": artist_names or None,
        "cover_url": None,
        "tracks": [
            {
                "external_id": ident,
                "title": (entity.get("name") or "Faixa").strip(),
                "artist": artist_names or None,
                "album": None,
                "duration": (duration_ms / 1000.0) if duration_ms else None,
                "source_url": f"https://open.spotify.com/track/{ident}",
                "isrc": None,
            }
        ],
        "source": "embed",
        "truncated": False,
    }


# ------------------------------------------------------------------ YouTube / yt-dlp

def _ytdlp_playlist(url: str) -> dict[str, Any]:
    ydl_mod = require_ytdlp()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 5,
    }
    with ydl_mod.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise MetadataError("yt-dlp não conseguiu ler essa URL.")
    entries = info.get("entries") or []
    tracks: list[dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        tracks.append(
            {
                "external_id": entry.get("id"),
                "title": title,
                "artist": entry.get("artist") or entry.get("uploader") or entry.get("channel"),
                "album": entry.get("album"),
                "duration": entry.get("duration"),
                "source_url": entry.get("url") or entry.get("webpage_url"),
                "isrc": None,
            }
        )
    if not tracks:
        raise MetadataError("A playlist veio vazia.")
    return {
        "title": (info.get("title") or "Playlist").strip(),
        "artist": info.get("uploader") or info.get("channel"),
        "cover_url": _best_thumbnail(info),
        "tracks": tracks,
        "source": "ytdlp",
        "truncated": False,
    }


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        best = max(
            thumbs,
            key=lambda t: (t.get("preference") or 0, t.get("width") or 0, t.get("height") or 0),
        )
        if best.get("url"):
            return best["url"]
    return info.get("thumbnail")


# --------------------------------------------------------------------- fachada

def resolve_playlist(url: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve qualquer URL aceita em uma playlist + tracklist normalizada."""
    cfg = cfg or config.load()
    source = normalize_source(url)

    if source["kind"] == "spotify":
        if source["type"] == "track":
            data = _spotify_single_track(source["id"], cfg)
        else:
            data = None
            token = _api_token(cfg)
            if token:
                try:
                    data = _spotify_api_playlist(source["id"], cfg, token)
                except (urllib.error.URLError, OSError, ValueError, KeyError):
                    data = None
            if data is None:
                data = _spotify_embed("playlist" if source["type"] == "playlist" else "album", source["id"])
    else:
        data = _ytdlp_playlist(source["url"])

    playlist_id = _stable_id(source["url"])
    cover_path = _cache_cover(data.get("cover_url"), playlist_id)

    return {
        "id": playlist_id,
        "title": data["title"],
        "artist": data.get("artist"),
        "source_url": source["url"],
        "cover_path": cover_path,
        "cover_url": data.get("cover_url"),
        "tracks": data["tracks"],
        "resolver": data.get("source"),
        "truncated": bool(data.get("truncated")),
    }


def track_details(
    source_url: str | None,
    fallback_title: str,
    fallback_artist: str | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Metadados extras de uma faixa (álbum e data quando disponíveis)."""
    cfg = cfg or config.load()
    details: dict[str, Any] = {
        "title": fallback_title,
        "artist": fallback_artist,
        "album": None,
        "duration": None,
        "year": None,
        "isrc": None,
    }
    if not source_url or "open.spotify.com/track/" not in source_url:
        return details
    track_id = source_url.rstrip("/").split("/")[-1]

    token = _api_token(cfg)
    if token:
        try:
            data = _http_get_json(
                f"https://api.spotify.com/v1/tracks/{track_id}",
                {"Authorization": f"Bearer {token}"},
            )
            artists = ", ".join(a.get("name", "") for a in data.get("artists") or [] if a.get("name"))
            details.update(
                {
                    "title": (data.get("name") or details["title"]).strip(),
                    "artist": artists or details["artist"],
                    "album": (data.get("album") or {}).get("name"),
                    "duration": (data.get("duration_ms") or 0) / 1000.0 or details["duration"],
                    "year": (data.get("album") or {}).get("release_date", "")[:4] or None,
                    "isrc": (data.get("external_ids") or {}).get("isrc"),
                }
            )
            return details
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            pass

    try:
        html = _http_get(f"https://open.spotify.com/embed/track/{track_id}")
        match = NEXT_DATA_RE.search(html)
        if not match:
            return details
        entity = _walk(json.loads(match.group(1)), "entity") or {}
        artists = entity.get("artists") or []
        names = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name"))
        release = entity.get("releaseDate") or {}
        details.update(
            {
                "title": (entity.get("name") or details["title"]).strip(),
                "artist": names or details["artist"],
                "duration": (entity.get("duration") or 0) / 1000.0 or details["duration"],
                "year": (release.get("isoString") or "")[:4] or None,
            }
        )
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        pass
    return details
