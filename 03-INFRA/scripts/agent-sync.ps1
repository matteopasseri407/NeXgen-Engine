$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "agent_sync.py"
$candidates = @()
foreach ($name in @("python3", "python")) {
  $found = Get-Command $name -ErrorAction SilentlyContinue
  if ($found) { $candidates += [pscustomobject]@{ Command = $found.Source; Prefix = @() } }
}
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $candidates += [pscustomobject]@{ Command = $py.Source; Prefix = @("-3") } }

$runtime = $null
foreach ($candidate in $candidates) {
  $prefix = @($candidate.Prefix)
  $candidateCommand = $candidate.Command
  & $candidateCommand @prefix -c "import sys, yaml; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { $runtime = $candidate; break }
}
if (-not $runtime) {
  Write-Error "agent-sync: Python 3 with PyYAML is required; run install.ps1 --check"
  exit 1
}
$runtimePrefix = @($runtime.Prefix)
$runtimeCommand = $runtime.Command
& $runtimeCommand @runtimePrefix $script @args
exit $LASTEXITCODE
