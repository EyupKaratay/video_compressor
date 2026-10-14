#!/usr/bin/env bash
# Linux için tek dosyalık çalıştırılabilir üretir: dist/VideoSikistirici
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="VideoSikistirici"
BIN="build/bin"

echo "==> ffmpeg hazırlanıyor"
python3 build/fetch_ffmpeg.py "$BIN"

echo "==> PyInstaller ortamı"
python3 -m venv build/.venv
# shellcheck disable=SC1091
source build/.venv/bin/activate
python -m pip install --quiet --upgrade pip pyinstaller

echo "==> Paketleniyor"
pyinstaller --noconfirm --clean --onefile --windowed \
  --name "$NAME" \
  --add-binary "$BIN/ffmpeg:bin" \
  --add-binary "$BIN/ffprobe:bin" \
  --exclude-module numpy --exclude-module PIL --exclude-module pytest \
  video_compressor.py

chmod +x "dist/$NAME"
echo
echo "Hazır: dist/$NAME  ($(du -h "dist/$NAME" | cut -f1))"
echo "Takım arkadaşlarınıza bu tek dosyayı gönderin."
echo "Not: Bazı masaüstlerinde çift tıklamadan önce çalıştırma izni gerekir:"
echo "     chmod +x $NAME"
