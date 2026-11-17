"""offline_playlist/build_exe.py -- empacota o app em um unico .exe (Windows).

    python build_exe.py            # instala o PyInstaller se preciso e gera dist/Offplayer.exe
    python build_exe.py --clean    # limpa build/ e dist/ antes

Requisitos: Python 3.11+, pip. O .exe final carrega yt-dlp/mutagen/imageio-ffmpeg do
ambiente onde foi gerado; para distribuir a terceiros, gere em uma maquina com esses
pacotes instalados (ou inclua-os com --collect-all).
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PROJECT = ROOT.parent


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def ensure_pyinstaller() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        print("instalando PyInstaller...")
        return run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"]) == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if not ensure_pyinstaller():
        print("falha ao instalar o PyInstaller")
        sys.exit(1)

    if args.clean:
        for folder in ("build", "dist"):
            shutil.rmtree(ROOT / folder, ignore_errors=True)

    sep = ";" if sys.platform.startswith("win") else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed" if False else "--console",
        "--name", "Offplayer",
        "--add-data", f"web{sep}web",
        "--hidden-import", "offline_playlist",
        "--hidden-import", "offline_playlist.core",
        "--hidden-import", "offline_playlist.core.db",
        "--hidden-import", "offline_playlist.core.meta",
        "--hidden-import", "offline_playlist.core.downloader",
        "--hidden-import", "offline_playlist.core.library",
        "--hidden-import", "yt_dlp",
        "--hidden-import", "mutagen",
        "--hidden-import", "imageio_ffmpeg",
        "launcher.py",
    ]
    # coleta yt-dlp inteiro (extratores, postprocessors) para o exe ser autocontido
    cmd += ["--collect-all", "yt_dlp", "--collect-all", "imageio_ffmpeg"]

    code = run(cmd)
    if code != 0:
        print("build falhou")
        sys.exit(code)

    exe = ROOT / "dist" / ("Offplayer.exe" if sys.platform.startswith("win") else "Offplayer")
    print("\npronto:", exe)
    print("rode o .exe; ele cria data/ e config.json ao lado de si.")


if __name__ == "__main__":
    main()
