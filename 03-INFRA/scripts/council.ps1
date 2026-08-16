$ErrorActionPreference = "Stop"
# PowerShell 7.3+ opt-in (defaults to $true there): council.py's non-zero exit
# is checked via $LASTEXITCODE below, so a native failure must not become a
# terminating error. Harmless no-op on Windows PowerShell 5.1.
$PSNativeCommandUseErrorActionPreference = $false
$parent = Split-Path $PSScriptRoot -Parent
$script = Join-Path $parent "agent-universal-layer\council\council.py"
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if ($py) { & $py -3 $script @args } else { & python $script @args }
exit $LASTEXITCODE
