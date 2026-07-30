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
  @ChromeArgs
exit $LASTEXITCODE
