# J.A.R.V.I.S. installer - run_jarvis.bat runs this automatically the first time. Nothing to answer.
#   -Offline    also download the offline voice/hearing (850 MB) and set up Ollama (offline brain)
#   -Autostart  start Jarvis when you sign in to Windows
param([switch]$Offline, [switch]$Autostart)
$ErrorActionPreference = "Stop"
$Project = $PSScriptRoot
$JarvisHome = Join-Path $env:USERPROFILE ".jarvis"     # kept out of OneDrive on purpose
$Venv = Join-Path $JarvisHome "venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }

# Files from a downloaded ZIP are flagged "from the internet"; clear that so Windows doesn't block them.
Get-ChildItem -Path $Project -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

function Find-Python {
    $ErrorActionPreference = "Continue"
    # 1. The Python launcher (installed with python.org Python), newest supported first
    foreach ($v in "3.12", "3.11", "3.13") {
        try {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch {}
    }
    # 2. 'python' on PATH (python.org or Microsoft Store). Windows also has a fake 'python' that only points
    #    you to the Store and fails when run - so actually run it and check the version it reports.
    foreach ($cmd in @(Get-Command python, python3 -All -ErrorAction SilentlyContinue)) {
        try {
            $ver = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -and [version]$ver.Trim() -ge [version]"3.11" -and [version]$ver.Trim() -lt [version]"3.14") {
                return (& $cmd.Source -c "import sys; print(sys.executable)").Trim()
            }
        } catch {}
    }
    # 3. The usual install folders (PATH isn't refreshed right after installing)
    foreach ($v in "312", "311", "313") {
        foreach ($root in "$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles") {
            $exe = Join-Path $root "Python$v\python.exe"
            if (Test-Path $exe) { return $exe }
        }
    }
    return $null
}

Step "Checking for Python"
$Python = Find-Python
if (-not $Python) {
    Step "Installing Python 3.12 (one time, about a minute)"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        $Python = Find-Python
    }
    if (-not $Python) {
        Start-Process "https://www.python.org/downloads/"
        throw "Couldn't install Python automatically. Install Python 3.12 from the page that just opened (tick 'Add python.exe to PATH'), then double-click run_jarvis.bat again."
    }
}
Write-Host "Using $Python"

Step "Installing Jarvis's components (a few minutes, one time)"
New-Item -ItemType Directory -Force $JarvisHome | Out-Null
if (-not (Test-Path $Py)) { & $Python -m venv $Venv }
if (-not (Test-Path $Py)) { throw "Couldn't create Jarvis's Python environment in $Venv." }
& $Py -m pip install --upgrade pip --quiet --disable-pip-version-check
& $Py -m pip install -r (Join-Path $Project "requirements.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Installing the components failed - check the internet connection and run run_jarvis.bat again." }
Push-Location $Project
& $Py -m jarvis.deps --mark      # remember this requirements.txt, so updates install only what's new
Pop-Location

if ($Offline) {
    Step "Downloading the offline voice and hearing (~850 MB)"
    Push-Location $Project
    & $Py -m jarvis --setup
    Pop-Location
    Step "Setting up Ollama (the offline brain)"
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    }
    Write-Host "Download the brain model from Jarvis's Settings window -> Offline mode."
}

Step "Putting a Jarvis button on your desktop"
$shell = New-Object -ComObject WScript.Shell
function New-JarvisLink($path) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = Join-Path $Project "run_jarvis.bat"
    $s.WorkingDirectory = $Project
    $s.IconLocation = (Join-Path $Project "jarvis.ico") + ",0"
    $s.WindowStyle = 7
    $s.Description = "Start J.A.R.V.I.S."
    $s.Save()
}
New-JarvisLink (Join-Path ([Environment]::GetFolderPath("Desktop")) "Jarvis.lnk")
if ($Autostart) { New-JarvisLink (Join-Path ([Environment]::GetFolderPath("Startup")) "Jarvis.lnk") }

Step "Installed. Starting Jarvis..."
