# Download and run this release of the app, without touching your normal installation.
#
#   irm https://github.com/<owner>/<repo>/releases/latest/download/launch.ps1 | iex
#
# The script is a release asset, so it is published once per release and knows which release it
# belongs to. That is also how versions are chosen: `latest/download/launch.ps1` runs the newest
# release, `download/v1.2.0/launch.ps1` runs that one, and each downloads only its own artifact —
# so two of them side by side really are two versions, not the same one twice.
#
# `| iex` cannot pass arguments. To use them, run the script block yourself:
#   & ([scriptblock]::Create((irm <url>))) -Yes
#
# It asks before downloading anything, and everything lands in a temporary directory: the app is
# installed there rather than into Program Files, so removing it is deleting a folder.

# --- release facts (regenerated at release time; the values below are the template's defaults) ---
$Repo = "daybrite/example"
$Tag = "v0.0.0"
$Asset = "example-windows-xaml-setup.exe"
# --- end release facts ---

$ErrorActionPreference = "Stop"

# Options come from $args rather than param(), which has to be the first statement in a file and
# so cannot sit below the generated block above. Environment variables work under `| iex`, where
# there are no arguments at all.
$AssumeYes = [bool]$env:DAY_LAUNCH_YES
foreach ($a in $args) {
    switch -Regex ($a) {
        '^-(y|Yes)$' { $AssumeYes = $true }
        '^-(h|Help|\?)$' {
            Write-Host "Download and launch $Repo $Tag."
            Write-Host ""
            Write-Host "Usage: launch.ps1 [-Yes]"
            Write-Host "  -Yes   Skip the confirmation prompt (or set DAY_LAUNCH_YES=1)."
            exit 0
        }
        default {
            Write-Error "unknown option $a"
            exit 2
        }
    }
}

$Url = "https://github.com/$Repo/releases/download/$Tag/$Asset"
# One directory per release, so two versions can sit side by side and be compared.
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) "$(Split-Path $Repo -Leaf)-$Tag"
$InstallDir = Join-Path $WorkDir "app"
$File = Join-Path $WorkDir $Asset

Write-Host "$Repo $Tag"
Write-Host ""
Write-Host "  download  $Url"
Write-Host "  into      $WorkDir"
Write-Host "  then      install it there (per-user, no admin) and run it"
Write-Host ""
Write-Host "  NOTE: the installer is signed with a development certificate, not one Windows"
Write-Host "        trusts, so SmartScreen may warn about it."
Write-Host ""

if (-not $AssumeYes) {
    $reply = Read-Host "Continue? [y/N]"
    if ($reply -notmatch '^(y|yes)$') {
        Write-Host "Cancelled - nothing was downloaded."
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

Write-Host "==> Downloading $Asset"
# -UseBasicParsing keeps this working on Windows PowerShell 5.1, where the default tries to use
# Internet Explorer's engine and fails on machines that have none.
Invoke-WebRequest -Uri $Url -OutFile $File -UseBasicParsing

# A file fetched from the internet carries a mark that makes Windows refuse or interrogate it.
# Clearing it here is deliberate and worth being explicit about: you asked for this download, and
# the alternative is a dialog with no useful information in it.
Unblock-File -Path $File

Write-Host "==> Installing into $InstallDir"
Write-Host "    Per-user and silent, so there is no admin prompt and no wizard."
# NSIS: /S is silent, /D= overrides the install directory. /D must come LAST and must not be
# quoted -- NSIS takes the rest of the command line verbatim, which is also how a path containing
# spaces survives. Passing one string keeps PowerShell from re-quoting the arguments.
$proc = Start-Process -FilePath $File -ArgumentList "/S /D=$InstallDir" -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    Write-Error "the installer exited $($proc.ExitCode)"
    exit 1
}

# The app's own .exe, whatever it is called -- the uninstaller is the one other .exe in there.
$exe = Get-ChildItem -Path $InstallDir -Filter *.exe -Recurse |
    Where-Object { $_.Name -ne "uninstall.exe" } |
    Select-Object -First 1
if (-not $exe) {
    Write-Error "no application .exe under $InstallDir"
    exit 1
}

Write-Host "==> Launching $($exe.Name)"
Start-Process -FilePath $exe.FullName

Write-Host ""
Write-Host "Running from $($exe.FullName)"
Write-Host "Remove it with: & '$InstallDir\uninstall.exe' /S"
Write-Host "That also clears the Start Menu shortcut and the uninstall entry it registered."
