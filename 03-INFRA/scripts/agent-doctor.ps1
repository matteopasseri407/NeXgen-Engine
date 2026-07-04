#requires -Version 5.1
<#
  agent-doctor.ps1 - verifica allineamento agenti su Windows (porting di agent-doctor.sh).
  Read-only. Exit 0 se nessun FAIL, 1 altrimenti.
  Uso:  .\agent-doctor.ps1            report leggibile
        .\agent-doctor.ps1 -Summary   una riga sintetica (per healthcheck)
  NOTA: su Windows Codex/Antigravity possono essere copie o symlink del canonico.
  Claude usa solo un pointer leggero verso il canonico per evitare duplicazione
  con OpenCode quando entrambi i file vengono caricati nello stesso contesto.
#>
param([switch]$Summary)

$ErrorActionPreference = "Continue"
$HomeDir = [Environment]::GetFolderPath("UserProfile")
$Vault   = if ($env:KNOWLEDGE_VAULT_PATH) { $env:KNOWLEDGE_VAULT_PATH } else { Join-Path $HomeDir "KnowledgeVault" }
$Remote  = if ($env:KNOWLEDGE_VAULT_REMOTE) { $env:KNOWLEDGE_VAULT_REMOTE } else { "oracle" }
$Branch  = if ($env:KNOWLEDGE_VAULT_BRANCH) { $env:KNOWLEDGE_VAULT_BRANCH } else { "main" }
$Layer   = Join-Path $Vault "03-INFRA\agent-universal-layer"
$Canon   = Join-Path $Layer "instructions\AGENTS.md"
$OcJson  = Join-Path $HomeDir ".config\opencode\opencode.json"

$script:PASS = 0; $script:WARN = 0; $script:FAILN = 0; $script:FAILS = @()
function ok($m)   { $script:PASS++;  if (-not $Summary) { Write-Host "  [OK]   $m" -ForegroundColor Green } }
function warn($m) { $script:WARN++;  if (-not $Summary) { Write-Host "  [WARN] $m" -ForegroundColor Yellow } }
function bad($m)  { $script:FAILN++; $script:FAILS += $m; if (-not $Summary) { Write-Host "  [FAIL] $m" -ForegroundColor Red } }
function sec($m)  { if (-not $Summary) { Write-Host "`n$m" -ForegroundColor White } }
function gitc([string[]]$GitArgs) { (& git -C $Vault @GitArgs 2>$null) }
function httpcode($url, $headers) {
  try { (Invoke-WebRequest -Uri $url -Method Get -TimeoutSec 6 -Headers $headers -UseBasicParsing -ErrorAction Stop).StatusCode }
  catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
}
function hashOf($p) { if (Test-Path -LiteralPath $p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash } else { "" } }

if (-not $Summary) { Write-Host "=== agent-doctor: verifica allineamento agenti ===" -ForegroundColor White }

sec "Host"
ok "rilevato: windows ($env:COMPUTERNAME)"

sec "Vault (memoria) - git vs Oracle (hub) + mirror GitHub"
if (Test-Path -LiteralPath (Join-Path $Vault ".git")) {
  gitc @("fetch","--prune",$Remote,$Branch) | Out-Null
  $b = (gitc @("rev-list","--count","$Branch..$Remote/$Branch")); if (-not $b) { $b = "?" }
  $a = (gitc @("rev-list","--count","$Remote/$Branch..$Branch")); if (-not $a) { $a = "?" }
  $d = @(gitc @("status","--porcelain","--untracked-files=no")).Where({ $_ }).Count
  if ("$b" -eq "0") { ok "allineato a $Remote/$Branch (0 indietro)" } else { bad "indietro di $b commit dal cloud" }
  if ("$a" -eq "0") { ok "nessun commit locale non pubblicato" } else { warn "$a commit locali non pubblicati" }
  if ($d -eq 0)     { ok "working tree pulita (file tracciati)" } else { warn "$d file tracciati non committati (bloccano il pull)" }
} else { bad "il vault non e' un repo git: $Vault" }

sec "Istruzioni canoniche (AGENTS.md unico, Claude pointer anti-duplicazione)"
if (Test-Path -LiteralPath $Canon) { ok "canonico presente"; $ch = hashOf $Canon } else { bad "manca il canonico $Canon"; $ch = "" }
$ClaudeFile = Join-Path $HomeDir "CLAUDE.md"
if (Test-Path -LiteralPath $ClaudeFile) {
  $ci = Get-Item -LiteralPath $ClaudeFile -Force
  $ct = Get-Content -Raw -LiteralPath $ClaudeFile -ErrorAction SilentlyContinue
  if (-not $ci.LinkType -and $ct.Contains($Canon) -and $ct.Contains("compatibility pointer")) { ok "Claude pointer -> AGENTS.md canonico" }
  else { bad "Claude.md deve essere un pointer leggero, non una copia/symlink del canonico ($ClaudeFile)" }
} else { bad "manca Claude pointer ($ClaudeFile)" }
# NOTA (2026-07-01, trovato su Fedora, NON ANCORA VERIFICATO su Windows): su
# Fedora Antigravity legge davvero ~/.gemini/config/AGENTS.md, non ~/ANTIGRAVITY.md
# (quel symlink esisteva ma l'app non lo leggeva mai, falso "ok" per giorni).
# Path Windows atteso per analogia: %USERPROFILE%\.gemini\config\AGENTS.md -- da
# confermare con un probe comportamentale reale (agy -p) la prima volta che si
# lavora su questa macchina, non fidarsi del solo confronto hash.
foreach ($pair in @(@("Codex", (Join-Path $HomeDir ".codex\AGENTS.md")), @("Antigravity", (Join-Path $HomeDir ".gemini\config\AGENTS.md")))) {
  $f = $pair[1]
  if ((hashOf $f) -and (hashOf $f) -eq $ch) { ok "$($pair[0]) = AGENTS.md canonico (contenuto identico)" }
  else { bad "$($pair[0]) NON allineato al canonico ($f)" }
}
if (Test-Path -LiteralPath $OcJson) {
  if (Select-String -Quiet -LiteralPath $OcJson -Pattern "instructions/AGENTS.md") { ok "OpenCode instructions -> AGENTS.md" } else { bad "OpenCode instructions NON puntano ad AGENTS.md" }
} else { bad "manca $OcJson" }

sec "Utility deterministiche agenti"
$agentNow = Get-Command agent-now -ErrorAction SilentlyContinue
if (-not $agentNow) {
  $agentNow = Get-Command agent-now.ps1 -ErrorAction SilentlyContinue
}
if ($agentNow) {
  try {
    $payload = (& $agentNow.Source 2>$null) | Out-String
    if ($payload -match '"source"\s*:\s*"system_clock"' -and $payload -match '"local_time"') {
      ok "agent-now disponibile e funzionante"
    }
    else {
      bad "agent-now presente ma output non valido"
    }
  }
  catch {
    bad "agent-now presente ma non eseguibile"
  }
}
else {
  bad "agent-now non in PATH (lancia agent-sync.ps1)"
}

sec "Connettori MCP - raggiungibilita'"
$c = httpcode "http://127.0.0.1:5678/healthz" $null; if ($c -eq 200) { ok "n8n-mcp (5678): $c" } else { bad "n8n-mcp (5678): $c" }
$c = httpcode "http://127.0.0.1:33002/" $null; if ($c -eq 200 -or $c -eq 302) { ok "firecrawl (33002): $c" } else { bad "firecrawl (33002): $c" }
$c = httpcode "http://127.0.0.1:33003/health" $null; if ($c -eq 200) { ok "vault-ocr (33003): $c" } else { bad "vault-ocr (33003): $c" }
if ($env:VAULT_LIBRARY_URL) {
  $c = httpcode $env:VAULT_LIBRARY_URL @{ Authorization = "Bearer $($env:VAULT_LIBRARY_TOKEN)" }
  if ($c -ne 0 -and $c -ne 401 -and $c -ne 403) { ok "vault-library: $c (vivo)" } else { bad "vault-library: $c" }
} else { warn "VAULT_LIBRARY_URL non in env" }
if (Get-Command npx -ErrorAction SilentlyContinue) { ok "playwright: npx disponibile" } else { warn "npx non in PATH (playwright MCP)" }

sec "Token in env"
foreach ($v in @("N8N_MCP_TOKEN","VAULT_LIBRARY_TOKEN","VAULT_LIBRARY_URL")) {
  if ([Environment]::GetEnvironmentVariable($v)) { ok "$v presente" } else { bad "$v mancante" }
}
if ($env:DEEPSEEK_API_KEY) { ok "DEEPSEEK_API_KEY presente" } else { warn "DEEPSEEK_API_KEY mancante (OpenCode default DeepSeek non parte)" }

sec "MCP configurati nei runtime (Vault 2.0 drift detection)"
if ((Get-Command "python" -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath "$Layer\mcp\render.py")) {
  $renderOut = python "$Layer\mcp\render.py" 2>&1
  $driftLines = @($renderOut | Where-Object { $_ -match '\[DIFF\]|\[MANCA\]|\[MISSING\]|\[ERROR\]' })
  if ($driftLines.Count -gt 0) {
    # render.py non conosce ancora il dialetto Windows (path/npx attesi in stile Fedora):
    # WARN informativo, non FAIL, finche' il rendering Windows non e' implementato (backlog Vault 2.0).
    warn "drift MCP: $($driftLines.Count) voci render.py (in parte dialetto Windows atteso; dettaglio: python `$Layer\mcp\render.py)"
  } else {
    ok "configurazioni MCP 100% allineate al manifest canonico"
  }
} else {
  warn "python o render.py non trovati, salto la verifica drift MCP"
}

sec "Skill"
$sk = Join-Path $HomeDir ".agents\skills"
$n = if (Test-Path -LiteralPath $sk) { @(Get-ChildItem -LiteralPath $sk -Directory).Count } else { 0 }
if ($n -gt 0) { ok "$n skill in ~/.agents/skills" } else { bad "nessuna skill in ~/.agents/skills" }
# Coverage manifest -> hub: senza questo assert una skill registrata nel manifest
# puo' mancare per settimane su un host (buco humanizer, 2026-07-03).
$skillsSyncScript = Join-Path $Vault "03-INFRA\scripts\skills-sync.py"
if ((Get-Command "python" -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $skillsSyncScript)) {
  $ssOut = & python $skillsSyncScript 2>$null
  $ssExit = $LASTEXITCODE
  $esc = [char]27
  $clean = @($ssOut | ForEach-Object { "$_" -replace "$esc\[[0-9;]*m", "" })
  $pending = @($clean | Where-Object { $_ -match '^\s*\+ ' }).Count
  if ($ssExit -ne 0) { warn "skills-sync diff con FAIL, controllare a mano" }
  elseif ($pending -gt 0) { warn "skill drift: $pending azioni pendenti dal manifest (skills-sync --apply)" }
  else { ok "skill allineate al manifest (diff pulito)" }
} else { warn "python o skills-sync.py non disponibili, salto coverage skill" }

sec "OpenCode config"
if (Test-Path -LiteralPath $OcJson) {
  try { Get-Content -Raw -LiteralPath $OcJson | ConvertFrom-Json | Out-Null
    ok "opencode.json: JSON valido"
    if (Select-String -Quiet -LiteralPath $OcJson -Pattern "opencode(-go)?/deepseek-v4-pro") { ok "default = deepseek-v4-pro via Go" } else { warn "default model non e' deepseek-v4-pro (Go)" }
  } catch { bad "opencode.json: JSON NON valido" }
}

sec "Modello locale (host-aware: worker di routing solo su Windows, tag scelto localmente)"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
  # Stessa risoluzione di local-model-agent.ps1: env -> file locale non sincronizzato -> default storico.
  $workerModel = $env:LOCAL_WORKER_MODEL
  if (-not $workerModel) {
    $mf = Join-Path $HomeDir ".config\local-worker\model"
    if (Test-Path -LiteralPath $mf) { $workerModel = (Get-Content -LiteralPath $mf -TotalCount 1).Trim() }
  }
  if (-not $workerModel) { $workerModel = "gemma4-12b-128k" }
  $models = (& ollama list 2>$null) -join "`n"
  if ($models -match [regex]::Escape($workerModel)) { ok "worker locale '$workerModel' presente in ollama list" } else { warn "worker locale '$workerModel' non in ollama list (config: ~\.config\local-worker\model o LOCAL_WORKER_MODEL)" }
} else { warn "ollama non in PATH (worker locale non disponibile)" }

sec "Claude hooks (vault checkpoint/briefing)"
$settingsPath = Join-Path $HomeDir ".claude\settings.json"
if (Test-Path -LiteralPath $settingsPath) {
  try {
    $sj = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
    $cmds = @()
    foreach ($evt in @("SessionStart", "PreCompact")) {
      foreach ($m in @($sj.hooks.$evt)) { $cmds += @($m.hooks).command }
    }
    if (@($cmds | Where-Object { $_ -match "claude-vault-checkpoint" }).Count -ge 2) {
      ok "hook checkpoint/briefing su SessionStart + PreCompact"
    }
    else { bad "hook vault-checkpoint mancante in settings.json (SessionStart/PreCompact) - lancia agent-sync.ps1" }
  }
  catch { bad "settings.json Claude: JSON non valido" }
}
else { warn "settings.json Claude assente (Claude non installato qui?)" }

if ($Summary) {
  $line = "agent-doctor [windows] PASS=$($script:PASS) WARN=$($script:WARN) FAIL=$($script:FAILN)"
  if ($script:FAILN -gt 0) { $line += " | FAIL: " + ($script:FAILS -join ', ') }
  Write-Output $line
} else {
  sec "Riepilogo"
  Write-Host ("  PASS={0}  WARN={1}  FAIL={2}" -f $script:PASS, $script:WARN, $script:FAILN)
  if ($script:FAILN -eq 0) { Write-Host "  -> allineamento VERIFICATO" -ForegroundColor Green } else { Write-Host "  -> ci sono FAIL da sistemare" -ForegroundColor Red }
}
if ($script:FAILN -eq 0) { exit 0 } else { exit 1 }
