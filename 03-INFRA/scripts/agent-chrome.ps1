#requires -Version 5.1
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ChromeArgs
)

$ErrorActionPreference = "Stop"
$Profile = if ($env:AGENT_CHROME_PROFILE) {
  $env:AGENT_CHROME_PROFILE
} else {
  Join-Path $HOME ".config\chrome-agent-debug"
}
$StandardProfile = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"

$Candidates = @()
foreach ($Root in @($env:PROGRAMFILES, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
  if ($Root) {
    $Candidates += Join-Path $Root "Google\Chrome\Application\chrome.exe"
  }
}
$Chrome = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $Chrome) {
  throw "agent-chrome: Chrome is not installed."
}

# Parity with agent-chrome.sh: a bare `agent-chrome` is the idempotent "make
# sure the shared browser is up" call, so when CDP already answers there is
# nothing to do. Without this the Windows launcher started a second Chrome
# process on every such call and blocked the caller until the user closed the
# window -- the opposite of the reuse rule in 03-INFRA/agent-browser-cdp.md.
# With arguments it still launches: Chrome's own single-instance handoff
# forwards the URL to the running window, which is the shared window.
function Test-Cdp {
  try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2 -ErrorAction Stop
    return $true
  } catch {
    return $false
  }
}
if ((-not $ChromeArgs) -and (Test-Cdp)) {
  exit 0
}

$StandardProfileIsRealDirectory = $false
if (Test-Path -LiteralPath $StandardProfile) {
  $StandardProfileItem = Get-Item -LiteralPath $StandardProfile -Force
  $StandardProfileIsRealDirectory = -not [bool](
    $StandardProfileItem.Attributes -band [IO.FileAttributes]::ReparsePoint
  )
}
if ($StandardProfileIsRealDirectory) {
  throw "agent-chrome: close Chrome and migrate the existing profile from $StandardProfile to $Profile before retrying."
}

New-Item -ItemType Directory -Path $Profile -Force | Out-Null
& $Chrome `
  "--remote-debugging-address=127.0.0.1" `
  "--remote-debugging-port=9222" `
  "--user-data-dir=$Profile" `
  "--no-first-run" `
  @ChromeArgs
exit $LASTEXITCODE
