param(
    [string]$OutDir = "dist"
)
# Build a single-file exe (green software: config/data/library stay next to the exe).
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot | Split-Path -Parent
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "venv missing: $python. Run: runtime\python311\tools\python.exe -m venv .venv"
    exit 1
}

& $python -m PyInstaller --noconfirm --clean --distpath $OutDir --workpath build nm.spec

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

$exe = Join-Path $root (Join-Path $OutDir "nm.exe")
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "OK: $exe ($size MB)"
    Write-Host "Now run: .\$OutDir\nm.exe doctor"
} else {
    Write-Error "exe not produced"
    exit 1
}
