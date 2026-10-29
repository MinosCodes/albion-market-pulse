# Downloads the latest official Albion Data Client (github.com/ao-data/albiondata-client)
# into .\albiondata-client\albiondata-client.exe next to this project.
#
# Releases ship the Windows binary as update-windows-amd64.exe.gz (a gzipped .exe);
# the old albiondata-client-windows-amd64.zip asset no longer exists.
param([string]$Destination = (Join-Path $PSScriptRoot "..\albiondata-client"))

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$release = Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/ao-data/albiondata-client/releases/latest" `
    -Headers @{ "User-Agent" = "albion-market-pulse" }
$asset = $release.assets | Where-Object { $_.name -eq "update-windows-amd64.exe.gz" }
if (-not $asset) { throw "Release $($release.tag_name) has no update-windows-amd64.exe.gz asset" }

New-Item -ItemType Directory -Force $Destination | Out-Null
$gzPath = Join-Path $Destination "albiondata-client.exe.gz"
$exePath = Join-Path $Destination "albiondata-client.exe"
Write-Host "Downloading Albion Data Client $($release.tag_name)..."
Invoke-WebRequest -UseBasicParsing $asset.browser_download_url -OutFile $gzPath

$in = [IO.File]::OpenRead($gzPath)
$gz = New-Object IO.Compression.GZipStream($in, [IO.Compression.CompressionMode]::Decompress)
$out = [IO.File]::Create($exePath)
try { $gz.CopyTo($out) } finally { $out.Close(); $gz.Close(); $in.Close() }
Remove-Item $gzPath

Write-Host "Saved $exePath"
