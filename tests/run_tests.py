#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Video Sıkıştırıcı test paketi.

Çalıştırma:
    python3 tests/run_tests.py            # tümü (~2 dk)
    python3 tests/run_tests.py --hizli    # kodlama testlerini atla (~5 sn)

Test videolarını ffmpeg ile kendisi üretir; dışarıdan dosya gerekmez.
Hiçbir şey sistemin Videolar klasörüne yazılmaz — geçici klasör kullanılır.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video_compressor as vc  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> bool:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  HATA {name} — {detail}")
    return bool(condition)


def section(title: str) -> None:
    print(f"\n== {title} ==")


def top_level_atoms(path: Path) -> list[str]:
    """MP4 üst düzey atom adlarını sırayla döndürür (faststart denetimi için)."""
    import struct
    names: list[str] = []
    total = path.stat().st_size
    position = 0
    with open(path, "rb") as handle:
        while position < total and len(names) < 16:
            handle.seek(position)
            header = handle.read(8)
            if len(header) < 8:
                break
            size, raw = struct.unpack(">I4s", header)
            names.append(raw.decode("latin1"))
            if size == 1:                      # 64-bit uzunluk
                size = struct.unpack(">Q", handle.read(8))[0]
            elif size == 0:                    # dosya sonuna kadar
                break
            if size < 8:
                break
            position += size
    return names


def ffmpeg_make(path: Path, *args: str) -> None:
    subprocess.run([vc.find_tool("ffmpeg"), "-v", "error", "-y", *args, str(path)],
                   check=True)


# ---------------------------------------------------------------------------

def test_pure() -> None:
    section("yardımcı fonksiyonlar")
    check("yasak karakterler temizlenir",
          vc.safe_stem('a<>:"/\\|?*b') == "ab", vc.safe_stem('a<>:"/\\|?*b'))
    check("boşluklar alt çizgi olur", vc.safe_stem("iki  kelime") == "iki_kelime")
    check("boş ad yedeğe düşer", vc.safe_stem("...") == "video")
    check("ad 70 karakterle sınırlı", len(vc.safe_stem("x" * 300)) == 70)
    check("Türkçe karakterler korunur", vc.safe_stem("giriş_ekranı") == "giriş_ekranı")
    check("boyut biçimi", vc.human_size(19_000_000) == "19.0 MB")
    check("süre biçimi", vc.human_time(3725) == "1:02:05")
    check("Videolar klasörü bulunur ve mevcut", vc.videos_dir().is_dir(), vc.videos_dir())
    check("ffmpeg bulunur", bool(vc.find_tool("ffmpeg")))
    check("ffprobe bulunur", bool(vc.find_tool("ffprobe")))


def test_naming(tmp: Path) -> None:
    section("çıktı adlandırma")
    plan = vc.Plan(1280, 720, 30, 1_000_000, 96_000, 22, scaled=True, fps_dropped=False)
    name = vc.build_output_name("bug 4312", 17_400_000, plan)
    check("küçültülmüşte çözünürlük yazar", "_720p_" in name and "17.4MB" in name, name)
    check("uzantı mp4", name.endswith(".mp4"), name)

    plan_native = vc.Plan(1920, 1080, 30, 1_000_000, 96_000, 22,
                          scaled=False, fps_dropped=False)
    name2 = vc.build_output_name("bug", 1_000_000, plan_native)
    check("orijinal çözünürlükte etiket yok", "p_" not in name2, name2)

    (tmp / "ayni.mp4").touch()
    second = vc.unique_path(tmp, "ayni.mp4")
    check("var olan dosyanın üzerine yazmaz",
          second.name == "ayni_2.mp4" and not second.exists(), second.name)
    (tmp / "ayni.mp4").unlink()


def test_plan(fixtures: dict[str, Path]) -> None:
    section("bit bütçesi planı")
    ffprobe = vc.find_tool("ffprobe")
    info = vc.probe(fixtures["motion_1080p"], ffprobe)
    check("çözünürlük okundu", (info.width, info.height) == (1920, 1080), info.label)
    check("süre okundu", 29 < info.duration < 31, info.duration)
    check("ses algılandı", info.has_audio)

    tight = vc.make_plan(info, 2_000_000, True, None, vc.MIN_BPP)
    check("dar bütçe çözünürlüğü düşürür", tight.scaled, tight.describe())
    check("ses bütçenin dörtte birini aşmaz",
          tight.audio_bps <= (tight.audio_bps + tight.video_bps) * 0.25)

    roomy = vc.make_plan(info, 19_000_000, True, None, vc.MIN_BPP)
    check("bol bütçe orijinali korur", not roomy.scaled, roomy.describe())

    forced = vc.make_plan(info, 2_000_000, True, None, 0.0)
    check("min_bpp=0 asla küçültmez", not forced.scaled)

    capped = vc.make_plan(info, 19_000_000, True, 480, vc.MIN_BPP)
    check("üst sınır uygulanır", capped.height <= 480, capped.height)

    mute = vc.make_plan(info, 19_000_000, False, None, vc.MIN_BPP)
    check("sessizde tüm bütçe videoya gider",
          mute.audio_bps == 0 and mute.video_bps > roomy.video_bps)

    high_fps = vc.probe(fixtures["fps60"], ffprobe)
    check("60 fps → 30 fps", vc.make_plan(high_fps, 19_000_000, True, None,
                                          vc.MIN_BPP).fps == 30)

    try:
        vc.make_plan(info, 50_000, True, None, vc.MIN_BPP)
        check("imkansız hedef hata verir", False)
    except vc.CompressError as exc:
        check("imkansız hedef hata verir", "sığmıyor" in str(exc))


def test_height_search(fixtures: dict[str, Path]) -> None:
    section("ölçüme dayalı çözünürlük düzeltmesi")
    info = vc.probe(fixtures["motion_1080p"], vc.find_tool("ffprobe"))
    base = vc.make_plan(info, 19_000_000, True, None, vc.MIN_BPP)
    low = vc.plan_at_height(info, base, 288)

    check("bol yer varsa yükseltir",
          (vc.next_height_up(info, low, 600_000, 19_000_000, None) or 0) > 288)
    check("yer yoksa yükseltmez",
          vc.next_height_up(info, low, 19_000_000, 19_000_000, None) is None)
    check("üst sınır aşılmaz",
          (vc.next_height_up(info, low, 600_000, 19_000_000, 480) or 0) <= 480)
    check("orijinalin üstüne çıkmaz",
          vc.next_height_up(info, vc.plan_at_height(info, base, 1080),
                            1000, 19_000_000, None) is None)
    # Durağan içerik (ölçülen üs küçük) tek adımda çok daha yukarı çıkmalı.
    static_jump = vc.next_height_up(info, vc.plan_at_height(info, base, 600),
                                    6_000_000, 19_000_000, None, previous=(288, 5_500_000))
    linear_jump = vc.next_height_up(info, vc.plan_at_height(info, base, 600),
                                    6_000_000, 19_000_000, None)
    check("ölçülen üs daha cesur sıçrama sağlar",
          (static_jump or 0) > (linear_jump or 0), (static_jump, linear_jump))


def test_bad_input(tmp: Path) -> None:
    section("hatalı girdi")
    missing = tmp / "yok.mp4"
    try:
        vc.compress_one(missing, 19_000_000)
        check("olmayan dosya", False)
    except vc.CompressError as exc:
        check("olmayan dosya", "bulunamadı" in str(exc))

    broken = tmp / "bozuk.mp4"
    broken.write_bytes(b"video degil" * 100)
    try:
        vc.compress_one(broken, 19_000_000)
        check("bozuk dosya", False)
    except vc.CompressError as exc:
        check("bozuk dosya", "okunamadı" in str(exc) or "bulunamadı" in str(exc))

    audio_only = tmp / "sadece_ses.m4a"
    ffmpeg_make(audio_only, "-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac")
    try:
        vc.compress_one(audio_only, 19_000_000)
        check("video akışı olmayan dosya", False)
    except vc.CompressError as exc:
        check("video akışı olmayan dosya", "video akışı bulunamadı" in str(exc))


def test_encode(fixtures: dict[str, Path], tmp: Path) -> None:
    section("uçtan uca sıkıştırma")
    out = tmp / "cikti"
    out.mkdir(exist_ok=True)

    target = 2_000_000
    result = vc.compress_to(fixtures["motion_1080p"], out, target)
    check("hedefin altında kalır", result.out_size <= target,
          f"{result.out_size} > {target}")
    check("çıktı dosyası var", result.saved_to.is_file())
    check("adda sıkıştırılmış etiketi var", "sikistirilmis" in result.saved_to.name)
    check("geçici klasör temizlendi", not result.workdir.exists())
    probe_out = vc.probe(result.saved_to, vc.find_tool("ffprobe"))
    check("çıktı H.264", probe_out.vcodec == "h264", probe_out.vcodec)
    check("süre korundu", abs(probe_out.duration - 30) < 1.0, probe_out.duration)
    decode = subprocess.run([vc.find_tool("ffmpeg"), "-v", "error", "-i",
                             str(result.saved_to), "-f", "null", "-"],
                            capture_output=True, text=True)
    check("çıktı tam çözülebilir", decode.returncode == 0, decode.stderr[:200])
    boxes = top_level_atoms(result.saved_to)
    check("faststart açık (moov, mdat'tan önce)",
          "moov" in boxes and "mdat" in boxes
          and boxes.index("moov") < boxes.index("mdat"), boxes)

    small = vc.compress_to(fixtures["tiny"], out, 19_000_000)
    check("sınır altındaki dosya kayıpsız kopyalanır", small.lossless)
    check("kayıpsız kopyada yeniden kodlama yok", small.attempts == 0)

    nosound = vc.compress_to(fixtures["motion_1080p"], out, target, keep_audio=False)
    check("sessiz seçeneği ses akışı bırakmaz",
          not vc.probe(nosound.saved_to, vc.find_tool("ffprobe")).has_audio)

    turkish = tmp / "Giriş ekranı #4312.mp4"
    shutil.copy(fixtures["tiny"], turkish)
    tr_result = vc.compress_to(turkish, out, 19_000_000)
    check("Türkçe/boşluklu ad işlenir",
          tr_result.saved_to.is_file() and " " not in tr_result.saved_to.name,
          tr_result.saved_to.name)


def test_two_pass(fixtures: dict[str, Path], tmp: Path) -> None:
    """İki geçişli ABR yolu tek geçiş hedefi aştığında devreye girer; doğrudan sına."""
    section("iki geçişli kodlama")
    out = tmp / "ikigecis"
    out.mkdir(exist_ok=True)
    target = out / "sonuc.mp4"

    info = vc.probe(fixtures["motion_1080p"], vc.find_tool("ffprobe"))
    plan = vc.make_plan(info, 4_000_000, True, None, vc.MIN_BPP)
    vc.encode_two_pass(info, plan, "veryfast", target, plan.video_bps,
                       on_progress=None, cancel=None, on_log=None)

    check("çıktı üretildi", target.is_file())
    budget = int((plan.video_bps + plan.audio_bps) / 8 * info.duration)
    size = target.stat().st_size
    check("hedeflenen bit hızına isabet eder (±%12)",
          abs(size / budget - 1) < 0.12, f"{size} / {budget}")
    result = vc.probe(target, vc.find_tool("ffprobe"))
    check("1. geçiş sesi bozmaz, 2. geçiş sesi yazar", result.has_audio)
    check("iki geçişte de çözünürlük planla aynı",
          (result.width, result.height) == (plan.width, plan.height),
          f"{result.width}x{result.height} != {plan.width}x{plan.height}")
    check("geçici istatistik dosyası bırakmaz",
          not list(out.glob("*.log")) and not list(out.glob("*mbtree*")),
          [p.name for p in out.iterdir()])


def test_save_choice(fixtures: dict[str, Path], tmp: Path) -> None:
    """Sıkıştırma ile kaydetme ayrı: yeri ve adı çağıran (kullanıcı) belirler."""
    section("kayıt yeri seçimi")
    result = vc.compress_one(fixtures["tiny"], 19_000_000)
    check("sonuç geçici klasörde bekliyor",
          result.temp_path.is_file() and result.saved_to is None, result.temp_path)
    check("önerilen ad üretildi", result.suggested_name.endswith(".mp4"),
          result.suggested_name)

    # a) Klasör verilince önerilen ad kullanılır.
    folder = tmp / "secilen klasor"
    folder.mkdir(exist_ok=True)
    saved = vc.save_result(result, folder)
    check("klasöre kaydedilir", saved.parent == folder and saved.is_file(), saved)
    check("önerilen ad kullanılır", saved.name == result.suggested_name, saved.name)
    check("saved_to doldurulur", result.saved_to == saved)
    check("kaydettikten sonra geçici klasör silinir", not result.workdir.exists())

    # b) Tam dosya yolu verilince kullanıcının adı birebir korunur.
    second = vc.compress_one(fixtures["tiny"], 19_000_000)
    custom = tmp / "JIRA-4312 ekran kaydi.mp4"
    saved2 = vc.save_result(second, custom)
    check("kullanıcının verdiği ad korunur", saved2 == custom and custom.is_file(), saved2)

    # c) Uzantı unutulursa .mp4 eklenir.
    third = vc.compress_one(fixtures["tiny"], 19_000_000)
    saved3 = vc.save_result(third, tmp / "uzantisiz")
    check("eksik uzantı tamamlanır", saved3.suffix == ".mp4" and saved3.is_file(), saved3)

    # d) Klasörde aynı ad varsa üzerine yazmaz.
    fourth = vc.compress_one(fixtures["tiny"], 19_000_000)
    (folder / fourth.suggested_name).write_bytes(b"onceki")
    saved4 = vc.save_result(fourth, folder)
    check("klasörde çakışma _2 ile çözülür", saved4.stem.endswith("_2"), saved4.name)
    check("eski dosyaya dokunulmaz",
          (folder / fourth.suggested_name).read_bytes() == b"onceki")

    # e) Vazgeçilirse hiçbir iz kalmaz.
    fifth = vc.compress_one(fixtures["tiny"], 19_000_000)
    workdir = fifth.workdir
    vc.discard_result(fifth)
    check("vazgeçilen sonuç diskten silinir", not workdir.exists(), workdir)

    # f) Kaydedilemeyen yol anlaşılır hata verir.
    sixth = vc.compress_one(fixtures["tiny"], 19_000_000)
    blocker = tmp / "dosya_olarak_klasor"
    blocker.write_bytes(b"x")
    try:
        vc.save_result(sixth, blocker / "altinda.mp4")
        check("geçersiz hedef hata verir", False)
    except vc.CompressError as exc:
        check("geçersiz hedef hata verir", "oluşturulamadı" in str(exc) or
              "kaydedilemedi" in str(exc), str(exc).splitlines()[0])
    finally:
        vc.discard_result(sixth)


def test_cancel(fixtures: dict[str, Path], tmp: Path) -> None:
    section("iptal ve geçici dosya temizliği")
    out = tmp / "iptal"
    out.mkdir(exist_ok=True)
    before = set(out.iterdir())
    cancel = threading.Event()
    captured: dict[str, object] = {}

    def worker() -> None:
        try:
            vc.compress_to(fixtures["motion_1080p"], out, 2_000_000,
                           workdir=out, cancel=cancel)
            captured["result"] = "bitti"
        except vc.CompressError as exc:
            captured["error"] = exc.args[0]

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(4)
    cancel.set()
    thread.join(timeout=30)

    check("iptal isteği yakalandı", captured.get("error") == "__CANCELLED__", captured)
    check("iş parçacığı kilitlenmedi", not thread.is_alive())
    leftovers = set(out.iterdir()) - before
    check("geçici dosya bırakmaz", not leftovers, [p.name for p in leftovers])


# ---------------------------------------------------------------------------

def build_fixtures(tmp: Path) -> dict[str, Path]:
    print("Test videoları üretiliyor…")
    fixtures = {
        "motion_1080p": tmp / "hareketli_1080p30.mp4",
        "fps60": tmp / "hizli_60fps.mp4",
        "tiny": tmp / "kucuk.mp4",
    }
    ffmpeg_make(fixtures["motion_1080p"],
                "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest")
    ffmpeg_make(fixtures["fps60"],
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60",
                "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-pix_fmt", "yuv420p")
    ffmpeg_make(fixtures["tiny"],
                "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
                "-t", "5", "-c:v", "libx264", "-crf", "32", "-pix_fmt", "yuv420p")
    return fixtures


def main() -> int:
    quick = "--hizli" in sys.argv
    if not (vc.find_tool("ffmpeg") and vc.find_tool("ffprobe")):
        print("ffmpeg/ffprobe bulunamadı — testler çalıştırılamıyor.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="vsk-test-") as raw:
        tmp = Path(raw)
        test_pure()
        test_naming(tmp)
        fixtures = build_fixtures(tmp)
        test_plan(fixtures)
        test_height_search(fixtures)
        test_bad_input(tmp)
        if quick:
            print("\n(--hizli: kodlama ve iptal testleri atlandı)")
        else:
            test_encode(fixtures, tmp)
            test_save_choice(fixtures, tmp)
            test_two_pass(fixtures, tmp)
            test_cancel(fixtures, tmp)

    print(f"\n{'=' * 48}")
    print(f"SONUÇ: {PASSED} başarılı, {len(FAILED)} hatalı")
    for name in FAILED:
        print(f"  - {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
