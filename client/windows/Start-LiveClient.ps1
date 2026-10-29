<#
  Albion Market Pulse - Windows live client (script version)

  Prefer AlbionMarketPulseClient.exe from the GitHub release; this script does the same for people
  who want to read exactly what runs.

  Your server cannot see the game's network traffic, only your PC can. This script starts the
  official, open-source Albion Data Client (github.com/ao-data/albiondata-client) and tells it to
  upload every market window you open to TWO places:
    1. the community database (AODP), exactly as the Data Client normally does, and
    2. your own server: https://<server>/api/ingest/<ingest_key>
  The server keeps polling AODP on its own, so this is optional: it only makes your own market
  visits show up on the dashboard within seconds.

  Nothing in this project reads game memory or sends input to the game. The Data Client needs
  Npcap (https://npcap.com, "WinPcap API-compatible mode") and administrator rights to capture packets.

  Usage: copy client.example.json to client.json, fill in server_url and ingest_key, then run
  Start-LiveClient.bat (or: powershell -ExecutionPolicy Bypass -File Start-LiveClient.ps1)
#>
[CmdletBinding()]
param(
    [string]$Config = (Join-Path $PSScriptRoot 'client.json'),
    [switch]$CheckOnly   # only validate settings and server connection, do not start the Data Client
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Fail([string]$msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $Config)) { Fail "Settings not found: $Config. Copy client.example.json to client.json and fill it in." }
$cfg = Get-Content $Config -Raw | ConvertFrom-Json
$server = ([string]$cfg.server_url).TrimEnd('/')
$key = [string]$cfg.ingest_key
if ($server -notmatch '^https://[^/?#@\s]+(/[^?#\s]*)?$') { Fail "server_url must be a plain https:// address (the ingest key must not travel unencrypted)." }
if ($key -notmatch '^[A-Za-z0-9_-]{16,128}$') { Fail "ingest_key is missing or invalid. Copy service.ingest_key from the server config." }
$aodp = if ($cfg.aodp_upload) { [string]$cfg.aodp_upload } else { 'https+pow://pow.europe.albion-online-data.com' }
if ($aodp -notmatch '^https\+pow://pow\.(europe|west|east)\.albion-online-data\.com$') { Fail "aodp_upload must be the official AODP address." }
$ingestUrl = "$server/api/ingest/$key"

# 1. Server reachable?
try {
    $h = Invoke-RestMethod -Uri "$server/healthz" -TimeoutSec 10
    if (-not $h.ok) { throw 'unexpected answer' }
    Write-Host "[OK] Server reachable: $server" -ForegroundColor Green
} catch { Fail "Server not reachable at $server/healthz ($($_.Exception.Message))." }

# 2. Key accepted? An empty order list is accepted and changes nothing.
try {
    $null = Invoke-RestMethod -Method Post -Uri "$ingestUrl/marketorders.ingest" -Body '{"Orders":[]}' -ContentType 'application/json' -TimeoutSec 10
    Write-Host "[OK] Ingest key accepted." -ForegroundColor Green
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 401) { Fail "The server rejected the ingest key. Check service.ingest_key." }
    Fail "Ingest test failed: $($_.Exception.Message)"
}
if ($CheckOnly) { Write-Host "Check finished, Data Client not started (-CheckOnly)."; exit 0 }

# 3. Npcap installed?
$npcap = (Test-Path 'HKLM:\SOFTWARE\Npcap') -or (Get-Service -Name npcap -ErrorAction SilentlyContinue)
if (-not $npcap) {
    Write-Host "[WARN] Npcap not found. Install it from https://npcap.com and tick 'WinPcap API-compatible Mode'." -ForegroundColor Yellow
}

# 4. Data Client present and current? Download the official release, verify the SHA-256 GitHub publishes.
$dir = Join-Path $PSScriptRoot 'albiondata-client'
$exe = Join-Path $dir 'albiondata-client.exe'
$verFile = Join-Path $dir 'version.txt'
$rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/ao-data/albiondata-client/releases/latest' -Headers @{ 'User-Agent' = 'albion-market-pulse' }
$asset = $rel.assets | Where-Object { $_.name -eq 'update-windows-amd64.exe.gz' } | Select-Object -First 1
if (-not $asset) { Fail "The latest Data Client release has no Windows binary." }
$installed = if (Test-Path $verFile) { (Get-Content $verFile -TotalCount 1).Trim() } else { '' }
if (-not (Test-Path $exe) -or $installed -ne $rel.tag_name) {
    if ([string]$asset.digest -notmatch '^sha256:[0-9a-f]{64}$') { Fail "GitHub published no SHA-256 for this release; not installing it." }
    if ([string]$asset.browser_download_url -notlike 'https://github.com/ao-data/albiondata-client/releases/download/*') { Fail "Unexpected download address." }
    Write-Host "[INFO] Downloading Albion Data Client $($rel.tag_name) from the official GitHub release ..."
    $gz = Join-Path $env:TEMP "albiondata-client-$($rel.tag_name).exe.gz"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $gz -UseBasicParsing
    $hash = (Get-FileHash $gz -Algorithm SHA256).Hash.ToLower()
    if ("sha256:$hash" -ne [string]$asset.digest) { Remove-Item $gz -Force; Fail "Checksum mismatch: the download was NOT installed." }
    New-Item -ItemType Directory -Force $dir | Out-Null
    $in = [IO.File]::OpenRead($gz); $out = [IO.File]::Create($exe)
    try { $z = New-Object IO.Compression.GZipStream($in, [IO.Compression.CompressionMode]::Decompress); $z.CopyTo($out) } finally { $out.Close(); $in.Close() }
    Remove-Item $gz -Force
    "$($rel.tag_name)`n$hash" | Out-File -Encoding ascii $verFile
    Write-Host "[OK] Data Client $($rel.tag_name) installed, checksum verified." -ForegroundColor Green
}

# 5. Start (needs admin for packet capture). Already running? Then leave it.
if (Get-Process -Name 'albiondata-client' -ErrorAction SilentlyContinue) {
    Write-Host "[INFO] Albion Data Client is already running. Close it first if it was started without your server URL."
    exit 0
}
$targets = "$aodp,$ingestUrl"
Write-Host "[INFO] Starting Albion Data Client (Windows will ask for administrator rights) ..."
Write-Host "       Uploads go to AODP and to $server/api/ingest/****"
Start-Process -FilePath $exe -ArgumentList @('-i', "`"$targets`"") -Verb RunAs -WorkingDirectory $dir
Write-Host "[OK] Running. Open a market in Albion Online; the dashboard shows 'Sniffer: ACTIVE' within seconds." -ForegroundColor Green
