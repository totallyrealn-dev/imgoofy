$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $taskPython) {
    & $taskPython (Join-Path $PSScriptRoot 'build_repo.py')
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 (Join-Path $PSScriptRoot 'build_repo.py')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python (Join-Path $PSScriptRoot 'build_repo.py')
} else {
    throw 'Python 3 is needed for local builds. You can also upload packages through GitHub.'
}
exit $LASTEXITCODE
