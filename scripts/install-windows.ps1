param([string]$Python = "python", [string]$InstallDir = "$env:LOCALAPPDATA\CitrusAgent\venv")
$ErrorActionPreference = "Stop"
$repoDir = Split-Path -Parent $PSScriptRoot
& $Python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required" }
& $Python -m venv $InstallDir
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
$venvPython = Join-Path $InstallDir "Scripts\python.exe"
& $venvPython -m pip install "${repoDir}[claude]"
if ($LASTEXITCODE -ne 0) { throw "Package installation failed" }
$scripts = Join-Path $InstallDir "Scripts"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ';') -notcontains $scripts) {
    [Environment]::SetEnvironmentVariable("Path", "$scripts;$userPath", "User")
}
$env:Path = "$scripts;$env:Path"
Write-Host "Installed. Run: citrus-agent init --hub https://your-hub"
