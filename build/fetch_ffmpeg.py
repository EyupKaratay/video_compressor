#!/usr/bin/env python3
"""Paketlemede gömülecek ffmpeg/ffprobe ikililerini hazırlar ve DOĞRULAR.

Kullanım:
    python3 build/fetch_ffmpeg.py [hedef_klasor]      # varsayılan: build/bin

Sıra:
  1. Hedef klasörde zaten varsa kullanılır.
  2. FFMPEG_BIN / FFPROBE_BIN ortam değişkenleri varsa oradan kopyalanır.
  3. Yoksa platforma uygun statik derleme indirilir.

Her durumda son adımda ikililer çalıştırılıp libx264 ve aac kodlayıcılarının
varlığı denetlenir. Bu denetim geçmezse paketleme yapılmaz — çünkü o ikiliyle
uygulama çalışmaz.

Kaynaklar:
  Windows : BtbN/FFmpeg-Builds (GPL, libx264 dahil)
  Linux   : johnvansickle.com statik derlemeleri — BtbN'in yarısı boyutunda
            (her kodeği değil gerekenleri içerir) ve eski glibc tabanıyla
            derlendiği için eski dağıtımlarda da çalışır
  macOS   : eugeneware/ffmpeg-static (darwin-arm64 / darwin-x64)
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
ARM = platform.machine().lower() in ("arm64", "aarch64")
NAMES = ("ffmpeg.exe", "ffprobe.exe") if IS_WIN else ("ffmpeg", "ffprobe")

BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
STATIC = "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/"
JVS = "https://johnvansickle.com/ffmpeg/releases/"


def log(message: str) -> None:
    print(message, flush=True)


def download(url: str, dest: Path) -> None:
    log(f"İndiriliyor: {url}")
    with urllib.request.urlopen(url, timeout=300) as response, open(dest, "wb") as handle:
        shutil.copyfileobj(response, handle)
    log(f"  {dest.stat().st_size / 1e6:.1f} MB")


def fetch_archive(target: Path) -> None:
    """Windows ve Linux: tek arşivden iki ikiliyi çıkar."""
    if IS_WIN:
        if ARM:
            raise SystemExit("Windows ARM64 için hazır derleme yok; ikilileri elle koyun.")
        url = BTBN + "ffmpeg-master-latest-win64-gpl.zip"
    else:
        url = JVS + ("ffmpeg-release-arm64-static.tar.xz" if ARM
                     else "ffmpeg-release-amd64-static.tar.xz")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / Path(url).name
        download(url, archive)
        found = 0
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    if Path(member).name in NAMES:
                        with zf.open(member) as src, open(target / Path(member).name, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        found += 1
        else:
            with tarfile.open(archive) as tf:
                for member in tf.getmembers():
                    if member.isfile() and Path(member.name).name in NAMES:
                        stream = tf.extractfile(member)
                        if stream is None:
                            continue
                        with stream, open(target / Path(member.name).name, "wb") as dst:
                            shutil.copyfileobj(stream, dst)
                        found += 1
        if found < 2:
            raise SystemExit("Arşivde ffmpeg/ffprobe bulunamadı.")


def fetch_macos(target: Path) -> None:
    """macOS: mimariye uygun tek tek ikililer."""
    arch = "arm64" if ARM else "x64"
    for name in NAMES:
        download(f"{STATIC}{name}-darwin-{arch}", target / name)
    # İnternetten inen dosyalar karantinaya alınır; paketlemeden önce temizle.
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(target)],
                   capture_output=True)


def from_env(target: Path) -> bool:
    ffmpeg, ffprobe = os.environ.get("FFMPEG_BIN"), os.environ.get("FFPROBE_BIN")
    if not (ffmpeg and ffprobe):
        return False
    shutil.copy2(ffmpeg, target / NAMES[0])
    shutil.copy2(ffprobe, target / NAMES[1])
    log(f"FFMPEG_BIN / FFPROBE_BIN değişkenlerinden kopyalandı → {target}")
    return True


def verify(target: Path) -> None:
    """İkililer gerçekten işe yarıyor mu? Yaramıyorsa paketleme yapma."""
    ffmpeg, ffprobe = target / NAMES[0], target / NAMES[1]
    for path in (ffmpeg, ffprobe):
        if not path.is_file():
            raise SystemExit(f"Eksik: {path}")
        path.chmod(0o755)

    try:
        version = subprocess.run([str(ffmpeg), "-hide_banner", "-version"],
                                 capture_output=True, text=True, timeout=60)
        encoders = subprocess.run([str(ffmpeg), "-hide_banner", "-encoders"],
                                  capture_output=True, text=True, timeout=60).stdout
        probe = subprocess.run([str(ffprobe), "-hide_banner", "-version"],
                               capture_output=True, text=True, timeout=60)
    except OSError as exc:
        raise SystemExit(f"İkili çalıştırılamadı ({exc}). Mimariye uygun mu?")

    if version.returncode != 0 or probe.returncode != 0:
        raise SystemExit("ffmpeg/ffprobe çalışmıyor:\n"
                         + (version.stderr or probe.stderr or "")[:400])

    missing = [enc for enc in ("libx264", " aac ") if enc not in encoders]
    if missing:
        raise SystemExit(
            "Bu ffmpeg derlemesinde gerekli kodlayıcılar yok: "
            + ", ".join(m.strip() for m in missing)
            + "\nlibx264 içeren (GPL) bir derleme gerekiyor; uygulama bu ikiliyle "
              "çalışmaz. build/bin içine uygun ikilileri koyup tekrar deneyin."
        )

    log("Doğrulandı: " + version.stdout.splitlines()[0])
    log("            libx264 ve aac kodlayıcıları mevcut.")


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "build/bin").resolve()
    target.mkdir(parents=True, exist_ok=True)

    if all((target / name).is_file() for name in NAMES):
        log(f"Mevcut ikililer kullanılıyor → {target}")
    elif not from_env(target):
        (fetch_macos if IS_MAC else fetch_archive)(target)

    verify(target)
    log(f"Hazır → {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
