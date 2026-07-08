#requires -Version 5.1
<#
  agent-now — deterministic current date/time source for agents.
  Training data lies about "what year is it"; this doesn't. Read this before
  any recency-sensitive judgment call, search query, or deadline check
  (see AGENTS.md > Operating Style, and firecrawl.md).

  Usage: agent-now.ps1 [-Format json|human|shell] [-Json]
  Default output is JSON. -Json is accepted for symmetry with call sites
  that pass it explicitly.
#>
[CmdletBinding()]
param(
    [ValidateSet("json", "human", "shell")]
    [string]$Format = "json",
    [switch]$Json
)

if ($Json) { $Format = "json" }

$now = Get-Date
$utc = $now.ToUniversalTime()
$offset = [DateTimeOffset]$now

try {
    $timezone = [TimeZoneInfo]::Local.Id
}
catch {
    $timezone = "unknown"
}

$ntpSynchronized = "unknown"
$ntpEnabled = "unknown"
try {
    $status = (& w32tm /query /status 2>$null) -join "`n"
    if ($LASTEXITCODE -eq 0 -and $status) {
        $ntpEnabled = "yes"
        if ($status -match "(?im)^\s*Source:\s+(.+)$") {
            $source = $Matches[1].Trim()
            if ($source -and $source -notmatch "Local CMOS Clock") {
                $ntpSynchronized = "yes"
            }
            else {
                $ntpSynchronized = "no"
            }
        }
    }
}
catch {
    $ntpSynchronized = "unknown"
}

$data = [ordered]@{
    source = "system_clock"
    local_time = $offset.ToString("yyyy-MM-ddTHH:mm:sszzz")
    utc_time = $utc.ToString("yyyy-MM-ddTHH:mm:ssZ")
    timezone = $timezone
    epoch_seconds = $offset.ToUnixTimeSeconds()
    date = $now.ToString("yyyy-MM-dd")
    time = $now.ToString("HH:mm:sszzz")
    year = $now.Year
    weekday = $now.ToString("dddd", [Globalization.CultureInfo]::InvariantCulture)
    ntp_synchronized = $ntpSynchronized
    ntp_enabled = $ntpEnabled
    can_ntp = "unknown"
    local_rtc = "unknown"
}

switch ($Format) {
    "human" {
        Write-Output ("Local: {0}" -f $data.local_time)
        Write-Output ("UTC:   {0}" -f $data.utc_time)
        Write-Output ("TZ:    {0}" -f $data.timezone)
        Write-Output ("NTP:   synchronized={0} enabled={1}" -f $data.ntp_synchronized, $data.ntp_enabled)
    }
    "shell" {
        Write-Output ("AGENT_NOW_LOCAL_ISO='{0}'" -f $data.local_time)
        Write-Output ("AGENT_NOW_UTC_ISO='{0}'" -f $data.utc_time)
        Write-Output ("AGENT_NOW_TIMEZONE='{0}'" -f $data.timezone)
        Write-Output ("AGENT_NOW_EPOCH_SECONDS='{0}'" -f $data.epoch_seconds)
        Write-Output ("AGENT_NOW_DATE='{0}'" -f $data.date)
        Write-Output ("AGENT_NOW_TIME='{0}'" -f $data.time)
        Write-Output ("AGENT_NOW_YEAR='{0}'" -f $data.year)
        Write-Output ("AGENT_NOW_WEEKDAY='{0}'" -f $data.weekday)
        Write-Output ("AGENT_NOW_NTP_SYNCHRONIZED='{0}'" -f $data.ntp_synchronized)
    }
    default {
        $data | ConvertTo-Json -Depth 4
    }
}
