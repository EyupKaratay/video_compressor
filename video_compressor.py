#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Sıkıştırıcı — Jira ek dosya sınırı için.

Hata kayıtlarını (ekran kaydı) Jira'nın ek dosya sınırının altına indirir,
görüntüyü mümkün olduğunca bozmadan, ve sonucu sistemin "Videolar" klasörüne
açıklayıcı bir isimle kaydeder.

Çalışma mantığı
---------------
1. ffprobe ile süre / çözünürlük / fps / ses bilgisi okunur.
2. Hedef boyuttan bit bütçesi hesaplanır (ses + kapsayıcı payı düşülür).
3. Tek geçiş "capped CRF": kaliteyi -crf belirler, boyutu -maxrate/-bufsize
   sınırlar. Kolay içerik (çoğu ekran kaydı) hedeften çok küçük çıkar.
4. Yine de sınırı aşarsa, ölçülen boyuta göre 2 geçişli ABR ile tam isabet.
5. Çözünürlük ancak bit bütçesi piksel başına çok düşerse düşürülür; amaç
   ekran kaydındaki yazıların okunabilir kalması.

Tek dosya: GUI + komut satırı + sıkıştırma motoru.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

APP_NAME = "Video Sıkıştırıcı"
APP_VERSION = "1.0.0"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

MB = 1_000_000                  # arayüzde 1 MB = 1.000.000 bayt
JIRA_LIMIT_BYTES = 20_000_000   # Jira'nın "19.07 MB" diye gösterdiği gerçek sınır
DEFAULT_TARGET_MB = 19.0        # güvenli varsayılan (sınırın %95'i)

CONTAINER_OVERHEAD = 0.985      # MP4 başlık/indeks payı
MIN_BPP = 0.030                 # piksel başına kabul edilebilir en az bit
ABS_MIN_VIDEO_BPS = 80_000      # bunun altında anlamlı video üretilemez
UPSCALE_TRIGGER = 0.75          # bütçenin bu kadarından azı kullanıldıysa
                                # çözünürlük boşa düşürülmüş demektir

STD_HEIGHTS = (2160, 1440, 1080, 900, 720, 600, 540, 480, 432, 360, 288)
AUDIO_LADDER = (192, 160, 128, 112, 96, 80, 64, 48)

PRESETS = {
    "hizli": ("veryfast", "Hızlı"),
    "dengeli": ("medium", "Dengeli (önerilen)"),
    "eniyi": ("slow", "En iyi kalite"),
}

VIDEO_EXTS = (
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".ogv",
)


class CompressError(Exception):
    """Kullanıcıya gösterilebilir hata."""


# ---------------------------------------------------------------------------
# Platform yardımcıları
# ---------------------------------------------------------------------------

def _popen_kwargs() -> dict:
    """Windows'ta siyah konsol penceresinin yanıp sönmesini engeller."""
    if not IS_WIN:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": si, "creationflags": 0x08000000}  # CREATE_NO_WINDOW


def app_dir() -> Path:
    """Çalıştırılabilir dosyanın (veya betiğin) bulunduğu klasör."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """PyInstaller --onefile açılım klasörü; paketlenmemişse app_dir()."""
    return Path(getattr(sys, "_MEIPASS", str(app_dir())))


def cache_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "VideoSikistirici"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "VideoSikistirici"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "videosikistirici"


def _exe(name: str) -> str:
    return name + ".exe" if IS_WIN else name


_TOOL_CACHE: dict[str, Optional[str]] = {}


def find_tool(name: str) -> Optional[str]:
    """ffmpeg/ffprobe'u sırayla: paket içi, exe yanı, PATH, bilinen yollar."""
    if name in _TOOL_CACHE:
        return _TOOL_CACHE[name]

    candidates: list[Path] = []
    for base in (bundle_dir(), bundle_dir() / "bin", app_dir(), app_dir() / "bin",
                 cache_dir(), cache_dir() / "bin"):
        candidates.append(base / _exe(name))

    found = shutil.which(name)
    if found:
        candidates.append(Path(found))

    for extra in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/snap/bin"):
        candidates.append(Path(extra) / _exe(name))

    result = None
    for cand in candidates:
        try:
            if cand.is_file() and os.access(str(cand), os.X_OK):
                result = str(cand)
                break
        except OSError:
            continue

    _TOOL_CACHE[name] = result
    return result


def _win_known_folder(guid_str: str) -> Optional[str]:
    """Windows'ta gerçek Videolar yolu (OneDrive yönlendirmesi dahil)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    guid = GUID()
    if ole32.CLSIDFromString(ctypes.create_unicode_buffer(guid_str), ctypes.byref(guid)) != 0:
        return None
    ptr = ctypes.c_wchar_p()
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) != 0:
        return None
    try:
        return ptr.value
    finally:
        ole32.CoTaskMemFree(ptr)


def videos_dir() -> Path:
    """Sistemin Videolar klasörü. Yoksa oluşturulur, olmazsa ev dizinine düşer."""
    home = Path.home()
    found: Optional[Path] = None
    try:
        if IS_WIN:
            raw = _win_known_folder("{18989B1D-99B5-455B-841C-AB7C74E4DDFC}")
            if raw:
                found = Path(raw)
        elif IS_MAC:
            found = home / "Movies"
        else:
            tool = shutil.which("xdg-user-dir")
            if tool:
                out = subprocess.run([tool, "VIDEOS"], capture_output=True, text=True,
                                     timeout=5, **_popen_kwargs()).stdout.strip()
                if out and Path(out) != home:
                    found = Path(out)
            if found is None:
                cfg = home / ".config" / "user-dirs.dirs"
                if cfg.is_file():
                    match = re.search(r'XDG_VIDEOS_DIR\s*=\s*"([^"]+)"',
                                      cfg.read_text(encoding="utf-8", errors="replace"))
                    if match:
                        found = Path(match.group(1).replace("$HOME", str(home)))
    except Exception:
        found = None

    if found is None:
        for name in ("Videos", "Videolar", "Movies", "Vídeos", "Vidéos", "Videot"):
            if (home / name).is_dir():
                found = home / name
                break
    if found is None:
        found = home / "Videos"

    try:
        found.mkdir(parents=True, exist_ok=True)
        return found
    except OSError:
        return home


def open_in_file_manager(path: Path) -> None:
    """Klasörü aç; dosya verildiyse onu seçili göstermeye çalış."""
    target = path if path.is_dir() else path.parent
    try:
        if IS_WIN:
            if path.is_file():
                subprocess.Popen(["explorer", f"/select,{path}"])
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception:
        pass


def human_size(num: float) -> str:
    if num >= MB:
        return f"{num / MB:.1f} MB"
    if num >= 1000:
        return f"{num / 1000:.0f} KB"
    return f"{int(num)} B"


def human_time(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Kaynak analizi
# ---------------------------------------------------------------------------

@dataclass
class MediaInfo:
    path: Path
    size: int
    duration: float
    width: int
    height: int
    fps: float
    vcodec: str
    acodec: str
    channels: int
    has_audio: bool

    @property
    def label(self) -> str:
        parts = [f"{self.width}x{self.height}", f"{self.fps:g} fps",
                 human_time(self.duration), human_size(self.size)]
        parts.append(f"ses: {self.acodec}" if self.has_audio else "ses yok")
        return " · ".join(parts)


def _ratio(value: Optional[str]) -> float:
    if not value or "/" not in value:
        try:
            return float(value) if value else 0.0
        except ValueError:
            return 0.0
    num, _, den = value.partition("/")
    try:
        num_f, den_f = float(num), float(den)
    except ValueError:
        return 0.0
    return num_f / den_f if den_f else 0.0


def probe(path: Path, ffprobe: str) -> MediaInfo:
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120, **_popen_kwargs())
    except subprocess.TimeoutExpired:
        raise CompressError("Dosya analizi zaman aşımına uğradı.")
    if res.returncode != 0:
        detail = (res.stderr or "").strip().splitlines()
        raise CompressError("Dosya okunamadı veya desteklenmiyor.\n"
                            + ("\n".join(detail[-3:]) if detail else ""))
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        raise CompressError("Dosya bilgisi çözümlenemedi.")

    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    vstream = None
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue  # kapak resmi, gerçek video değil
        vstream = stream
        break
    if vstream is None:
        raise CompressError("Dosyada video akışı bulunamadı.")

    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    if width < 2 or height < 2:
        raise CompressError("Video çözünürlüğü okunamadı.")

    rotation = 0
    for side in (vstream.get("side_data_list") or []):
        if "rotation" in side:
            try:
                rotation = int(abs(float(side["rotation"]))) % 180
            except (TypeError, ValueError):
                rotation = 0
    if rotation == 0:
        try:
            rotation = int(abs(float((vstream.get("tags") or {}).get("rotate", 0)))) % 180
        except (TypeError, ValueError):
            rotation = 0
    if rotation == 90:
        width, height = height, width

    fps = _ratio(vstream.get("avg_frame_rate")) or _ratio(vstream.get("r_frame_rate"))
    if not 0.5 <= fps <= 480:
        fps = 30.0

    duration = 0.0
    for raw in (fmt.get("duration"), vstream.get("duration")):
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            break
    if duration <= 0:
        duration = _duration_by_decode(path)
    if duration <= 0:
        raise CompressError("Video süresi belirlenemedi; dosya bozuk olabilir.")

    try:
        size = path.stat().st_size
    except OSError:
        size = int(fmt.get("size") or 0)

    return MediaInfo(
        path=path,
        size=size,
        duration=duration,
        width=width - width % 2,
        height=height - height % 2,
        fps=fps,
        vcodec=str(vstream.get("codec_name") or "?"),
        acodec=str((astream or {}).get("codec_name") or "-"),
        channels=int((astream or {}).get("channels") or 0),
        has_audio=astream is not None,
    )


def _duration_by_decode(path: Path) -> float:
    """Süre üstverisi yoksa (bazı ekran kaydedicilerde olur) tam çözerek bul."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        return 0.0
    cmd = [ffmpeg, "-hide_banner", "-i", str(path), "-map", "0:v:0",
           "-c", "copy", "-f", "null", os.devnull]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=600, **_popen_kwargs())
    except subprocess.TimeoutExpired:
        return 0.0
    last = 0.0
    for match in re.finditer(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)", res.stderr or ""):
        last = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    return last


# ---------------------------------------------------------------------------
# Kodlama planı
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    width: int
    height: int
    fps: float
    video_bps: int
    audio_bps: int
    crf: int
    scaled: bool
    fps_dropped: bool
    warning: str = ""

    @property
    def bpp(self) -> float:
        return self.video_bps / (self.width * self.height * self.fps)

    def describe(self) -> str:
        bits = [f"{self.width}x{self.height}", f"{self.fps:g} fps",
                f"video ~{self.video_bps // 1000} kbps",
                f"ses {self.audio_bps // 1000} kbps" if self.audio_bps else "ses kapalı",
                f"CRF {self.crf}"]
        return " · ".join(bits)


def _pick_audio_bps(info: MediaInfo, keep_audio: bool, total_bps: float) -> int:
    if not (keep_audio and info.has_audio):
        return 0
    wanted = 128 if info.channels >= 2 else 80
    cap = total_bps * 0.22
    for kbps in AUDIO_LADDER:
        if kbps <= wanted and kbps * 1000 <= cap:
            return kbps * 1000
    return 48_000 if cap >= 48_000 else 0


def _crf_for(height: int) -> int:
    if height <= 576:
        return 20
    if height <= 800:
        return 21
    if height <= 1100:
        return 22
    return 23


def make_plan(info: MediaInfo, target_bytes: int, keep_audio: bool = True,
              max_height: Optional[int] = None, min_bpp: float = MIN_BPP) -> Plan:
    duration = max(info.duration, 0.1)
    total_bps = target_bytes * 8.0 / duration * CONTAINER_OVERHEAD
    audio_bps = _pick_audio_bps(info, keep_audio, total_bps)
    video_bps = total_bps - audio_bps

    if video_bps < ABS_MIN_VIDEO_BPS:
        usable = target_bytes * 8.0 * CONTAINER_OVERHEAD / (ABS_MIN_VIDEO_BPS + audio_bps)
        raise CompressError(
            f"Bu video ({human_time(duration)}) {human_size(target_bytes)} içine "
            f"izlenebilir kalitede sığmıyor.\n"
            f"Bu hedefle en fazla ~{human_time(usable)} uzunluk önerilir.\n"
            "Videoyu kısaltın ya da hedef boyutu artırın."
        )

    source_h = info.height
    fps_out = 30.0 if info.fps > 32 else float(info.fps)
    warning = ""
    fps_dropped = False

    def choose_height(fps_value: float) -> int:
        options = [source_h] + [h for h in STD_HEIGHTS if h < source_h]
        if max_height:
            narrowed = [h for h in options if h <= max_height]
            options = narrowed or [min(options)]
        for height in options:
            width = max(2, round(info.width * height / source_h / 2) * 2)
            if video_bps / (width * height * fps_value) >= min_bpp:
                return height
        return options[-1]

    height_out = choose_height(fps_out)
    width_out = max(2, round(info.width * height_out / source_h / 2) * 2)

    # Hâlâ çok sıkışıksa çözünürlük yerine kare hızından ödün ver.
    if video_bps / (width_out * height_out * fps_out) < min_bpp * 0.8 and fps_out > 24:
        fps_out = 24.0
        fps_dropped = True
        height_out = choose_height(fps_out)
        width_out = max(2, round(info.width * height_out / source_h / 2) * 2)

    final_bpp = video_bps / (width_out * height_out * fps_out)
    if final_bpp < min_bpp * 0.7:
        warning = ("Bit bütçesi çok düşük; görüntüde belirgin bozulma olabilir. "
                   "Videoyu kısaltmak en iyi sonucu verir.")

    return Plan(
        width=width_out,
        height=height_out,
        fps=fps_out,
        video_bps=int(video_bps),
        audio_bps=int(audio_bps),
        crf=_crf_for(height_out),
        scaled=height_out != source_h,
        fps_dropped=fps_dropped,
        warning=warning,
    )


def plan_at_height(info: MediaInfo, plan: Plan, height: int) -> Plan:
    """Aynı bit bütçesini farklı bir çözünürlükte uygulayan plan kopyası."""
    width = max(2, round(info.width * height / info.height / 2) * 2)
    return replace(plan, width=width, height=height, crf=_crf_for(height),
                   scaled=height != info.height, warning="")


def next_height_up(info: MediaInfo, plan: Plan, size: int, target_bytes: int,
                   max_height: Optional[int],
                   previous: Optional[tuple[int, int]] = None) -> Optional[int]:
    """Ölçülen boyuta göre güvenle denenebilecek bir üst çözünürlük.

    Boyut, piksel sayısının bir kuvveti gibi artar: boyut ~ piksel**e.
    İlk denemede en kötü durum (e=1, doğrusal) varsayılır. İki ölçüm olunca e
    gerçek içerikten hesaplanır — durağan ekran kayıtlarında e çok küçüktür,
    yani çözünürlük neredeyse bedavadır ve tek adımda orijinaline çıkılabilir.
    """
    if size <= 0:
        return None
    headroom = target_bytes * 0.95 / size
    if headroom <= 1.0:
        return None

    exponent = 1.0
    if previous:
        prev_height, prev_size = previous
        if prev_size > 0 and plan.height > prev_height:
            pixel_growth = (plan.height / prev_height) ** 2
            size_growth = size / prev_size
            if pixel_growth > 1.05 and size_growth > 1.0:
                exponent = math.log(size_growth) / math.log(pixel_growth)
                exponent = min(1.0, max(0.25, exponent))

    ceiling = min(info.height, int(plan.height * headroom ** (1.0 / (2 * exponent))))
    if max_height:
        ceiling = min(ceiling, max_height)
    options = [h for h in (info.height,) + STD_HEIGHTS if plan.height < h <= ceiling]
    return max(options) if options else None


# ---------------------------------------------------------------------------
# ffmpeg çalıştırma
# ---------------------------------------------------------------------------

@dataclass
class Progress:
    fraction: float      # 0.0 - 1.0
    speed: float         # gerçek zamana göre kat (ffmpeg "speed=")
    eta: float           # kalan saniye tahmini
    stage: str           # kullanıcıya gösterilecek aşama metni


ProgressCb = Optional[Callable[[Progress], None]]
LogCb = Optional[Callable[[str], None]]


def run_ffmpeg(args: Sequence[str], duration: float, stage: str,
               on_progress: ProgressCb = None,
               cancel: Optional[threading.Event] = None,
               base: float = 0.0, span: float = 1.0,
               on_log: LogCb = None) -> None:
    """ffmpeg'i çalıştırır; -progress akışını okuyarak ilerleme bildirir."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise CompressError("ffmpeg bulunamadı.")

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats", *args]
    if on_log:
        on_log("$ " + " ".join(cmd))

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, **_popen_kwargs())

    err_tail: collections.deque[str] = collections.deque(maxlen=40)

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                err_tail.append(line)

    reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()

    cancelled = False
    speed = 0.0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            line = line.strip()
            if line.startswith("speed="):
                try:
                    speed = float(line[6:].rstrip("x").strip() or 0)
                except ValueError:
                    speed = 0.0
            elif line.startswith("out_time="):
                match = re.match(r"out_time=(-?\d+):(\d\d):(\d\d(?:\.\d+)?)", line)
                if match and duration > 0:
                    done = (int(match.group(1)) * 3600 + int(match.group(2)) * 60
                            + float(match.group(3)))
                    frac = min(max(done / duration, 0.0), 1.0)
                    remaining = (duration - done) / speed if speed > 0.01 else 0.0
                    if on_progress:
                        on_progress(Progress(base + frac * span, speed, remaining, stage))
    finally:
        if cancelled:
            _terminate(proc)
        proc.wait()
        reader.join(timeout=2)

    if cancelled:
        raise CompressError("__CANCELLED__")
    if proc.returncode != 0:
        detail = "\n".join(list(err_tail)[-6:]) or f"ffmpeg çıkış kodu {proc.returncode}"
        raise CompressError(f"Kodlama başarısız oldu:\n{detail}")


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def _video_filters(plan: Plan, info: MediaInfo) -> str:
    """Her zaman sabit kare hızı uygula: ekran kayıtları sık sık VFR olur."""
    chain = [f"fps={plan.fps:g}"]
    if plan.scaled:
        chain.append(f"scale=-2:{plan.height}:flags=lanczos")
    chain.append("format=yuv420p")
    return ",".join(chain)


def _audio_args(plan: Plan, info: MediaInfo) -> list[str]:
    if plan.audio_bps <= 0:
        return ["-an"]
    args = ["-c:a", "aac", "-b:a", f"{plan.audio_bps // 1000}k", "-ar", "48000"]
    if info.channels > 2:
        args += ["-ac", "2"]
    return args


def _common_video_args(plan: Plan, info: MediaInfo, preset: str,
                       map_audio: bool = True) -> list[str]:
    keyint = max(2, int(round(plan.fps * 4)))   # ~4 sn: Jira'da ileri/geri sarma rahat
    return [
        "-map", "0:v:0",
        *(["-map", "0:a:0?"] if (map_audio and plan.audio_bps > 0) else []),
        "-dn", "-sn", "-map_metadata", "-1", "-map_chapters", "-1",
        "-vf", _video_filters(plan, info),
        "-c:v", "libx264", "-preset", preset,
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(keyint), "-keyint_min", str(max(1, keyint // 4)),
        "-threads", "0",
    ]


def encode_capped_crf(info: MediaInfo, plan: Plan, preset: str, out: Path,
                      on_progress: ProgressCb, cancel, on_log: LogCb) -> None:
    """Tek geçiş: kaliteyi CRF belirler, boyut tavanını VBV uygular."""
    args = ["-i", str(info.path), *_common_video_args(plan, info, preset),
            "-crf", str(plan.crf),
            "-maxrate", str(plan.video_bps), "-bufsize", str(plan.video_bps * 2),
            *_audio_args(plan, info),
            "-movflags", "+faststart", str(out)]
    run_ffmpeg(args, info.duration, "Sıkıştırılıyor", on_progress, cancel,
               0.0, 1.0, on_log)


def encode_two_pass(info: MediaInfo, plan: Plan, preset: str, out: Path,
                    video_bps: int, on_progress: ProgressCb, cancel,
                    on_log: LogCb) -> None:
    """İki geçişli ABR: hedef boyuta isabet için."""
    workdir = Path(tempfile.mkdtemp(prefix="vsk-"))
    passlog = workdir / "x264"
    try:
        rate = ["-b:v", str(video_bps),
                "-maxrate", str(int(video_bps * 1.5)),
                "-bufsize", str(int(video_bps * 3))]

        # 1. geçiş yalnızca istatistik toplar: ses hiç eşlenmez.
        analysis = _common_video_args(plan, info, preset, map_audio=False)
        base = _common_video_args(plan, info, preset)

        run_ffmpeg(["-i", str(info.path), *analysis, *rate,
                    "-pass", "1", "-passlogfile", str(passlog),
                    "-an", "-f", "null", os.devnull],
                   info.duration, "1/2. geçiş (analiz)", on_progress, cancel,
                   0.0, 0.5, on_log)

        run_ffmpeg(["-i", str(info.path), *base, *rate,
                    "-pass", "2", "-passlogfile", str(passlog),
                    *_audio_args(plan, info),
                    "-movflags", "+faststart", str(out)],
                   info.duration, "2/2. geçiş (kodlama)", on_progress, cancel,
                   0.5, 0.5, on_log)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# MP4 kapsayıcısının sorunsuz taşıdığı ses kodekleri — bunlar kopyalanabilir.
MP4_SAFE_AUDIO = ("aac", "mp3", "ac3", "eac3", "alac")


def remux_copy(info: MediaInfo, out: Path, keep_audio: bool,
               on_progress: ProgressCb, cancel, on_log: LogCb) -> None:
    """Zaten sınırın altındaysa yeniden kodlamadan MP4'e al (kalite kaybı yok).

    Görüntü her zaman kopyalanır. Ses de MP4'ün taşıyabildiği bir kodekteyse
    kopyalanır; değilse (örn. webm içindeki Opus) yalnızca ses dönüştürülür.
    """
    with_audio = keep_audio and info.has_audio
    if not with_audio:
        audio = ["-an"]
    elif info.acodec in MP4_SAFE_AUDIO:
        audio = ["-c:a", "copy"]
    else:
        audio = ["-c:a", "aac", "-b:a", "128k"]
        if on_log:
            on_log(f"Ses {info.acodec} → aac (MP4 için); görüntüye dokunulmuyor.")

    args = ["-i", str(info.path), "-map", "0:v:0",
            *(["-map", "0:a:0?"] if with_audio else []),
            "-dn", "-sn", "-c:v", "copy", *audio,
            "-movflags", "+faststart", str(out)]
    run_ffmpeg(args, info.duration, "Kopyalanıyor (kayıpsız)", on_progress, cancel,
               0.0, 1.0, on_log)


# ---------------------------------------------------------------------------
# Çıktı adlandırma
# ---------------------------------------------------------------------------

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_stem(stem: str) -> str:
    cleaned = _ILLEGAL.sub("", stem).strip().strip(".")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if len(cleaned) > 70:
        cleaned = cleaned[:70].rstrip("_")
    return cleaned or "video"


def build_output_name(stem: str, size: int, plan: Optional[Plan]) -> str:
    """Örn: login_hatasi_sikistirilmis_720p_17.4MB_20261013-2318.mp4

    Klasörden bağımsızdır: kullanıcı kaydederken yeri de adı da değiştirebilir,
    bu yalnızca önerilen addır.
    """
    parts = [safe_stem(stem), "sikistirilmis"]
    if plan is not None and plan.scaled:
        parts.append(f"{plan.height}p")
    parts.append(f"{size / MB:.1f}MB")
    parts.append(datetime.now().strftime("%Y%m%d-%H%M"))
    return "_".join(parts) + ".mp4"


def unique_path(folder: Path, name: str) -> Path:
    """Klasörde var olan dosyanın üzerine yazmamak için _2, _3 … ekler."""
    stem, _, suffix = name.rpartition(".")
    candidate = folder / name
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{counter}.{suffix}"
        counter += 1
    return candidate


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

MISSING_FFMPEG = (
    "ffmpeg bulunamadı.\n\n"
    "Bu uygulamanın hazır sürümünde ffmpeg gömülüdür; betiği doğrudan "
    "çalıştırıyorsanız kurmanız gerekir:\n"
    "  • Windows : winget install Gyan.FFmpeg\n"
    "  • macOS   : brew install ffmpeg\n"
    "  • Linux   : sudo apt install ffmpeg\n\n"
    "Alternatif olarak ffmpeg ve ffprobe dosyalarını uygulamanın yanındaki "
    "'bin' klasörüne koyabilirsiniz."
)


@dataclass
class Result:
    """Sıkıştırma sonucu.

    Dosya henüz kalıcı yerine taşınmamıştır: `temp_path` geçici klasördedir.
    Nereye kaydedileceğine çağıran karar verir — `save_result()` ile taşıyın
    ya da `discard_result()` ile atın. Böylece kayıt yeri tamamen kullanıcıya
    bırakılabilir (farklı kaydet).
    """
    src: Path
    temp_path: Path
    suggested_name: str
    in_size: int
    out_size: int
    attempts: int
    elapsed: float
    plan: Optional[Plan]
    lossless: bool
    workdir: Path
    saved_to: Optional[Path] = None

    def summary(self) -> str:
        if self.lossless:
            return (f"Zaten sınırın altındaydı, kayıpsız kopyalandı: "
                    f"{human_size(self.out_size)}")
        saved = 100 - (self.out_size * 100 / self.in_size) if self.in_size else 0
        return (f"{human_size(self.in_size)} → {human_size(self.out_size)} "
                f"(%{saved:.0f} küçüldü, {human_time(self.elapsed)})")


def _is_cancel(exc: CompressError) -> bool:
    return bool(exc.args) and exc.args[0] == "__CANCELLED__"


def compress_one(src: Path, target_bytes: int, *,
                 keep_audio: bool = True, preset: str = "medium",
                 max_height: Optional[int] = None, min_bpp: float = MIN_BPP,
                 workdir: Optional[Path] = None,
                 on_progress: ProgressCb = None, on_log: LogCb = None,
                 cancel: Optional[threading.Event] = None) -> Result:
    """Videoyu sıkıştırır ve sonucu GEÇİCİ bir klasörde bırakır.

    Dosyayı kalıcı yerine taşımak çağıranın işidir: `save_result()` ya da
    `discard_result()` çağrılmalıdır; aksi hâlde geçici klasör diskte kalır.
    """
    def log(message: str) -> None:
        if on_log:
            on_log(message)

    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        raise CompressError(MISSING_FFMPEG)

    src = Path(src)
    if not src.is_file():
        raise CompressError(f"Dosya bulunamadı: {src}")

    started = time.monotonic()
    info = probe(src, ffprobe)
    log(f"Kaynak: {info.label}")

    work = Path(tempfile.mkdtemp(prefix="vsk-", dir=str(workdir) if workdir else None))
    tmp = work / "sonuc.mp4"          # kabul edilen sonuç
    tmp_alt = work / "deneme.mp4"     # daha iyi çözünürlük denemesi

    def finalize(plan: Optional[Plan], attempts: int, lossless: bool) -> Result:
        size = tmp.stat().st_size
        return Result(src=src, temp_path=tmp,
                      suggested_name=build_output_name(src.stem, size, plan),
                      in_size=info.size, out_size=size, attempts=attempts,
                      elapsed=time.monotonic() - started, plan=plan,
                      lossless=lossless, workdir=work)

    try:
        # 1) Zaten sığıyorsa yeniden kodlamaya hiç girmeyelim.
        if info.size <= target_bytes and info.vcodec == "h264":
            log("Dosya hedefin altında ve H.264 — kayıpsız kopyalanıyor.")
            try:
                remux_copy(info, tmp, keep_audio, on_progress, cancel, on_log)
                if tmp.stat().st_size <= target_bytes:
                    return finalize(None, 0, lossless=True)
                log("Kopya beklenenden büyük çıktı, normal sıkıştırmaya geçiliyor.")
            except CompressError as exc:
                if _is_cancel(exc):
                    raise
                log("Kayıpsız kopyalama olmadı, normal sıkıştırmaya geçiliyor.")

        plan = make_plan(info, target_bytes, keep_audio, max_height, min_bpp)
        log(f"Plan: {plan.describe()}")
        if plan.scaled:
            log(f"Çözünürlük {info.height}p → {plan.height}p "
                "(bit bütçesi bu boyutta yetmiyordu).")
        if plan.fps_dropped:
            log(f"Kare hızı {info.fps:g} → {plan.fps:g} fps.")
        if plan.warning:
            log("UYARI: " + plan.warning)

        # 2) Tek geçiş capped-CRF: kaliteyi CRF, boyut tavanını VBV belirler.
        encode_capped_crf(info, plan, preset, tmp, on_progress, cancel, on_log)
        size = tmp.stat().st_size
        attempts = 1
        log(f"{attempts}. deneme: {plan.height}p → {human_size(size)}")

        if size <= target_bytes:
            # 3a) Bütçede çok yer kaldıysa çözünürlük boşa düşürülmüş demektir.
            #     Tahminle değil ölçümle düzelt: daha büyüğünü dene, sığarsa al.
            previous: Optional[tuple[int, int]] = None
            while (attempts < 4 and plan.scaled
                   and size <= target_bytes * UPSCALE_TRIGGER):
                taller = next_height_up(info, plan, size, target_bytes, max_height,
                                        previous)
                if taller is None:
                    break
                trial = plan_at_height(info, plan, taller)
                log(f"Bütçenin %{size * 100 / target_bytes:.0f} kadarı kullanıldı; "
                    f"{trial.height}p deneniyor.")
                attempts += 1
                try:
                    encode_capped_crf(info, trial, preset, tmp_alt,
                                      on_progress, cancel, on_log)
                except CompressError as exc:
                    if _is_cancel(exc):
                        raise
                    log(f"   {trial.height}p denenemedi, {plan.height}p korunuyor.")
                    break
                trial_size = tmp_alt.stat().st_size
                log(f"{attempts}. deneme: {trial.height}p → {human_size(trial_size)}")
                if trial_size > target_bytes:
                    log(f"   Sığmadı, {plan.height}p sonucu korunuyor.")
                    break
                os.replace(str(tmp_alt), str(tmp))
                previous = (plan.height, size)
                plan, size = trial, trial_size
            return finalize(plan, attempts, lossless=False)

        # 3b) Hedef aşıldı: iki geçişli ABR ile boyuta isabet et.
        bps = max(ABS_MIN_VIDEO_BPS, int(plan.video_bps * target_bytes / size * 0.97))
        attempts += 1
        log(f"Hedef aşıldı, iki geçişli kodlama ({bps // 1000} kbps).")
        encode_two_pass(info, plan, preset, tmp, bps, on_progress, cancel, on_log)
        size = tmp.stat().st_size
        log(f"{attempts}. deneme: {human_size(size)}")
        if size <= target_bytes:
            return finalize(plan, attempts, lossless=False)

        bps = max(ABS_MIN_VIDEO_BPS, int(bps * target_bytes / size * 0.95))
        attempts += 1
        log(f"Son deneme ({bps // 1000} kbps).")
        encode_two_pass(info, plan, preset, tmp, bps, on_progress, cancel, on_log)
        size = tmp.stat().st_size
        log(f"{attempts}. deneme: {human_size(size)}")
        if size <= target_bytes:
            return finalize(plan, attempts, lossless=False)

        raise CompressError(
            f"Hedef boyuta indirilemedi (en iyi sonuç {human_size(size)}).\n"
            "Videoyu kısaltmayı veya çözünürlük sınırını düşürmeyi deneyin."
        )
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def save_result(result: Result, destination: Path) -> Path:
    """Sıkıştırılan dosyayı kullanıcının seçtiği yere taşır.

    `destination` bir klasörse önerilen ad kullanılır (var olanın üzerine
    yazılmaz, _2 eklenir). Tam dosya yoluysa aynen o yola kaydedilir — farklı
    kaydet penceresi üzerine yazmayı zaten kullanıcıya sormuştur.
    """
    destination = Path(destination).expanduser()
    if destination.is_dir():
        target = unique_path(destination, result.suggested_name)
    else:
        target = destination
        if target.suffix.lower() != ".mp4":
            target = target.with_suffix(".mp4")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CompressError(f"Klasör oluşturulamadı: {target.parent}\n{exc}")

    try:
        # Geçici klasör başka bir disk bölümünde olabilir; move kopyalamaya düşer.
        shutil.move(str(result.temp_path), str(target))
    except OSError as exc:
        raise CompressError(f"Dosya kaydedilemedi: {target}\n{exc}")

    try:
        os.chmod(str(target), 0o644)
    except OSError:
        pass

    shutil.rmtree(result.workdir, ignore_errors=True)
    result.saved_to = target
    return target


def discard_result(result: Result) -> None:
    """Kaydedilmeyen sonucu ve geçici klasörünü siler."""
    shutil.rmtree(result.workdir, ignore_errors=True)


def compress_to(src: Path, outdir: Path, target_bytes: int, **kwargs) -> Result:
    """Sıkıştır ve doğrudan verilen klasöre kaydet (komut satırı için kolaylık)."""
    result = compress_one(src, target_bytes, **kwargs)
    try:
        save_result(result, Path(outdir))
    except BaseException:
        discard_result(result)
        raise
    return result


# ---------------------------------------------------------------------------
# ffmpeg yedek indirici (yalnızca kullanıcı açıkça isterse)
# ---------------------------------------------------------------------------

def ffmpeg_download_url() -> Optional[str]:
    base = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if IS_WIN and not arm:
        return base + "ffmpeg-master-latest-win64-gpl.zip"
    if not IS_WIN and not IS_MAC:
        return base + ("ffmpeg-master-latest-linuxarm64-gpl.tar.xz" if arm
                       else "ffmpeg-master-latest-linux64-gpl.tar.xz")
    return None  # macOS: "brew install ffmpeg" daha güvenilir


def download_ffmpeg(on_log: LogCb = None) -> bool:
    """ffmpeg/ffprobe'u önbellek klasörüne indirir. Ağ erişimi gerekir."""
    import tarfile
    import urllib.request
    import zipfile

    url = ffmpeg_download_url()
    if not url:
        raise CompressError("Bu platform için otomatik indirme yok.\n"
                            "macOS: brew install ffmpeg")
    if on_log:
        on_log(f"İndiriliyor: {url}")

    target = cache_dir() / "bin"
    target.mkdir(parents=True, exist_ok=True)
    wanted = {_exe("ffmpeg"), _exe("ffprobe")}

    with tempfile.TemporaryDirectory(prefix="vsk-dl-") as tmpdir:
        archive = Path(tmpdir) / Path(url).name
        with urllib.request.urlopen(url, timeout=120) as response, \
                open(archive, "wb") as handle:
            shutil.copyfileobj(response, handle)

        extracted = 0
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    if Path(member).name in wanted:
                        with zf.open(member) as src, open(target / Path(member).name, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        extracted += 1
        else:
            with tarfile.open(archive) as tf:
                for member in tf.getmembers():
                    if member.isfile() and Path(member.name).name in wanted:
                        stream = tf.extractfile(member)
                        if stream is None:
                            continue
                        with stream, open(target / Path(member.name).name, "wb") as dst:
                            shutil.copyfileobj(stream, dst)
                        extracted += 1

    for name in wanted:
        path = target / name
        if path.exists():
            path.chmod(0o755)
    _TOOL_CACHE.clear()
    return extracted >= 2 and bool(find_tool("ffmpeg") and find_tool("ffprobe"))


# ---------------------------------------------------------------------------
# Komut satırı
# ---------------------------------------------------------------------------

def expand_inputs(paths: Sequence[str]) -> list[Path]:
    """Dosya veya klasör yollarını video dosyası listesine çevirir."""
    out: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            out.extend(sorted(p for p in path.iterdir()
                              if p.is_file() and p.suffix.lower() in VIDEO_EXTS))
        elif path.is_file():
            out.append(path)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def run_cli(args: argparse.Namespace) -> int:
    files = expand_inputs(args.files)
    if not files:
        print("Sıkıştırılacak video bulunamadı.", file=sys.stderr)
        return 2

    outdir = Path(args.outdir).expanduser() if args.outdir else videos_dir()
    target = int(args.target_mb * MB)
    preset = PRESETS.get(args.kalite, ("medium", ""))[0]
    max_height = None if args.max_height in (0, None) else int(args.max_height)

    print(f"Çıktı klasörü : {outdir}")
    print(f"Hedef boyut   : {human_size(target)} (Jira sınırı {human_size(JIRA_LIMIT_BYTES)})")

    last = {"text": ""}

    def show(progress: Progress) -> None:
        bar_len = 28
        filled = int(progress.fraction * bar_len)
        eta = f" kalan ~{human_time(progress.eta)}" if progress.eta > 1 else ""
        text = (f"\r  [{'#' * filled}{'.' * (bar_len - filled)}] "
                f"{progress.fraction * 100:5.1f}%  {progress.stage}"
                f"  {progress.speed:.1f}x{eta}   ")
        if text != last["text"]:
            sys.stdout.write(text)
            sys.stdout.flush()
            last["text"] = text

    failures = 0
    for index, path in enumerate(files, 1):
        print(f"\n[{index}/{len(files)}] {path.name}")
        try:
            result = compress_to(
                path, outdir, target, keep_audio=not args.no_audio,
                preset=preset, max_height=max_height,
                min_bpp=0.0 if args.orijinal_cozunurluk else MIN_BPP,
                on_progress=show,
                on_log=(lambda m: print("  " + m)) if args.verbose else (lambda m: None),
            )
            sys.stdout.write("\r" + " " * 78 + "\r")
            print(f"  ✓ {result.saved_to.name}")
            print(f"    {result.summary()}")
        except CompressError as exc:
            sys.stdout.write("\r" + " " * 78 + "\r")
            print(f"  ✗ {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_compressor",
        description=f"{APP_NAME} {APP_VERSION} — videoyu Jira ek dosya sınırının "
                    "altına indirir ve Videolar klasörüne kaydeder.",
    )
    parser.add_argument("files", nargs="*", help="video dosyaları veya klasörler")
    parser.add_argument("--target-mb", type=float, default=DEFAULT_TARGET_MB,
                        metavar="MB", help=f"hedef boyut (varsayılan {DEFAULT_TARGET_MB})")
    parser.add_argument("--outdir", help="çıktı klasörü (varsayılan: sistem Videolar)")
    parser.add_argument("--kalite", choices=tuple(PRESETS), default="dengeli",
                        help="hız/kalite dengesi (varsayılan dengeli)")
    parser.add_argument("--max-height", type=int, default=0, metavar="PX",
                        help="en fazla çözünürlük yüksekliği, 0 = otomatik")
    parser.add_argument("--orijinal-cozunurluk", action="store_true",
                        help="çözünürlüğü asla düşürme (boyut için kaliteden ödün verir)")
    parser.add_argument("--no-audio", action="store_true", help="sesi at")
    parser.add_argument("--no-gui", action="store_true", help="arayüzü hiç açma")
    parser.add_argument("--verbose", action="store_true", help="ffmpeg komutlarını yazdır")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    # PyInstaller --windowed paketinde stdout/stderr None olur; print() çökmesin.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    args = build_parser().parse_args(argv)
    if args.no_gui:
        return run_cli(args)
    if args.files and not sys.stdout.isatty():
        # Dosya uygulamanın üzerine bırakıldı → arayüzü kuyruk dolu aç.
        return run_gui(expand_inputs(args.files), args)
    if args.files:
        return run_cli(args)
    return run_gui([], args)



# ---------------------------------------------------------------------------
# Grafik arayüz (Tkinter — standart kütüphane, ek kurulum yok)
# ---------------------------------------------------------------------------

RES_CHOICES = (
    ("Otomatik (önerilen)", None, MIN_BPP),
    ("Orijinali koru", None, 0.0),
    ("En fazla 1080p", 1080, MIN_BPP),
    ("En fazla 720p", 720, MIN_BPP),
    ("En fazla 480p", 480, MIN_BPP),
)


def _mono_font() -> tuple:
    if IS_WIN:
        return ("Consolas", 9)
    if IS_MAC:
        return ("Menlo", 11)
    return ("monospace", 9)


class CompressorApp:
    def __init__(self, root, initial_files: Sequence[Path], defaults) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.cancel = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.rows: dict[str, Path] = {}

        # Kayıt yeri tamamen kullanıcıya ait: varsayılan davranış her videodan
        # sonra "farklı kaydet" penceresi açmak. Pencerenin başlangıç klasörü
        # sistemin Videolar klasörüdür; son kaydedilen yer hatırlanır.
        start_dir = Path(defaults.outdir).expanduser() if defaults.outdir else videos_dir()
        self.fixed_dir = start_dir
        self.last_dir = start_dir
        self.last_output: Optional[Path] = None
        self._pending_dialogs: list[threading.Event] = []
        self._dialog_lock = threading.Lock()
        self._pump_id: Optional[str] = None
        self._closing = False

        root.title(f"{APP_NAME} {APP_VERSION}")
        # 1366x768 gibi yaygın dizüstü ekranlarında alt sıradaki düğmeler
        # görev çubuğunun altında kalmasın diye pencere derli toplu tutulur.
        root.minsize(720, 520)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=3)
        root.rowconfigure(5, weight=2)

        self._build_header()
        self._build_queue()
        self._build_options(defaults)
        self._build_destination()
        self._build_progress()
        self._build_log()
        self._build_actions()

        for path in initial_files:
            self.add_path(path)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._pump_id = self.root.after(80, self.pump)
        self.check_ffmpeg()

    # -- arayüz kurulumu ---------------------------------------------------

    def _build_header(self) -> None:
        tk, ttk = self.tk, self.ttk
        frame = ttk.Frame(self.root, padding=(12, 8, 12, 2))
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Hata kaydı videolarını Jira'ya yüklenebilir boyuta indirir.",
                  font=("", 10)).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Sıkıştırma bitince dosyayı nereye kaydedeceğinizi siz "
                              "seçersiniz.", foreground="#666666").grid(
            row=1, column=0, sticky="w", pady=(2, 0))

    def _build_queue(self) -> None:
        ttk = self.ttk
        frame = ttk.LabelFrame(self.root, text="Videolar", padding=6)
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=2)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(frame, columns=("bilgi", "durum"), height=4)
        self.tree.heading("#0", text="Dosya", anchor="w")
        self.tree.heading("bilgi", text="Özellikler", anchor="w")
        self.tree.heading("durum", text="Durum", anchor="w")
        self.tree.column("#0", width=230, anchor="w")
        self.tree.column("bilgi", width=290, anchor="w")
        self.tree.column("durum", width=150, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

        bar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Video Ekle…", command=self.pick_files).pack(side="left")
        ttk.Button(buttons, text="Klasör Ekle…", command=self.pick_folder).pack(side="left", padx=6)
        ttk.Button(buttons, text="Seçiliyi Kaldır", command=self.remove_selected).pack(side="left")
        ttk.Button(buttons, text="Listeyi Temizle", command=self.clear_queue).pack(side="left", padx=6)

    def _build_options(self, defaults) -> None:
        tk, ttk = self.tk, self.ttk
        frame = ttk.LabelFrame(self.root, text="Ayarlar", padding=6)
        frame.grid(row=2, column=0, sticky="ew", padx=12, pady=2)

        ttk.Label(frame, text="Hedef boyut (MB):").grid(row=0, column=0, sticky="w")
        self.target_var = tk.StringVar(value=f"{defaults.target_mb:.1f}")
        ttk.Spinbox(frame, from_=1.0, to=2000.0, increment=0.5, width=8,
                    textvariable=self.target_var).grid(row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(frame, text=f"Jira sınırı 19.07 MB = {JIRA_LIMIT_BYTES:,} bayt"
                              .replace(",", "."),
                  foreground="#666666").grid(row=0, column=2, columnspan=3, sticky="w")

        ttk.Label(frame, text="Hız / kalite:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.preset_var = tk.StringVar(value=PRESETS[defaults.kalite][1])
        ttk.Combobox(frame, textvariable=self.preset_var, state="readonly", width=20,
                     values=[label for _, label in PRESETS.values()]).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(6, 16), pady=(8, 0))

        ttk.Label(frame, text="Çözünürlük:").grid(row=1, column=3, sticky="w", pady=(8, 0))
        self.res_var = tk.StringVar(value=RES_CHOICES[0][0])
        ttk.Combobox(frame, textvariable=self.res_var, state="readonly", width=20,
                     values=[name for name, _, _ in RES_CHOICES]).grid(
            row=1, column=4, sticky="w", padx=6, pady=(8, 0))

        self.audio_var = tk.BooleanVar(value=not defaults.no_audio)
        ttk.Checkbutton(frame, text="Sesi koru", variable=self.audio_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_destination(self) -> None:
        tk, ttk = self.tk, self.ttk
        frame = ttk.LabelFrame(self.root, text="Kayıt yeri", padding=6)
        frame.grid(row=3, column=0, sticky="ew", padx=12, pady=2)
        frame.columnconfigure(1, weight=1)

        self.ask_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(frame, variable=self.ask_var, value=True,
                        text="Her videodan sonra sor (farklı kaydet…)",
                        command=self._sync_destination).grid(
            row=0, column=0, columnspan=3, sticky="w")

        ttk.Radiobutton(frame, variable=self.ask_var, value=False,
                        text="Doğrudan şu klasöre kaydet:",
                        command=self._sync_destination).grid(row=1, column=0, sticky="w",
                                                             pady=(4, 0))
        self.fixed_var = tk.StringVar(value=str(self.fixed_dir))
        self.fixed_entry = ttk.Entry(frame, textvariable=self.fixed_var, state="readonly")
        self.fixed_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(4, 0))
        self.fixed_btn = ttk.Button(frame, text="Değiştir…", command=self.pick_fixed_dir)
        self.fixed_btn.grid(row=1, column=2, sticky="e", pady=(4, 0))

        self._sync_destination()

    def _sync_destination(self) -> None:
        """Sabit klasör alanı yalnızca o seçenek işaretliyken etkin olsun."""
        state = "disabled" if self.ask_var.get() else "normal"
        self.fixed_btn.configure(state=state)
        self.fixed_entry.configure(state="readonly" if state == "normal" else "disabled")

    def pick_fixed_dir(self) -> None:
        from tkinter import filedialog
        folder = filedialog.askdirectory(parent=self.root, title="Kayıt klasörünü seçin",
                                         initialdir=str(self.fixed_dir))
        if folder:
            self.fixed_dir = Path(folder)
            self.fixed_var.set(folder)

    def _open_save_dialog(self, suggested: str) -> Optional[str]:
        """Farklı kaydet penceresi — yalnızca ana iş parçacığından çağrılır."""
        from tkinter import filedialog
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Sıkıştırılmış videoyu kaydet",
            initialfile=suggested,
            initialdir=str(self.last_dir),
            defaultextension=".mp4",
            filetypes=[("MP4 videosu", "*.mp4"), ("Tüm dosyalar", "*.*")],
        )
        return chosen or None

    def request_destination(self, suggested: str) -> Optional[str]:
        """İşçi iş parçacığından: pencereyi ana iş parçacığına açtır ve bekle."""
        reply: dict = {}
        done = threading.Event()
        with self._dialog_lock:
            self._pending_dialogs.append(done)
        self.events.put({"t": "ask_save", "suggested": suggested,
                         "reply": reply, "done": done})
        done.wait()
        with self._dialog_lock:
            if done in self._pending_dialogs:
                self._pending_dialogs.remove(done)
        return reply.get("path")

    def _build_progress(self) -> None:
        tk, ttk = self.tk, self.ttk
        frame = ttk.Frame(self.root, padding=(12, 2))
        frame.grid(row=4, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1000)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="Hazır.")
        ttk.Label(frame, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(3, 0))

    def _build_log(self) -> None:
        ttk = self.ttk
        from tkinter import scrolledtext
        frame = ttk.LabelFrame(self.root, text="Günlük", padding=5)
        frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=2)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log_box = scrolledtext.ScrolledText(frame, height=4, wrap="word",
                                                 font=_mono_font(), state="disabled")
        self.log_box.grid(row=0, column=0, sticky="nsew")

    def _build_actions(self) -> None:
        ttk = self.ttk
        frame = ttk.Frame(self.root, padding=(12, 4, 12, 8))
        frame.grid(row=6, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)

        self.start_btn = ttk.Button(frame, text="Sıkıştır", command=self.start)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.cancel_btn = ttk.Button(frame, text="İptal", command=self.request_cancel,
                                     state="disabled")
        self.cancel_btn.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="Kaydedilen Klasörü Aç",
                   command=lambda: open_in_file_manager(
                       self.last_output or self.last_dir)).grid(row=0, column=2, sticky="e")

    # -- yardımcılar -------------------------------------------------------

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message.rstrip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def check_ffmpeg(self) -> None:
        if find_tool("ffmpeg") and find_tool("ffprobe"):
            self.log(f"ffmpeg hazır: {find_tool('ffmpeg')}")
            return
        from tkinter import messagebox
        self.log("ffmpeg bulunamadı.")
        if ffmpeg_download_url() and messagebox.askyesno(
                APP_NAME,
                MISSING_FFMPEG + "\n\nffmpeg'i şimdi indirmemi ister misiniz?\n"
                f"Kaynak: {ffmpeg_download_url()}\n"
                f"Hedef : {cache_dir() / 'bin'}"):
            self.status_var.set("ffmpeg indiriliyor…")
            threading.Thread(target=self._download_worker, daemon=True).start()
        else:
            messagebox.showwarning(APP_NAME, MISSING_FFMPEG)

    def _download_worker(self) -> None:
        try:
            ok = download_ffmpeg(lambda m: self.events.put({"t": "log", "msg": m}))
            self.events.put({"t": "log", "msg": "ffmpeg hazır." if ok
                             else "ffmpeg indirilemedi."})
        except Exception as exc:
            self.events.put({"t": "log", "msg": f"ffmpeg indirilemedi: {exc}"})
        self.events.put({"t": "status", "msg": "Hazır."})

    def add_path(self, path: Path) -> None:
        resolved = str(Path(path).resolve())
        if any(str(p.resolve()) == resolved for p in self.rows.values()):
            return
        info_text = "—"
        ffprobe = find_tool("ffprobe")
        if ffprobe:
            try:
                info_text = probe(Path(path), ffprobe).label
            except CompressError:
                info_text = "okunamadı"
        iid = self.tree.insert("", "end", text=Path(path).name,
                               values=(info_text, "bekliyor"))
        self.rows[iid] = Path(path)

    def pick_files(self) -> None:
        from tkinter import filedialog
        chosen = filedialog.askopenfilenames(
            title="Sıkıştırılacak videoları seçin",
            filetypes=[("Video dosyaları", " ".join("*" + e for e in VIDEO_EXTS)),
                       ("Tüm dosyalar", "*.*")])
        for item in chosen:
            self.add_path(Path(item))

    def pick_folder(self) -> None:
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Video klasörü seçin")
        if folder:
            for item in expand_inputs([folder]):
                self.add_path(item)

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            self.rows.pop(iid, None)
            self.tree.delete(iid)

    def clear_queue(self) -> None:
        for iid in list(self.rows):
            self.tree.delete(iid)
        self.rows.clear()

    def read_settings(self) -> Optional[dict]:
        from tkinter import messagebox
        try:
            target_mb = float(self.target_var.get().replace(",", "."))
        except ValueError:
            messagebox.showerror(APP_NAME, "Hedef boyut sayı olmalı.")
            return None
        if not 0.3 <= target_mb <= 4000:
            messagebox.showerror(APP_NAME, "Hedef boyut 0.3 – 4000 MB arasında olmalı.")
            return None
        if target_mb * MB > JIRA_LIMIT_BYTES:
            if not messagebox.askyesno(
                    APP_NAME,
                    f"{target_mb:.1f} MB, Jira'nın {human_size(JIRA_LIMIT_BYTES)} "
                    "sınırının üzerinde. Yine de devam edilsin mi?"):
                return None

        preset = next((key for key, (_, label) in PRESETS.items()
                       if label == self.preset_var.get()), "dengeli")
        choice = next((c for c in RES_CHOICES if c[0] == self.res_var.get()), RES_CHOICES[0])
        ask_each = bool(self.ask_var.get())
        if not ask_each and not self.fixed_dir.parent.exists():
            messagebox.showerror(APP_NAME, f"Klasör bulunamadı:\n{self.fixed_dir}")
            return None

        return {
            "target": int(target_mb * MB),
            "preset": PRESETS[preset][0],
            "max_height": choice[1],
            "min_bpp": choice[2],
            "keep_audio": bool(self.audio_var.get()),
            "ask_each": ask_each,
            "fixed_dir": self.fixed_dir,
        }

    # -- çalıştırma --------------------------------------------------------

    def start(self) -> None:
        from tkinter import messagebox
        if self.worker and self.worker.is_alive():
            return
        if not self.rows:
            messagebox.showinfo(APP_NAME, "Önce en az bir video ekleyin.")
            return
        if not (find_tool("ffmpeg") and find_tool("ffprobe")):
            messagebox.showerror(APP_NAME, MISSING_FFMPEG)
            return
        settings = self.read_settings()
        if settings is None:
            return

        self.cancel.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        jobs = [(iid, path) for iid, path in self.rows.items()]
        for iid, _ in jobs:
            self.tree.set(iid, "durum", "bekliyor")
        where = ("her videodan sonra sorulacak" if settings["ask_each"]
                 else str(settings["fixed_dir"]))
        self.log("")
        self.log(f"=== {len(jobs)} video · hedef {human_size(settings['target'])} "
                 f"· kayıt: {where} ===")
        self.worker = threading.Thread(target=self._work, args=(jobs, settings), daemon=True)
        self.worker.start()

    def _work(self, jobs, settings) -> None:
        put = self.events.put
        ok = failed = 0
        for index, (iid, path) in enumerate(jobs, 1):
            if self.cancel.is_set():
                put({"t": "item", "iid": iid, "status": "iptal"})
                continue
            put({"t": "item", "iid": iid, "status": "çalışıyor"})
            put({"t": "log", "msg": f"[{index}/{len(jobs)}] {path.name}"})
            label = f"[{index}/{len(jobs)}] {path.name}"
            result = None
            try:
                result = compress_one(
                    path, settings["target"],
                    keep_audio=settings["keep_audio"], preset=settings["preset"],
                    max_height=settings["max_height"],
                    min_bpp=settings["min_bpp"],
                    on_progress=lambda p, lbl=label: put(
                        {"t": "prog", "frac": p.fraction, "speed": p.speed,
                         "eta": p.eta, "stage": f"{lbl} — {p.stage}"}),
                    on_log=lambda m: put({"t": "log", "msg": "   " + m}),
                    cancel=self.cancel,
                )
                put({"t": "log", "msg": f"     {result.summary()}"})

                # Nereye kaydedileceğine kullanıcı karar verir.
                if settings["ask_each"]:
                    put({"t": "item", "iid": iid, "status": "kayıt yeri bekleniyor"})
                    put({"t": "status", "msg": f"{path.name} — kayıt yerini seçin…"})
                    chosen = self.request_destination(result.suggested_name)
                    if not chosen:
                        put({"t": "item", "iid": iid, "status": "kaydedilmedi"})
                        put({"t": "log", "msg": "   Kaydetmekten vazgeçildi; "
                                                "sıkıştırılan dosya silindi."})
                        continue
                    destination = Path(chosen)
                else:
                    destination = settings["fixed_dir"]

                saved = save_result(result, destination)
                ok += 1
                put({"t": "item", "iid": iid,
                     "status": f"kaydedildi · {human_size(result.out_size)}"})
                put({"t": "log", "msg": f"   ✓ {saved}"})
                put({"t": "last", "path": saved})
            except CompressError as exc:
                if _is_cancel(exc):
                    put({"t": "item", "iid": iid, "status": "iptal"})
                    put({"t": "log", "msg": "   İptal edildi."})
                    break
                failed += 1
                put({"t": "item", "iid": iid, "status": "hata"})
                put({"t": "log", "msg": f"   ✗ {exc}"})
            except Exception as exc:  # beklenmeyen durumda arayüz kilitlenmesin
                failed += 1
                put({"t": "item", "iid": iid, "status": "hata"})
                put({"t": "log", "msg": f"   ✗ Beklenmeyen hata: {exc!r}"})
            finally:
                # Kaydedilmemiş her sonuç (vazgeçme, hata, çıkış) temizlenir.
                if result is not None and result.saved_to is None:
                    discard_result(result)
        put({"t": "finished", "ok": ok, "failed": failed,
             "cancelled": self.cancel.is_set()})

    def pump(self) -> None:
        """Arayüz iş parçacığı: olay kuyruğunu boşalt."""
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event["t"]
                if kind == "log":
                    self.log(event["msg"])
                elif kind == "status":
                    self.status_var.set(event["msg"])
                elif kind == "prog":
                    self.progress.configure(value=event["frac"] * 1000)
                    eta = f" · kalan ~{human_time(event['eta'])}" if event["eta"] > 1 else ""
                    self.status_var.set(f"%{event['frac'] * 100:.1f} — {event['stage']}"
                                        f" · {event['speed']:.1f}x{eta}")
                elif kind == "item":
                    if self.tree.exists(event["iid"]):
                        self.tree.set(event["iid"], "durum", event["status"])
                elif kind == "ask_save":
                    try:
                        event["reply"]["path"] = self._open_save_dialog(event["suggested"])
                    finally:
                        event["done"].set()
                elif kind == "last":
                    self.last_output = event["path"]
                    self.last_dir = event["path"].parent
                elif kind == "finished":
                    self._on_finished(event)
        except queue.Empty:
            pass
        if not self._closing:
            self._pump_id = self.root.after(80, self.pump)

    def _on_finished(self, event) -> None:
        self.start_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.progress.configure(value=0)
        if event["cancelled"]:
            text = "İptal edildi."
        elif event["failed"]:
            text = f"Bitti: {event['ok']} kaydedildi, {event['failed']} hatalı."
        elif self.last_output is not None:
            text = f"Bitti: {event['ok']} video kaydedildi → {self.last_output.parent}"
        else:
            text = "Bitti: hiçbir dosya kaydedilmedi."
        self.status_var.set(text)
        self.log(text)

    def request_cancel(self) -> None:
        self.cancel.set()
        self.status_var.set("İptal ediliyor…")
        self.cancel_btn.configure(state="disabled")

    def on_close(self) -> None:
        from tkinter import messagebox
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Sıkıştırma sürüyor. Çıkılsın mı?"):
                return
            self.cancel.set()
            # Kayıt yeri sorusunda bekleyen işçi varsa kilitlenmesin.
            with self._dialog_lock:
                pending = list(self._pending_dialogs)
                self._pending_dialogs.clear()
            for done in pending:
                done.set()
            self.worker.join(timeout=8)

        # Yok edilmiş pencereye zamanlayıcı kalmasın.
        self._closing = True
        if self._pump_id is not None:
            try:
                self.root.after_cancel(self._pump_id)
            except Exception:
                pass
        self.root.destroy()


def run_gui(initial_files: Sequence[Path], defaults) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        print(f"Grafik arayüz açılamadı ({exc}). Komut satırını kullanın: "
              "video_compressor --no-gui dosya.mp4", file=sys.stderr)
        return 3

    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    if not IS_MAC:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

    CompressorApp(root, initial_files, defaults)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
