#!/usr/bin/env bash
# macOS için uygulama üretir: dist/VideoSikistirici.app  (+ zip)
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="VideoSikistirici"
BIN="build/bin"
mkdir -p "$BIN"

echo "==> ffmpeg hazırlanıyor ve doğrulanıyor"
python3 build/fetch_ffmpeg.py "$BIN"

# Dağıtılabilirlik denetimi: sistem dışı dylib bağımlılığı varsa ortaya çıkan
# .app yalnızca aynı kütüphaneler kurulu Mac'lerde çalışır.
EXTERNAL=$(otool -L "$BIN/ffmpeg" | tail -n +2 \
  | grep -vE '/usr/lib/|/System/Library/' | awk '{print $1}' || true)
if [[ -n "$EXTERNAL" ]]; then
  echo
  echo "!!! UYARI: ffmpeg dinamik bağlı. Gerekli kütüphaneler:"
  echo "$EXTERNAL" | sed 's/^/      /'
  echo "    Bu .app yalnızca aynı kütüphaneler kurulu Mac'lerde çalışır."
  echo "    Takıma dağıtacaksanız statik ffmpeg kullanın (build/bin içine koyun)."
  echo
  if [[ "${ASSUME_YES:-}" != "1" ]]; then
    read -r -p "    Yine de devam edilsin mi? [e/H] " yanit
    [[ "$yanit" == "e" || "$yanit" == "E" ]] || exit 1
  fi
fi

echo "==> PyInstaller ortamı"
python3 -m venv build/.venv
# shellcheck disable=SC1091
source build/.venv/bin/activate
python -m pip install --quiet --upgrade pip pyinstaller

echo "==> Paketleniyor"
pyinstaller --noconfirm --clean --onefile --windowed \
  --name "$NAME" \
  --osx-bundle-identifier "com.qa.videosikistirici" \
  --add-binary "$BIN/ffmpeg:bin" \
  --add-binary "$BIN/ffprobe:bin" \
  --exclude-module numpy --exclude-module PIL --exclude-module pytest \
  video_compressor.py

# Gatekeeper'ın "bozuk" demesini önlemek için geçici imza (ad-hoc).
codesign --force --deep --sign - "dist/$NAME.app" 2>/dev/null || true

( cd dist && ditto -c -k --keepParent "$NAME.app" "$NAME-macos.zip" )

echo
echo "Hazır: dist/$NAME.app  →  dist/$NAME-macos.zip (takıma bunu gönderin)"
echo
echo "ÖNEMLİ — ilk açılışta macOS imzasız uygulamayı engeller. Takım arkadaşınız:"
echo "  • uygulamaya SAĞ TIK → 'Aç' → açılan uyarıda yine 'Aç' demeli,"
echo "  • veya terminalde: xattr -dr com.apple.quarantine /Applications/$NAME.app"
