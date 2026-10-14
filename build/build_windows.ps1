# Windows icin tek dosyalik exe uretir: dist\VideoSikistirici.exe
# Calistirma:  powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$Name = "VideoSikistirici"
$Bin  = "build\bin"

Write-Host "==> ffmpeg hazirlaniyor"
python build\fetch_ffmpeg.py $Bin

Write-Host "==> PyInstaller ortami"
python -m venv build\.venv
& build\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip pyinstaller

Write-Host "==> Paketleniyor"
& build\.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed `
  --name $Name `
  --add-binary "$Bin\ffmpeg.exe;bin" `
  --add-binary "$Bin\ffprobe.exe;bin" `
  --exclude-module numpy --exclude-module PIL --exclude-module pytest `
  video_compressor.py

$exe = "dist\$Name.exe"
$mb  = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Hazir: $exe ($mb MB)"
Write-Host "Takim arkadaslariniza bu tek dosyayi gonderin."
Write-Host ""
Write-Host "ONEMLI - imzasiz oldugu icin ilk calistirmada SmartScreen uyarisi cikar:"
Write-Host "  'Daha fazla bilgi' -> 'Yine de calistir'"
