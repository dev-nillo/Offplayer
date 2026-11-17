"""offline_playlist/run.py -- entry point.

    python -m offline_playlist.run   (a partir da pasta pai)
    python run.py                    (a partir desta pasta)

Opcoes:
    --host 0.0.0.0        aceita conexoes de outros dispositivos na rede local
    --port 9000
    --no-browser
"""
from __future__ import annotations

import argparse
import pathlib
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from offline_playlist.server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Offplayer - importador de playlist + player local")
    parser.add_argument("--host", default=None, help="endereco de bind (padrao 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="porta HTTP (padrao 8722)")
    parser.add_argument("--no-browser", action="store_true", help="nao abre o navegador ao iniciar")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, open_browser=False if args.no_browser else None)


if __name__ == "__main__":
    main()
