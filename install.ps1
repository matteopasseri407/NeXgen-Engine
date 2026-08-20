# NeXgen Engine — bootstrap (Windows).
#
# Gemello di install.sh, e volutamente altrettanto sottile: trova Python e
# passa il testimone. La logica non vive qui, vive in
# 03-INFRA\scripts\nexgen_core\bootstrap.py, una volta sola per entrambe le
# piattaforme. Due gusci che non contengono logica non possono divergere.
#
#   .\install.ps1            preflight, domande e passo successivo
#   .\install.ps1 -Check     solo controlli, nessuna domanda e nessuna scrittura

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
    $probe = $candidate[1..($candidate.Length - 1)] + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
    & $exe @probe 2>$null
    if ($LASTEXITCODE -eq 0) {
        $forward = @()
        if ($Check) { $forward += '--check' }
        if ($Rest)  { $forward += $Rest }
        $args = $candidate[1..($candidate.Length - 1)] + @($bootstrap) + $forward
        & $exe @args
        exit $LASTEXITCODE
    }
    $found = $exe
}

[Console]::Error.WriteLine("NeXgen: serve Python 3.11 o successivo." + $(if ($found) { " Trovato '$found', ma e' troppo vecchio." } else { "" }))
[Console]::Error.WriteLine("Installalo (winget install Python.Python.3.13) e rilancia questo script.")
exit 1
