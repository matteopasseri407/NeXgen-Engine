[CmdletBinding()]
param(
  [Parameter(Position = 0)] [string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Path) -or $Path -in @('-h', '--help')) {
  Write-Output 'Uso: agent-open-folder <percorso-assoluto-cartella>'
  Write-Output 'Apre una cartella locale nel file manager predefinito.'
  exit $(if ($Path) { 0 } else { 2 })
}

if (-not [System.IO.Path]::IsPathRooted($Path)) {
  Write-Error 'Errore: serve un percorso assoluto di una cartella esistente.'
  exit 2
}

try {
  $Folder = [System.IO.Path]::GetFullPath($Path)
} catch {
  Write-Error 'Errore: il percorso della cartella non è valido.'
  exit 2
}

if (-not (Test-Path -LiteralPath $Folder -PathType Container)) {
  Write-Error 'Errore: il percorso non indica una cartella esistente.'
  exit 2
}

# Use Invoke-Item -LiteralPath instead of quoting for explorer.exe: PS 5.1
# does not quote -ArgumentList items itself and explorer splits on spaces,
# and any hand-rolled command-line quoting is a minefield with trailing
# backslashes ("C:\" needs doubling, "C:\foo\\" needs every trailing bar
# doubled again -- CommandLine parsing treats a lone backslash before the
# closing quote as an escaped quote). Invoke-Item opens the folder via
# ShellExecute with NO command-line parsing at all, so paths with spaces,
# drive roots and trailing backslashes all just work (2026-08-15 review,
# councils of Opus 5).
Invoke-Item -LiteralPath $Folder
Write-Output "Cartella aperta: $Folder"
