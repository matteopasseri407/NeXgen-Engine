#requires -Version 5.1
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ChromeArgs
)

# Parity with agent-chrome.sh -- same two contracts, same reasons:
#
#   agent-chrome [args...]  someone wants a WINDOW. Always hand off to the
#                           Chrome binary; its single-instance handoff raises
#                           the running browser instead of starting a second.
#   agent-chrome --ensure   an agent only wants the shared browser to EXIST.
#                           Exit 0 when CDP already answers, opening nothing.
#
# The bare call used to be the --ensure call on both platforms. That is the
# defect an end user reported as "Chrome does nothing when I click it": with a
# windowless Chrome still holding :9222, the launcher exited 0 and the desktop
# treated the launch as successful.
#
# The first Chrome process to open the shared profile decides whether :9222
# exists for the whole session; a launcher that starts Chrome without the
# debugging port wins that race and cannot be repaired by a later handoff.
# This script reports that state instead of failing silently, and --heal
# restarts the browser to recover it.

$ErrorActionPreference = "Stop"
# PowerShell 7.3+ opt-in (defaults to $true there): chrome.exe's non-zero exit
# is checked via $LASTEXITCODE below, so a native failure must not become a
# terminating error that skips `exit $LASTEXITCODE`. Harmless no-op on Windows
# PowerShell 5.1.
$PSNativeCommandUseErrorActionPreference = $false
$Profile = if ($env:AGENT_CHROME_PROFILE) {
  $env:AGENT_CHROME_PROFILE
} else {
  Join-Path $HOME ".config\chrome-agent-debug"
}
$StandardProfile = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"

$Mode = "window"
if ($ChromeArgs -and $ChromeArgs.Count -gt 0) {
  switch ($ChromeArgs[0]) {
    "--ensure" { $Mode = "ensure"; $ChromeArgs = @($ChromeArgs | Select-Object -Skip 1) }
    "--heal"   { $Mode = "heal";   $ChromeArgs = @($ChromeArgs | Select-Object -Skip 1) }
  }
}

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

function Test-Cdp {
  try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2 -ErrorAction Stop
    return $true
  } catch {
    return $false
  }
}

# The browser process holding the shared profile, if any. Windows has no
# SingletonLock symlink to read, so this matches the user-data-dir on the
# command line and skips renderer/GPU children, which carry it too.
function Get-ProfileOwnerProcessId {
  try {
    $Processes = Get-CimInstance -ClassName Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction Stop
  } catch {
    return $null
  }
  foreach ($Process in $Processes) {
    if (-not $Process.CommandLine) { continue }
    # Windows paths are case-insensitive: a profile path written with a
    # different case must still match or the owner goes unrecognized and a
    # second Chrome starts instead of --ensure/--heal handing off.
    $profileArg = "--user-data-dir=$Profile"
    if ($Process.CommandLine.IndexOf($profileArg, [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
    if ($Process.CommandLine.Contains("--type=")) { continue }
    return $Process.ProcessId
  }
  return $null
}

# --ensure is the idempotent "make sure the shared browser is up" call. It is
# the only mode allowed to do nothing, and only when CDP actually answers.
if ($Mode -eq "ensure" -and (Test-Cdp)) {
  exit 0
}

$Owner = Get-ProfileOwnerProcessId
if ($Owner) {
  $CdpUp = Test-Cdp
  if ($CdpUp -and $Mode -eq "heal") {
    Write-Error "agent-chrome: CDP already answers on 127.0.0.1:9222; nothing to heal." -ErrorAction Continue
    exit 0
  }
  if (-not $CdpUp -and $Mode -eq "heal") {
    Write-Error "agent-chrome: restarting Chrome (PID $Owner) to restore the CDP port." -ErrorAction Continue
    Stop-Process -Id $Owner -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 10; $i++) {
      if (-not (Get-Process -Id $Owner -ErrorAction SilentlyContinue)) { break }
      Start-Sleep -Seconds 1
    }
    Stop-Process -Id $Owner -Force -ErrorAction SilentlyContinue
  }
  if (-not $CdpUp -and $Mode -ne "heal") {
    # Handing off still gives the human their window, so this warns instead of
    # refusing -- but it must never be silent.
    Write-Error (
      "agent-chrome: Chrome (PID $Owner) holds the shared profile without the CDP port. " +
      "Agents cannot attach until it is restarted: run 'agent-chrome --heal'."
    ) -ErrorAction Continue
  }
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
