# NeXgen Engine — bootstrap (Windows).
#
# Twin of install.sh, and deliberately just as thin: find Python and hand
# off to it. The logic doesn't live here, it lives in
# 03-INFRA\scripts\nexgen_core\bootstrap.py, in one place for both
# platforms. Two shells with no logic in them can't drift apart.
#
#   .\install.ps1            preflight, questions, and next step
#   .\install.ps1 -Check     checks only, no questions and no writes

[CmdletBinding()]
param(
    [switch]$Check,
    [Parameter(ValueFromRemainingArguments = $true)] [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bootstrap = Join-Path $root '03-INFRA\scripts\nexgen_core\bootstrap.py'

if (-not (Test-Path $bootstrap)) {
    [Console]::Error.WriteLine("NeXgen: questo clone e' incompleto, manca $bootstrap")
    exit 1
}

$found = $null
foreach ($candidate in @(@('py', '-3'), @('python3'), @('python'))) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $extraArgs = if ($candidate.Length -gt 1) { $candidate[1..($candidate.Length - 1)] } else { @() }
    $probe = $extraArgs + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
    & $exe @probe 2>$null
    if ($LASTEXITCODE -eq 0) {
        $forward = @()
        if ($Check) { $forward += '--check' }
        if ($Rest)  { $forward += $Rest }
        $args = $extraArgs + @($bootstrap) + $forward
        & $exe @args
        exit $LASTEXITCODE
    }
    $found = $exe
}

[Console]::Error.WriteLine("NeXgen: serve Python 3.11 o successivo." + $(if ($found) { " Trovato '$found', ma e' troppo vecchio." } else { "" }))
[Console]::Error.WriteLine("Installalo (winget install Python.Python.3.13) e rilancia questo script.")
exit 1
