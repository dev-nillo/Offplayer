#!/usr/bin/env bash
# run_termux.sh -- sobe o servidor no celular (localhost, sem expor na rede).
set -e
cd "$(dirname "$0")"

# biblioteca na memoria compartilhada do celular (visivel no app de Musica/Files)
export OFFLINE_LIBRARY_DIR="$HOME/storage/music/Offplayer"

mkdir -p "$OFFLINE_LIBRARY_DIR"
python run.py --host 127.0.0.1 --port 8722 --no-browser
