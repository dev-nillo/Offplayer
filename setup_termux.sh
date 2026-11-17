#!/usr/bin/env bash
# setup_termux.sh -- prepara o Offplayer para rodar no próprio celular Android (Termux).
# Uso:
#   pkg install git  (se ainda nao tiver)
#   git clone https://github.com/dev-nillo/Offplayer.git
#   cd Offplayer
#   bash setup_termux.sh
set -e

echo "==> atualizando pacotes"
pkg update -y && pkg upgrade -y

echo "==> instalando python, ffmpeg e dependencias de build"
pkg install -y python ffmpeg clang make pkg-config

echo "==> pedindo acesso ao armazenamento do celular"
termux-setup-storage

echo "==> instalando dependencias python"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "==> criando biblioteca em ~/storage/music/Offplayer"
mkdir -p "$HOME/storage/music/Offplayer"

echo "==> pronto"
echo "    rode:  bash run_termux.sh"
echo "    depois abra no navegador do celular:  http://127.0.0.1:8722"
