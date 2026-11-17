"""offline_playlist/launcher.py -- ponto de entrada do executavel empacotado.

Quando rodado como script (python launcher.py) ou como exe (PyInstaller), sobe o
servidor na porta 8722 e abre o navegador. Sem dependencia de cwd.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from offline_playlist.server import serve


def main() -> None:
    serve(host="0.0.0.0", port=8722, open_browser=True)


if __name__ == "__main__":
    main()
