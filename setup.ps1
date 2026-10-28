# J.A.R.V.I.S. setup - creates the Python environment, downloads voice/speech models and checks Ollama.
# Usage:  powershell -ExecutionPolicy Bypass -File setup.ps1 [-Autostart]
param([switch]$Autostart)
$ErrorActionPreference = "Stop"
$Project = $PSScriptRoot
$JarvisHome = Join-Path $env:USERPROFILE ".jarvis"     # kept out of OneDrive on purpose
$Venv = Join-Path $JarvisHome "venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }

Step "Checking Python (3.11+ required)"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python not found. Install it with: winget install Python.Python.3.12" }
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.11") { throw "Python $ver found; Jarvis needs 3.11 or newer." }
Write-Host "Python $ver"

Step "Creating the virtual environment in $Venv"
New-Item -ItemType Directory -Force $JarvisHome | Out-Null
if (-not (Test-Path $Py)) { & python -m venv $Venv }
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r (Join-Path $Project "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Step "Preparing your .env file"
$envFile = Join-Path $Project ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $Project ".env.example") $envFile; Write-Host "Created .env (optional keys go here)" }

Step "Downloading voice and speech models (~850 MB, one time)"
Push-Location $Project
& $Py -m jarvis --setup
Pop-Location

Step "Checking Ollama (the local brain)"
$model = (Select-String -Path (Join-Path $Project "config\settings.toml") -Pattern '^model\s*=\s*"([^"]+)"' | Select-Object -First 1).Matches.Groups[1].Value
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama isn't installed. Install it with:  winget install Ollama.Ollama" -ForegroundColor Yellow
    Write-Host "then run:  ollama pull $model" -ForegroundColor Yellow
} elseif (-not ((ollama list) -match [regex]::Escape($model))) {
    Write-Host "Pulling $model ..."
    ollama pull $model
} else { Write-Host "$model is ready." }

if ($Autostart) {
    Step "Adding Jarvis to Windows startup"
    $lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "Jarvis.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $Project "run_jarvis.bat"
    $s.WorkingDirectory = $Project
    $s.WindowStyle = 7
    $s.Save()
    Write-Host "Jarvis will start when you sign in (remove $lnk to undo)."
}

Step "Done. Start Jarvis with run_jarvis.bat"
