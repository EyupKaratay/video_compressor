# Windows icin tek dosyalik exe uretir: dist\VideoSikistirici.exe
# Calistirma:  powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Turkce ciktinin cp1252 konsolda cokmesini onler.
$env:PYTHONUTF8 = "1"

$Name = "VideoSikistirici"
$Bin  = "build\bin"

# PowerShell, yerel komutlarin cikis kodunda kendiliginden durmaz; her adimi
# acikca denetlemezsek ilk hata gizlenip sonraki adimda kafa karistirici bir
# hataya donusuyor.
function Invoke-Step {
    param([string]$Title, [scriptblock]$Body)
    Write-Host "==> $Title"
    & $Body
    if ($LASTEXITCODE -ne 0) {
        throw "$Title basarisiz oldu (cikis kodu $LASTEXITCODE). Yukaridaki hataya bakin."
    }
}

Invoke-Step "ffmpeg hazirlaniyor ve dogrulaniyor" { python build\fetch_ffmpeg.py $Bin }

foreach ($f in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path (Join-Path $Bin $f))) { throw "$Bin\$f olusturulamadi." }
}

Invoke-Step "PyInstaller ortami kuruluyor" { python -m venv build\.venv }
Invoke-Step "PyInstaller yukleniyor" {
    & build\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip pyinstaller
}

Invoke-Step "Paketleniyor" {
    & build\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed `
      --name $Name `
      --add-binary "$Bin\ffmpeg.exe;bin" `
      --add-binary "$Bin\ffprobe.exe;bin" `
      --exclude-module numpy --exclude-module PIL --exclude-module pytest `
      video_compressor.py
}

$exe = "dist\$Name.exe"
if (-not (Test-Path $exe)) { throw "Paketleme bitti ama $exe olusmadi." }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

Write-Host ""
Write-Host "Hazir: $exe ($mb MB)"
Write-Host "Takim arkadaslariniza bu tek dosyayi gonderin."
Write-Host ""
Write-Host "ONEMLI - imzasiz oldugu icin ilk calistirmada SmartScreen uyarisi cikar:"
Write-Host "  'Daha fazla bilgi' -> 'Yine de calistir'"
