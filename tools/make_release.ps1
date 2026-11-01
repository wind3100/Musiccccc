param(
    [switch]$SkipBuild
)
# Produce a clean, distributable folder: EXE + config template + guide. NO cookies, NO data.
# ASCII only on purpose (GBK consoles misparse non-ASCII script bodies).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$out = Join-Path $root "release"

if (-not $SkipBuild) {
    Write-Host "[1/3] build dist\nm.exe"
    & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build nm.spec | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed: $LASTEXITCODE" }
}

$exe = Join-Path $root "dist\nm.exe"
if (-not (Test-Path $exe)) { throw "missing $exe" }

Write-Host "[2/3] stage release\"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Path $out | Out-Null
Copy-Item $exe (Join-Path $out "nm.exe")
Copy-Item (Join-Path $root "packaging\config.release.toml") (Join-Path $out "config.toml")
# Wildcard on purpose: the guide has a non-ASCII filename and this script body is ASCII-only.
Copy-Item (Join-Path $root "packaging\*.txt") $out -Force
if (-not @(Get-ChildItem $out -Filter *.txt).Count) { throw "usage guide (packaging\*.txt) missing" }

Write-Host "[3/3] verify cleanliness + smoke"
$forbidden = @(".env", "data", "library", "staging")
foreach ($f in $forbidden) {
    if (Test-Path (Join-Path $out $f)) { throw "release must not contain $f (contains personal data / cookie)" }
}
$files = @(Get-ChildItem $out -File)
Write-Host ("  files: " + (($files | ForEach-Object Name) -join ", "))

# Run the released exe inside a throwaway sandbox: it must start, show onboarding hints,
# and must NOT create .env by itself.
$sandbox = Join-Path $env:TEMP ("nm-release-" + $PID)
if (Test-Path $sandbox) { Remove-Item -Recurse -Force $sandbox }
New-Item -ItemType Directory -Path $sandbox | Out-Null
try {
    Copy-Item (Join-Path $out "nm.exe") (Join-Path $sandbox "nm.exe")
    Copy-Item (Join-Path $out "config.toml") (Join-Path $sandbox "config.toml")
    Push-Location $sandbox
    $welcome = (& (Join-Path $sandbox "nm.exe") | Out-String)
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw "released exe exited $code" }
    # ASCII-only assertion: the first-run screen must offer the login command.
    if ($welcome -notmatch "login") { throw "onboarding hint missing in output" }
    if ($welcome -notmatch "doctor") { throw "menu items missing in output" }
    if (Test-Path (Join-Path $sandbox ".env")) { throw "exe created .env on its own" }
    Write-Host "  ok: fresh start shows onboarding, no cookie materialized"
} finally {
    Remove-Item -Recurse -Force $sandbox -ErrorAction SilentlyContinue
}

$sizeMb = [math]::Round((Get-Item (Join-Path $out "nm.exe")).Length / 1MB, 2)
Write-Host "OK  release\ ready ($sizeMb MB, no cookie / no data)"
exit 0
