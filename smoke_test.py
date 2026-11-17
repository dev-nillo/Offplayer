"""smoke test: sobe o servidor, exercita a API, o streaming com Range e a UI."""
import json
import pathlib
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from offline_playlist import config
from offline_playlist.core import db
from offline_playlist.server import Handler
from http.server import ThreadingHTTPServer

config.ensure_dirs()
db.init()

# faixa falsa para testar o streaming com Range
fake = config.DATA_DIR / "fake.mp3"
fake.write_bytes(bytes(range(256)) * 40)
db.upsert_playlist("pl_test", "Playlist Teste", "Artista", "https://open.spotify.com/playlist/abc", None)
db.replace_tracks("pl_test", [{"title": "Faixa Um", "artist": "Artista", "duration": 123.0,
                                "external_id": "x1", "source_url": "https://open.spotify.com/track/x1"}])
track = db.list_tracks("pl_test")[0]
db.update_track(track["id"], status="done", file_path=str(fake), progress=1.0)

httpd = ThreadingHTTPServer(("127.0.0.1", 8733), Handler)
httpd.daemon_threads = True
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.4)

base = "http://127.0.0.1:8733"


def get(path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read(), dict(resp.headers)


status, body, _ = get("/api/state")
state = json.loads(body)
print("GET /api/state ->", status, "playlists:", len(state["playlists"]),
      "tracks:", len(state["tracks"]), "ready:", state["deps"]["ready"])

status, body, headers = get("/")
print("GET / ->", status, "html:", b"Offplayer" in body)

status, body, _ = get("/static/app.js")
print("GET /static/app.js ->", status, "bytes:", len(body))

status, body, headers = get(f"/api/track/{track['id']}/file")
print("GET file ->", status, "len:", len(body), "accept-ranges:", headers.get("Accept-Ranges"))

status, body, headers = get(f"/api/track/{track['id']}/file", {"Range": "bytes=0-9"})
print("GET file Range 0-9 ->", status, "body:", body, "content-range:", headers.get("Content-Range"))

status, body, headers = get(f"/api/track/{track['id']}/file", {"Range": "bytes=100-199"})
print("GET file Range 100-199 ->", status, "bytes:", len(body), "content-range:", headers.get("Content-Range"))

# import invalido -> erro limpo, sem traceback
req = urllib.request.Request(base + "/api/import", data=json.dumps({"url": ""}).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(req, timeout=10)
    print("POST /api/import (vazio) -> nao deu erro")
except urllib.error.HTTPError as exc:
    print("POST /api/import (vazio) ->", exc.code, json.loads(exc.read())["error"])

# import de link spotify: resolve de verdade (embed publico), sem disparar download
pre_ids = {p["id"] for p in db.list_playlists()}
req = urllib.request.Request(base + "/api/import",
                             data=json.dumps({"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
                                              "auto_download": False}).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read())
    print("POST /api/import (spotify) ->", resp.status,
          "| resolver:", payload.get("resolver"),
          "| faixas:", len(payload.get("tracks") or []),
          "| truncado:", payload.get("truncated"))
    created = payload.get("playlist", {}).get("id")
except urllib.error.HTTPError as exc:
    created = None
    print("POST /api/import (spotify) ->", exc.code, json.loads(exc.read())["error"])

# settings
before_cfg = dict(config.load())
req = urllib.request.Request(base + "/api/settings", data=json.dumps({"settings": {"audio_quality": "320"}}).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=10) as resp:
    print("POST /api/settings ->", resp.status, json.loads(resp.read())["config"]["audio_quality"])
config.save({"audio_quality": before_cfg.get("audio_quality")})

db.delete_track(track["id"])
db.delete_playlist("pl_test")
if created and created not in pre_ids:
    db.delete_playlist(created)
fake.unlink(missing_ok=True)
httpd.shutdown()
print("smoke ok")
