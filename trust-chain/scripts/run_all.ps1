# Runs the whole pipeline from a fresh clone. Requires uv on PATH. Fails loudly.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is not on PATH. Install it first: https://docs.astral.sh/uv/'
}
$repo = Split-Path -Parent $PSScriptRoot
# The exit code decides: in Windows PowerShell 5.1, 'Stop' would make any stderr line from uv fatal.
$ErrorActionPreference = 'Continue'
uv run --no-project --python 3.12 python (Join-Path $repo 'scripts\run_all.py')
if ($LASTEXITCODE -ne 0) {
    throw "run_all failed with exit code $LASTEXITCODE"
}
