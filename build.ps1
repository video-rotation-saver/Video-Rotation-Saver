param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
    }
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.10+ first."
}

Invoke-Native py -3 -m venv .venv-build
Invoke-Native .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
Invoke-Native .\.venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
Invoke-Native .\.venv-build\Scripts\python.exe scripts\export_branding.py
Invoke-Native .\.venv-build\Scripts\pyinstaller.exe --noconfirm packaging\video_rotation_saver.spec

if (-not $SkipInstaller) {
    $isccPath = $null
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($iscc) {
        $isccPath = $iscc.Source
    }
    if (-not $iscc) {
        $common = @(
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
        )
        foreach ($candidate in $common) {
            if ($candidate -and (Test-Path $candidate)) {
                $isccPath = $candidate
                break
            }
        }
    }

    if ($isccPath) {
        Invoke-Native $isccPath packaging\installer.iss
    } else {
        Write-Warning "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6, then run: ISCC.exe packaging\installer.iss"
    }
}

Write-Host "Build complete."
Write-Host "App EXE:       $Root\dist\VideoRotationSaver.exe"
Write-Host "Installer dir: $Root\dist\installer"
