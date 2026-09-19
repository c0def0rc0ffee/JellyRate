# <summary>
# SUPERSEDED on 12/09/2026 by build-zip.sh in this folder (Linux build). Kept for reference only, do not run.
# </summary>
# <remarks>
# Builds two versioned zips straight from the project root, plus the local
# App mirror:
#   JellyRate Dist\service.jellyrate-<version>.zip  Kodi-installable add-on
#                                                     zip (service.jellyrate\
#                                                     folder at the zip root,
#                                                     named per Kodi convention)
#   JellyRate Git\jellyrate-v<version>-src.zip      GitHub-bound source
#                                                     (add-on + housekeeping)
#   JellyRate App\                                  mirror of the Dist zip
#                                                     contents
# Both zips exclude archives, temp files, output folders and any dot folder
# holding local editor or tooling state.
# Run from the repo root:  powershell -File build-zip.ps1
# </remarks>
$ErrorActionPreference = 'Stop'

$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim()
$distDir = Join-Path $root 'JellyRate Dist'
$gitDir  = Join-Path $root 'JellyRate Git'
$appDir  = Join-Path $root 'JellyRate App'
$stage   = Join-Path $env:TEMP 'jellyrate-build'

# Kodi reads the version from addon.xml, so VERSION must agree with it.
$addonXml = [xml](Get-Content (Join-Path $root 'service.jellyrate\addon.xml') -Raw)
if ($addonXml.addon.version -ne $version) {
    throw "VERSION ($version) does not match addon.xml version ($($addonXml.addon.version)). Update both before building."
}

# Excluded from BOTH zips (stray archives, temp/cache files, local notes and
# release tooling that belongs on this machine only).
# The release tooling is local only. publish.conf and release-mirror.conf name
# a machine on a private network, and the rest is of no use to anyone reading
# the source, so none of it belongs in a snapshot that may be handed out.
$excludeFiles = @('*.zip', '*.7z', '*.tmp', '*.log', '*.pyc',
                  'publish-github.ps1', 'publish-github.sh', 'push-source.sh',
                  'publish.conf', 'release-mirror.conf', '.publish-allow',
                  'GITHUB-RELEASE-GUIDE.md', 'Github repository')
$excludeDirs  = @('JellyRate Dist', 'JellyRate Git', 'JellyRate App',
                  '__pycache__', '$RECYCLE.BIN')
# Plus every dot folder in the root (version control metadata, editor and
# tooling state), so nothing local can leak into a published zip.
$excludeDirs += @(Get-ChildItem -Path $root -Directory -Force -Filter '.*' |
                  Where-Object { $_.Name -ne '.github' } |
                  Select-Object -ExpandProperty FullName)

foreach ($d in @($distDir, $gitDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }

function Build-Zip {
    param([string]$ZipPath, [string]$ContentPath)
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Compress-Archive -Path $ContentPath -DestinationPath $ZipPath -Force
    $size = [math]::Round((Get-Item $ZipPath).Length / 1KB, 1)
    Write-Output "Built: $ZipPath ($size KB)"
}

# Dist zip: the add-on folder itself at the zip root, so Kodi's
# "Install from zip file" (and the Flatpak unzip in the README) work as-is.
$deployStage = Join-Path $stage 'deploy'
robocopy (Join-Path $root 'service.jellyrate') (Join-Path $deployStage 'service.jellyrate') `
    /E /XF @excludeFiles /XD @excludeDirs /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging deploy (exit $LASTEXITCODE)" }
Build-Zip -ZipPath (Join-Path $distDir "service.jellyrate-$version.zip") `
    -ContentPath (Join-Path $deployStage 'service.jellyrate')

# Local App copy: exact mirror of the Dist zip contents.
# /MIR removes anything not in the stage, so never hand-edit this folder.
robocopy $deployStage $appDir /MIR /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed mirroring $appDir (exit $LASTEXITCODE)" }
Write-Output "Mirrored: $appDir"

# Source zip: add-on + repo housekeeping (README, LICENSE, VERSION, this script).
$srcStage = Join-Path $stage 'src'
robocopy $root $srcStage /E /XF @excludeFiles /XD @excludeDirs /NFL /NDL /NJH | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed staging src (exit $LASTEXITCODE)" }
Build-Zip -ZipPath (Join-Path $gitDir "jellyrate-v$version-src.zip") `
    -ContentPath (Join-Path $srcStage '*')

Remove-Item $stage -Recurse -Force

# Copy both zips to the release share. Destinations are read from
# release-mirror.conf beside this script, one per line, first existing one
# wins. That file is local only, because a server address does not belong in
# a published repository. No file means no mirroring, which is what a fresh
# clone should do. A file naming nowhere reachable is an error, not a shrug:
# the point of listing a destination is that builds must land there.
$mirrorConf = Join-Path $root 'release-mirror.conf'
if (Test-Path $mirrorConf) {
    $candidates = Get-Content $mirrorConf |
                  ForEach-Object { $_.Trim() } |
                  Where-Object { $_ -and -not $_.StartsWith('#') }
    $dest = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $dest) {
        throw "No destination in release-mirror.conf is reachable:`n  $($candidates -join "`n  ")`nMount the share and build again, or comment the line out."
    }
    foreach ($z in @((Join-Path $distDir "service.jellyrate-$version.zip"),
                     (Join-Path $gitDir  "jellyrate-v$version-src.zip"))) {
        $target = Join-Path $dest (Split-Path $z -Leaf)
        Copy-Item $z $target -Force
        # A build that reports success while the copy silently failed is the
        # one outcome worth going out of the way to prevent.
        if ((Get-Item $target).Length -ne (Get-Item $z).Length) {
            throw "Mirrored $(Split-Path $z -Leaf) is the wrong size at $dest"
        }
        Write-Output "Mirrored: $target"
    }
}
