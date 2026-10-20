$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'run.py'
if ($env:PYTHON) {
    & $env:PYTHON $launcher @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 $launcher @args
} else {
    & python $launcher @args
}
exit $LASTEXITCODE
