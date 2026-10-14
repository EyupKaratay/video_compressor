# Video Sıkıştırıcı

Hata kaydı videolarını (ekran kayıtlarını) Jira'nın ek dosya sınırının altına
indirir ve sonucu sistemin **Videolar** klasörüne açıklayıcı bir isimle kaydeder.

Windows, macOS ve Linux'ta çalışır. Takım arkadaşlarınıza **tek bir dosya**
gönderirsiniz; çift tıklayıp kullanırlar. Python, ffmpeg ya da başka bir şey
kurmaları gerekmez — hepsi dosyanın içinde.

## İndir

**[→ En son sürümü indir (Releases)](../../releases/latest)**

| İşletim sistemi | Dosya |
|---|---|
| Windows 10/11 (64 bit) | `VideoSikistirici-windows-x64.exe` |
| Mac — Apple Silicon (M1/M2/M3/M4) | `VideoSikistirici-macos-appleSilicon.zip` |
| Mac — Intel (2020 ve öncesi) | `VideoSikistirici-macos-intel.zip` |
| Linux (64 bit) | `VideoSikistirici-linux-x64` |

Mac'inizin hangisi olduğundan emin değilseniz:  → Bu Mac Hakkında → "Yonga"
satırında *Apple* yazıyorsa Apple Silicon, *Intel* yazıyorsa Intel sürümü.

---

## Takım arkadaşları için kullanım

1. Gelen dosyayı çift tıklayın.
2. **Video Ekle…** ile hata kaydını seçin (birden fazla seçebilirsiniz).
3. **Sıkıştır**.
4. Sıkıştırma bitince **farklı kaydet** penceresi açılır: dosyayı nereye,
   hangi adla kaydedeceğinizi siz seçersiniz.

Dosyayı doğrudan uygulamanın üzerine de bırakabilirsiniz; kuyruğa dolu açılır.

### İlk açılışta çıkan güvenlik uyarısı

Uygulama imzalı olmadığı için işletim sistemi ilk seferde uyarır. Bu normaldir:

| Sistem | Yapılacak |
|---|---|
| **Windows** | "Windows bilgisayarınızı korudu" → **Daha fazla bilgi** → **Yine de çalıştır** |
| **macOS** | Uygulamaya **sağ tık → Aç** → çıkan uyarıda tekrar **Aç** |
| **Linux** | Gerekirse çalıştırma izni verin: `chmod +x VideoSikistirici` |

Dosya ~69 MB olduğu için e-posta yerine ortak sürücü, Teams/Slack ya da ağ
paylaşımıyla göndermek gerekir.

---

## Kayıt yeri tamamen size ait

Sıkıştırma bittikten sonra **farklı kaydet** penceresi açılır. Dosyanın nereye
ve hangi adla kaydedileceğine siz karar verirsiniz — uygulama hiçbir yere
kendiliğinden yazmaz.

* Pencere, **sistemin Videolar klasöründe** açılır; olduğu gibi onaylarsanız
  dosya oraya gider. Sonraki videoda pencere en son kaydettiğiniz klasörde açılır.
* Dosya adı önerilmiş gelir; istediğiniz gibi değiştirebilirsiniz.
* **Vazgeç** derseniz sıkıştırılan dosya diske hiç yazılmaz, silinir.
* Çok sayıda video işliyorsanız ve her seferinde sorulmasını istemiyorsanız:
  *Kayıt yeri* bölümünden **"Doğrudan şu klasöre kaydet"** seçeneğini işaretleyip
  klasörü bir kez belirleyin.

Pencerenin açıldığı başlangıç klasörü, sistemin kendi Videolar klasörüdür ve
yerelleştirilmiş adıyla, OneDrive yönlendirmesi dahil doğru bulunur:

| Sistem | Başlangıç klasörü |
|---|---|
| Windows | Bilinen klasör API'siyle gerçek *Videos* yolu (OneDrive'a taşınmışsa oraya) |
| macOS | `~/Movies` |
| Linux | `xdg-user-dir VIDEOS` (örn. Türkçe sistemde `~/Videolar`) |

### Önerilen dosya adı

```
<orijinal ad>_sikistirilmis[_<çözünürlük>]_<boyut>MB_<tarih-saat>.mp4
```

Örnekler:

```
login_hatasi_sikistirilmis_17.4MB_20261013-2318.mp4
login_hatasi_sikistirilmis_900p_18.9MB_20261013-2318.mp4
```

* Çözünürlük parçası **yalnızca** çözünürlük düşürüldüyse eklenir — yani adına
  bakarak orijinal çözünürlüğün korunup korunmadığını anlarsınız.
* Windows'ta geçersiz karakterler temizlenir, ad 70 karakterle sınırlanır.
* "Doğrudan şu klasöre kaydet" modunda aynı ad varsa `_2`, `_3` eklenir;
  hiçbir dosyanın üzerine yazılmaz. Farklı kaydet penceresinde üzerine yazma
  kararı zaten size sorulur.

---

## Hedef boyut neden 19.0 MB?

Jira'nın "19.07 MB" diye gösterdiği sınır aslında **20.000.000 bayt**tır
(19,07 MiB). Varsayılan hedef, güvenlik payı bırakmak için **19,0 MB =
19.000.000 bayt** olarak ayarlıdır (sınırın %95'i). Arayüzden değiştirebilirsiniz
— başka araçların sınırları için de kullanışlıdır (Slack 10 MB, GitHub 10 MB vb.).

---

## Ayarlar

| Ayar | Anlamı |
|---|---|
| **Hedef boyut (MB)** | Çıktının aşmaması gereken boyut. 1 MB = 1.000.000 bayt. |
| **Hız / kalite** | Hızlı (`veryfast`) · Dengeli (`medium`, varsayılan) · En iyi (`slow`). Aynı boyutta daha yavaş = biraz daha iyi görüntü. |
| **Çözünürlük** | *Otomatik*: gerekirse düşürür, sonra ölçerek geri yükseltir. *Orijinali koru*: asla düşürmez. Ya da üst sınır seçin (1080p / 720p / 480p). |
| **Sesi koru** | Kapatılırsa tüm bütçe görüntüye gider. Sessiz ekran kayıtlarında zaten ses yoksa fark etmez. |
| **Kayıt yeri** | *Her videodan sonra sor*: farklı kaydet penceresi açılır (varsayılan). *Doğrudan şu klasöre kaydet*: seçtiğiniz klasöre sormadan kaydeder. |

---

## Görüntüyü nasıl bozmadan sıkıştırıyor?

Çoğu "video küçültücü" sabit bir bit hızı dayatır ve yazıları okunmaz hale
getirir. Buradaki akış ölçüme dayanır:

1. **Zaten sığıyorsa hiç dokunmaz.** Dosya sınırın altında ve H.264 ise yeniden
   kodlama yapılmaz, kayıpsız kopyalanır (saniyenin altında biter).
2. **Bit bütçesi hesaplanır:** süreden, ses bit hızından ve MP4 başlık payından.
3. **Tek geçiş "capped CRF":** kaliteyi `-crf` belirler, boyut tavanını
   `-maxrate`/`-bufsize` (VBV) uygular. Kolay içerik — ki çoğu ekran kaydı
   öyledir — hedefin çok altında kalır ve hiç kalite kaybetmez.
4. **Ölçerek çözünürlük düzeltmesi:** bütçenin %75'inden azı kullanıldıysa
   çözünürlük boşuna düşürülmüş demektir. Uygulama bunu tahminle değil
   **ölçerek** anlar: boyutun piksel sayısıyla gerçek ölçekleme üssünü hesaplayıp
   daha büyük çözünürlüğü dener, sığarsa onu alır. Durağan ekran kayıtlarında
   bu genellikle orijinal çözünürlüğe geri döner.
5. **Gerekirse iki geçişli ABR:** tek geçiş hedefi aşarsa, ölçülen boyuta göre
   bit hızı yeniden hesaplanıp iki geçişle hedefe isabet edilir.

Ayrıca:

* Çıktı her zaman **H.264 + AAC + MP4** — Jira önizlemesi ve tüm tarayıcılar oynatır.
* `-movflags +faststart`: video tam inmeden oynamaya başlar.
* 4 saniyede bir anahtar kare: inceleyen kişi videoda rahat ileri/geri sarar.
* Ekran kayıtları sık sık değişken kare hızlıdır; her zaman sabit kare hızına
  çevrilir (60 fps üzeri 30'a indirilir) — yoksa süre ve boyut tahmini kayar.
* Üstveri (konum, cihaz adı vb.) temizlenir.
* Çözünürlük düşürülürken `lanczos` kullanılır: yazı kenarları daha keskin kalır.

### Ölçülen sonuçlar

Bu makinede (8 çekirdek, `medium`) alınan gerçek sonuçlar:

| Girdi | Sonuç | Süre |
|---|---|---|
| 327 MB · 1080p30 · 90 sn · yoğun hareket | **18,9 MB** · 900p | 49 sn |
| 220 MB · 1080p60 · 40 sn | **18,1 MB** · 1080p30 | 67 sn |
| 11,6 MB · 1080p30 · 120 sn (sınır altında) | **11,6 MB** · kayıpsız kopya | 0,3 sn |
| 11,6 MB → 3 MB hedef · durağan arayüz | **1,1 MB** · 1080p (288p'den düzeltildi) | 73 sn |

Tüm çıktılar: sınırın altında, tam çözülebilir, faststart'lı.

---

## Testler

Test videolarını ffmpeg ile kendisi üretir; dışarıdan dosya gerekmez ve
sistemin Videolar klasörüne hiçbir şey yazmaz.

```bash
python3 tests/run_tests.py --hizli   # saf mantık, ~5 sn (32 kontrol)
python3 tests/run_tests.py           # kodlama dahil tümü, ~2 dk (51 kontrol)
```

Kapsam: ad temizleme ve çakışma, Videolar klasörü çözümleme, bit bütçesi planı,
ses bütçe tavanı, 60→30 fps indirme, imkansız hedefin hatası, ölçüme dayalı
çözünürlük düzeltmesi, bozuk/olmayan/sessiz dosyalar, uçtan uca sıkıştırma
(boyut, kodek, süre, çözülebilirlik, faststart), kayıpsız kopya yolu,
**kayıt yeri seçimi** (klasöre kaydetme, kullanıcının verdiği tam yol, eksik
uzantının tamamlanması, çakışma, vazgeçince silme, geçersiz hedef hatası),
iki geçişli ABR isabeti, iptal ve geçici dosya temizliği.

---

## Komut satırı (isteğe bağlı)

Otomasyon veya toplu iş için:

```bash
python3 video_compressor.py --no-gui kayit.mp4
python3 video_compressor.py --no-gui --target-mb 9.5 --kalite eniyi kayitlar/
python3 video_compressor.py --no-gui --orijinal-cozunurluk --no-audio kayit.mov
```

Komut satırında pencere açılmaz (otomasyon içindir): dosya `--outdir` ile
verdiğiniz klasöre, verilmezse sistemin Videolar klasörüne kaydedilir.
`--max-height` ile çözünürlük üst sınırı koyabilirsiniz. Tüm seçenekler: `--help`.

---

## Dağıtılabilir dosyayı üretme

PyInstaller çapraz derleme yapamaz: **her işletim sisteminin dosyası o
işletim sisteminde üretilmelidir.** İki yol var.

### A) GitHub Actions (önerilen — üçünü birden üretir)

Depoyu GitHub'a koyup **Actions → Paketle → Run workflow** deyin. İş bitince
dört hazır dosya iner: Linux, Windows, macOS Intel, macOS Apple Silicon.

### B) Elle, her makinede

```bash
bash build/build_linux.sh      # → dist/VideoSikistirici
bash build/build_macos.sh      # → dist/VideoSikistirici-macos.zip
```
```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1   # → dist\VideoSikistirici.exe
```

Betikler sırayla: statik ffmpeg'i indirir, **libx264 ve aac kodlayıcılarının
gerçekten var olduğunu doğrular** (yoksa paketlemeyi reddeder), sonra
PyInstaller ile tek dosya üretir. ffmpeg içeride olduğu için karşı tarafta
hiçbir kurulum gerekmez.

ffmpeg kaynakları platforma göre seçilir:

| Platform | Kaynak | Not |
|---|---|---|
| Linux | johnvansickle.com statik | Eski glibc tabanı → eski dağıtımlarda da çalışır |
| Windows | eugeneware/ffmpeg-static | `win32-x64` |
| macOS | eugeneware/ffmpeg-static | `darwin-arm64` ve `darwin-x64` ayrı |

BtbN derlemeleri bilerek kullanılmıyor: her kodeği (x265, AV1, VP9…) içerdiği
için paketi iki katına çıkarıyordu.

**Ölçülen sonuç (Linux x64):** `dist/VideoSikistirici` = **69 MB**, açılış süresi
1,2 sn (tek dosya her çalıştırmada kendini geçici klasöre açar).

Kendi ffmpeg ikililerinizi kullanmak isterseniz `build/bin/` içine koyun ya da
`FFMPEG_BIN` / `FFPROBE_BIN` değişkenlerini ayarlayın.

> **macOS notu:** Apple Silicon'da üretilen `.app` Intel Mac'lerde çalışmaz
> (tersi de geçerli). Karışık bir filoda ikisini de üretin — GitHub Actions
> zaten iki mimariyi de kapsar. Homebrew ffmpeg dinamik bağlıdır; `build_macos.sh`
> bunu `otool` ile denetler ve dağıtıma uygun olmadığını söyler.

> **Linux notu:** Eski dağıtımlarda da çalışsın diye CI `ubuntu-22.04`
> kullanır. Çok yeni bir dağıtımda derlerseniz çıktı eski glibc'lerde çalışmaz.

---

## Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| "ffmpeg bulunamadı" | Hazır paketi değil, `.py` dosyasını çalıştırıyorsunuz. Uygulama indirmeyi teklif eder; ya da `sudo apt install ffmpeg` / `brew install ffmpeg` / `winget install Gyan.FFmpeg`. |
| "Bu video … içine izlenebilir kalitede sığmıyor" | Video hedef boyut için fazla uzun. Mesaj önerilen azami süreyi söyler; videoyu kısaltın veya hedefi artırın. |
| Farklı kaydet penceresi her videoda açılıyor | Kasıtlı. İstemiyorsanız *Kayıt yeri* → "Doğrudan şu klasöre kaydet". |
| Çıktı beklenenden bulanık | *Çözünürlük: Orijinali koru* deneyin, veya hedef boyutu yükseltin. Asıl çözüm videoyu kısaltmaktır: bütçe saniyeye bölünür. |
| Linux'ta çift tıklayınca açılmıyor | `chmod +x VideoSikistirici` |
| macOS "bozuk, çöpe atılmalı" diyor | `xattr -dr com.apple.quarantine /yol/VideoSikistirici.app` |

---

## Teknik özet

* **Dil:** Python 3 (standart kütüphane; çalışma anında hiç üçüncü parti paket yok)
* **Arayüz:** Tkinter — üç platformda da yerel görünür, ek kurulum gerektirmez
* **Motor:** FFmpeg / libx264 / AAC, MP4 kapsayıcı
* **Paketleme:** PyInstaller `--onefile --windowed`, ffmpeg gömülü
* **Kaynak:** tek dosya — [`video_compressor.py`](video_compressor.py)
