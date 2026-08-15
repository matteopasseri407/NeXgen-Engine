#requires -Version 5.1
<#
  agent-now - deterministic current date/time source for agents.
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
    # Both checks must be language-independent (2026-08-15 review, council
    # of Opus 5): w32tm output labels AND values are localized (Italian
    # "Sorgente:" / "Orologio CMOS locale", German "Quelle:", ...), so a
    # regex on either is wrong on every non-English Windows. The registry
    # is the non-textual source: HKLM\...\W32Time\Parameters\Type holds
    # untranslated enum values (NoSync / NTP / NT5DS / AllSync), and
    # checking StartType != Disabled (not the transient "Running" state)
    # avoids false "no" on trigger-started W32Time clients that synced
    # earlier and stopped.
    $ntpType = $null
    try {
        $ntpType = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' -Name 'Type' -ErrorAction Stop).Type
    }
    catch {
        $ntpType = $null
    }
    if (-not $ntpType) {
        $ntpEnabled = "unknown"
        $ntpSynchronized = "unknown"
    }
    elseif ($ntpType -eq "NoSync") {
        $ntpEnabled = "no"
        $ntpSynchronized = "no"
    }
    else {
        $w32time = Get-Service w32time -ErrorAction SilentlyContinue
        # ServiceController.StartType is missing on .NET < 4.6.1 (PS 5.1 on
        # Win7/8.1): guard before reading it, and stay honest with
        # "unknown" instead of asserting "no" for a service we cannot
        # inspect (2026-08-15 council-2, Opus 5).
        if ($w32time -and $w32time.PSObject.Properties['StartType']) {
            $ntpEnabled = if ($w32time.StartType -ne "Disabled") { "yes" } else { "no" }
        }
        else {
            $ntpEnabled = "unknown"
        }
        # Synchronization proof must not rely on localized text (w32tm
        # source values like "Local CMOS Clock" / "Orologio CMOS locale"
        # are translated). Use the Windows Time Service event log instead:
        # event IDs are numbers, never localized (2026-08-15 council-2,
        # Opus 5).
        #   35 = "now synchronized with the time source"
        #   37 = "receiving valid time data"
        #   36 = "unable to synchronize ... because the local clock is not
        #        synchronized"  (a real desync)
        #   47 = "unable to establish a working time sample"  (a real
        #        desync)
        # Event 38 ("cannot reach ... invalid time data") is TRANSIENT --
        # a laptop offline for a night logs it and re-syncs later without
        # a fresh 35 -- so it must not be read as proof of desync, or a
        # healthy machine reports "no" forever (2026-08-15 council-8,
        # Opus 5). The LAST known state decides: most recent sync event
        # vs. most recent real desync event. No window arithmetic: a fixed
        # recency window made "unknown" permanent on healthy machines
        # whose log rotated or whose service started long ago, and
        # SpecialPollInterval is only meaningful for Type=NTP, not
        # NT5DS/AllSync domain clients (2026-08-15 council-8, Opus 5).
        $syncEvent = $null
        $desyncEvent = $null
        try {
            $syncEvent = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Time-Service'; Id = 35, 37 } -MaxEvents 1 -ErrorAction Stop
        }
        catch {
            $syncEvent = $null
        }
        try {
            $desyncEvent = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-Time-Service'; Id = 36, 47 } -MaxEvents 1 -ErrorAction Stop
        }
        catch {
            $desyncEvent = $null
        }
        $syncTime = if ($syncEvent) { $syncEvent[0].TimeCreated } else { [datetime]::MinValue }
        $desyncTime = if ($desyncEvent) { $desyncEvent[0].TimeCreated } else { [datetime]::MinValue }
        if ($syncTime -ge $desyncTime) {
            if ($syncTime -eq [datetime]::MinValue) {
                $ntpSynchronized = "unknown"  # no evidence at all
            }
            else {
                $ntpSynchronized = "yes"  # last known state: synchronized
            }
        }
        else {
            $ntpSynchronized = "no"  # last known state: desynchronized
        }
    }
}
catch {
    # Both variables already default to "unknown" before the try; do NOT
    # re-assign here, or a partially computed $ntpEnabled (e.g. registry
    # read OK, event query failed) would be clobbered (2026-08-15
    # council-4, Opus 5).
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
