# NeXgen Engine — generated, do not edit by hand.
#
# 'vault-groom' as the previous release installed it. It holds no logic: it finds a
# Python and hands over to 'nexgen vault groom'. Regenerate with:
#   python3 03-INFRA/scripts/nexgen_core/legacy_launchers.py --write

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$entry = Join-Path $scriptDir 'nexgen_core\cli\__init__.py'

if (-not (Test-Path $entry)) {
    [Console]::Error.WriteLine("NeXgen: engine files are missing at $entry")
    exit 1
}

foreach ($candidate in @(@('py', '-3'), @('python3'), @('python'))) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $forward = $candidate[1..($candidate.Length - 1)] + @($entry) + @('vault') + @('groom') + $args
    & $exe @forward
    exit $LASTEXITCODE
}

[Console]::Error.WriteLine("NeXgen: Python 3 is not on this system's PATH.")
exit 1
