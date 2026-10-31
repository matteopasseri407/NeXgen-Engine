# nexgen: allow reason="bootstrap launcher must locate or install Python before Python exists"
# NeXgen Engine — bootstrap (Windows).
#
# Twin of install.sh, and deliberately just as thin: find Python and hand
# off to it. The logic doesn't live here, it lives in
# 03-INFRA\scripts\nexgen_core\bootstrap.py, in one place for both
# platforms. Two shells with no logic in them can't drift apart.
#
#   .\install.ps1                      preflight, questions, and next step
#   .\install.ps1 -Check               checks only, no questions and no writes
#   .\install.ps1 -Role server         headless Linux always-on gateway
#   .\install.ps1 -Role node           connects as a node to an existing gateway
#   .\install.ps1 -Role node -ExecPolicy full
#                                      the same, and this PC never stops on an exec approval
#   .\install.ps1 -Role standalone     local PC with its own gateway

[CmdletBinding()]
param(
    [switch]$Check,
    [string]$Role,
    [string]$Target,
    [string]$ExecPolicy,
    [string]$Join,
    [string]$NodeName,
    [switch]$Force,
    [Parameter(ValueFromRemainingArguments = $true)] [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bootstrap = Join-Path $root '03-INFRA\scripts\nexgen_core\bootstrap.py'

if (-not (Test-Path $bootstrap)) {
    [Console]::Error.WriteLine("NeXgen: questo clone e' incompleto, manca $bootstrap")
    exit 1
}

# The engine is this clone unless one is declared or installed in ~/.nexgen-engine (see install.sh).
if (-not $env:AGENT_ENGINE_ROOT -and -not (Test-Path (Join-Path $HOME '.nexgen-engine\03-INFRA'))) {
    $env:AGENT_ENGINE_ROOT = Join-Path $root '03-INFRA'
}

$forward = @()
if ($Check)  { $forward += '--check' }
if ($Role)   { $forward += @('--role', $Role) }
if ($Target) { $forward += @('--target', $Target) }
if ($ExecPolicy) { $forward += @('--exec-policy', $ExecPolicy) }
if ($Join)     { $forward += @('--join', $Join) }
if ($NodeName) { $forward += @('--node-name', $NodeName) }
if ($Force)    { $forward += '--force' }
if ($Rest)   { $forward += $Rest }

$found = $null
foreach ($candidate in @(@('python'), @('py', '-3'), @('python3'))) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $extraArgs = if ($candidate.Length -gt 1) { $candidate[1..($candidate.Length - 1)] } else { @() }
    $probe = $extraArgs + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
    & $exe @probe 2>$null
    if ($LASTEXITCODE -eq 0) {
        $launchArgs = $extraArgs + @($bootstrap) + $forward
        & $exe @launchArgs
        exit $LASTEXITCODE
    }
    $found = $exe
}

# If not found or < 3.11, try uv in user mode without reading other user configs
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $env:UV_NO_CONFIG = "1"
    $uvPy = & uv python find ">=3.11" 2>$null
    if (-not $uvPy) {
        [Console]::Error.WriteLine("NeXgen: installazione di Python in modo utente con uv...")
        & uv python install 3.12 2>$null
        $uvPy = & uv python find ">=3.11" 2>$null
    }
    if ($uvPy -and (Test-Path $uvPy)) {
        & $uvPy $bootstrap @forward
        exit $LASTEXITCODE
    }
}

[Console]::Error.WriteLine("NeXgen: serve Python 3.11 o successivo." + $(if ($found) { " Trovato '$found', ma e' troppo vecchio." } else { "" }))
[Console]::Error.WriteLine("Installalo in modo utente con uv (powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`") oppure winget (winget install Python.Python.3.13) e rilancia questo script.")
exit 1
