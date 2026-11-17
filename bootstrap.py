"""offline_playlist/bootstrap.py -- installs the runtime dependencies and prepares the app.

    python bootstrap.py            # installs yt-dlp + mutagen + imageio-ffmpeg, checks ffmpeg
    python bootstrap.py --ffmpeg   # also installs ffmpeg via winget/choco when missing

imageio-ffmpeg is used as the fallback ffmpeg provider: it ships a static binary inside the
site-packages, so the app converts audio to mp3 even when ffmpeg is not on PATH.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from offline_playlist import config


def run(cmd: list[str]) -> tuple[int, str]:
    print(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except OSError as exc:
        return 1, str(exc)


def install_python_packages() -> bool:
    print("instalando pacotes python (yt-dlp, mutagen, imageio-ffmpeg)...")
    code, output = config.pip_install("yt-dlp", "mutagen", "imageio-ffmpeg")
    if code != 0:
        print(output[-1500:])
        print("  falhou -- tente manualmente:  python -m pip install yt-dlp mutagen imageio-ffmpeg")
        return False
    print("  ok")
    return True


def install_ffmpeg_via_system() -> bool:
    if shutil.which("winget"):
        code, output = run([
            "winget", "install", "--id", "Gyan.FFmpeg", "-e",
            "--accept-source-agreements", "--accept-package-agreements",
        ])
        if code == 0:
            print("  ffmpeg instalado via winget")
            return True
        print(output[-800:])
    if shutil.which("choco"):
        code, output = run(["choco", "install", "ffmpeg", "-y"])
        if code == 0:
            print("  ffmpeg instalado via chocolatey")
            return True
        print(output[-800:])
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="bootstrap do Offplayer")
    parser.add_argument("--ffmpeg", action="store_true", help="tenta instalar o ffmpeg pelo sistema")
    args = parser.parse_args()

    config.ensure_dirs()
    print(f"projeto: {config.APP_DIR}")
    print(f"biblioteca: {config.load()['library_dir']}")

    install_python_packages()

    if not config.ffmpeg_path():
        print("ffmpeg ausente")
        if args.ffmpeg:
            install_ffmpeg_via_system()
        else:
            print("  (rode com --ffmpeg para tentar instalar pelo winget/choco)")
        print("  alternativa ja coberta: imageio-ffmpeg fornece o binario")

    report = config.dependency_report()
    print("\nstatus:")
    for key, value in report.items():
        print(f"  {key}: {value}")
    if report["ready"]:
        print("\npronto:  python run.py")
    else:
        print("\nfaltam dependencias -- reinstale com:  python -m pip install yt-dlp mutagen imageio-ffmpeg")


if __name__ == "__main__":
    main()
