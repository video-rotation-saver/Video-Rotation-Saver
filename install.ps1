# install.ps1 — Video Rotation Saver setup
#
# Usage (from the project root, in a normal (non-admin) PowerShell):
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
#     .\install.ps1
#
# What it does:
#   1. Verifies Python 3.10+ and FFmpeg/ffprobe are available.
#   2. Creates .venv\ and pip-installs requirements.txt.
#   3. Drops a shortcut to run_silent.vbs into your Startup folder (optional).
#   4. Prints a smoke-test command and the default hotkeys.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Section($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

# ---- Python ----
Write-Section "Python"
try {
    $pyver = (& py -3 --version) 2>&1
} catch {
    Write-Host "Couldn't run 'py -3'. Install Python 3.10+ from https://www.python.org/downloads/ and re-run." -ForegroundColor Red
    exit 1
}
Write-Host $pyver

# ---- ffmpeg ----
Write-Section "FFmpeg"
$ff  = Get-Command ffmpeg  -ErrorAction SilentlyContinue
$ffp = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ff -or -not $ffp) {
    Write-Host "ffmpeg and/or ffprobe not found on PATH." -ForegroundColor Yellow
    Write-Host "Install via 'winget install Gyan.FFmpeg' or grab a build from https://www.gyan.dev/ffmpeg/builds/ and add bin\ to PATH."
    Write-Host "You can still proceed — edit %APPDATA%\VideoRotationSaver\config.ini with full paths later."
} else {
    Write-Host "ffmpeg  -> $($ff.Source)"
    Write-Host "ffprobe -> $($ffp.Source)"
}

# ---- venv ----
Write-Section "Virtual environment"
if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

# ---- Startup shortcut (opt-in prompt) ----
Write-Section "Startup shortcut"
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'Video Rotation Saver.lnk'
$ans = Read-Host "Create a Startup shortcut so the daemon runs at login? [Y/n]"
if ($ans -eq '' -or $ans -match '^[yY]') {
    $wshell = New-Object -ComObject WScript.Shell
    $shortcut = $wshell.CreateShortcut($lnk)
    $shortcut.TargetPath = (Resolve-Path .\run_silent.vbs).Path
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.WindowStyle = 7   # minimized (unused — .vbs runs invisibly)
    $shortcut.Description = 'Video Rotation Saver tray daemon'
    $shortcut.Save()
    Write-Host "Shortcut: $lnk"
} else {
    Write-Host "Skipped. You can always run .\run_silent.vbs manually."
}

# ---- Smoke test instructions ----
Write-Section "Done"
Write-Host "Start the daemon now (visible console, so you can see errors):" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\python.exe -m potplayer_rotate daemon"
Write-Host ""
Write-Host "Once verified, use the silent launcher:" -ForegroundColor Green
Write-Host "    wscript .\run_silent.vbs"
Write-Host ""
Write-Host "Default hotkeys: Ctrl+Alt+Numpad 2 rotates, Ctrl+Alt+Numpad 4 renames."
Write-Host "Edit in %APPDATA%\VideoRotationSaver\config.ini, or use the tray menu."
Write-Host ""
Write-Host "NOTE: PotPlayer does not need any hotkey binding of its own — this"
Write-Host "      daemon registers the hotkey globally via RegisterHotKey."
