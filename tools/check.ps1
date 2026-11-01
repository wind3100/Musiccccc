param(
    [switch]$SkipBuild
)
# One-shot verification for nm: tests -> rebuild single-file exe -> smoke run in a TEMP sandbox.
# Smoke runs in %TEMP% on purpose: dist\ must stay clean for real use (green software dir).
# ASCII only: cmd/PS on GBK consoles misparse non-ASCII script bodies.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "missing venv python: $py" }

Write-Host "[1/3] pytest"
& $py -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "tests failed with exit code $LASTEXITCODE" }

if (-not $SkipBuild) {
    Write-Host "[2/3] build dist\nm.exe"
    & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build nm.spec | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed with exit code $LASTEXITCODE" }
}

$exe = Join-Path $root "dist\nm.exe"
if (-not (Test-Path $exe)) { throw "missing $exe (run without -SkipBuild)" }

# Guard: verification must never write into the deploy dir (dist\).
# Regression for the bug where smoke ran in dist\ and left demo songs + fake config there.
$distDir = Join-Path $root "dist"
$distBefore = @(Get-ChildItem $distDir -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })

Write-Host "[3/3] smoke: packaged exe on offline demo (isolated sandbox)"
$sandbox = Join-Path $env:TEMP ("nm-smoke-" + $PID)
if (Test-Path $sandbox) { Remove-Item -Recurse -Force $sandbox }
New-Item -ItemType Directory -Path $sandbox | Out-Null
try {
    $sandboxExe = Join-Path $sandbox "nm.exe"
    Copy-Item $exe $sandboxExe

    $manifest = (Join-Path $root "demo\manifest.json")
    $playlist = (Join-Path $root "demo\playlist.json")
    $lines = @(
        '[paths]',
        'library = "library"',
        'staging = "staging"',
        'data = "data"',
        '',
        '[download]',
        'executors = ["fake"]',
        'search_interval_s = 0',
        'download_interval_s = 0',
        'max_consecutive_failures = 99',
        '',
        '[executors.fake]',
        'kind = "fake"',
        ('manifest = "' + $manifest.Replace('\', '/') + '"')
    )
    Set-Content -Path (Join-Path $sandbox "config.toml") -Value $lines -Encoding ascii

    $welcome = (& $sandboxExe) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "no-arg run should exit 0, got $LASTEXITCODE" }
    foreach ($kw in @("doctor", "sync", "approve")) {
        if ($welcome -notmatch $kw) { throw "no-arg welcome text missing command: $kw" }
    }
    Write-Host "  ok: double-click shows status/menu"

    (& $sandboxExe --version | Out-Host)
    (& $sandboxExe sync --offline $playlist | Out-Host)
    (& $sandboxExe download --only-new | Out-Host)

    $lib = Join-Path $sandbox "library"
    $files = @(Get-ChildItem $lib -File -ErrorAction SilentlyContinue)
    if ($files.Count -lt 3) { throw "expected >=3 files in library, got $($files.Count)" }

    $pending = (& $sandboxExe approve --list) | Measure-Object -Line
    if ($pending.Lines -lt 3) { throw "expected >=3 pending approvals, got $($pending.Lines)" }

    $sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 2)
    Write-Host ("  library files: {0}, approval lines: {1}" -f $files.Count, $pending.Lines)
    Write-Host "OK  (nm.exe $sizeMb MB, $($files.Count) files, $($pending.Lines) approvals, sandbox cleaned)"
} finally {
    Remove-Item -Recurse -Force $sandbox -ErrorAction SilentlyContinue
}

# Guard check: nothing may have appeared under dist\ during verification.
$distAfter = @(Get-ChildItem $distDir -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
$added = @($distAfter | Where-Object { $distBefore -notcontains $_ })
if ($added.Count -gt 0) {
    throw ("verification polluted dist\ with: " + ($added -join ", "))
}
Write-Host "  ok: dist\ untouched (deliverable dir stays clean)"
exit 0
