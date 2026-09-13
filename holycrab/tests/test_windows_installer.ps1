$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$TestRoot = Join-Path $env:RUNNER_TEMP "holycrab-windows-installer"
$env:HOLYCRAB_INSTALL_SOURCE_DIR = $RepoRoot
$env:HOLYCRAB_INSTALL_PREFIX = Join-Path $TestRoot "prefix"
$env:HOLYCRAB_CONFIG_DIR = Join-Path $TestRoot "config"
$env:HOLYCRAB_INSTALL_MCP = "0"
$env:HOLYCRAB_INSTALL_AGENTS = "none"

& (Join-Path $RepoRoot "install.ps1")
& (Join-Path $RepoRoot "install.ps1")

$BinDir = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "bin"
$Launcher = Join-Path $BinDir "holycrab.cmd"
if (-not (Test-Path $Launcher)) { throw "Missing Windows launcher: $Launcher" }

$PathEntries = @($env:Path -split ";" | Where-Object { $_.TrimEnd("\") -ieq $BinDir.TrimEnd("\") })
if ($PathEntries.Count -ne 1) { throw "HolyCrab bin directory should occur once in current PATH" }

$Version = & $Launcher --version
if ($Version -notmatch "0\.4\.0") { throw "Unexpected version: $Version" }

$Doctor = & $Launcher doctor --json | ConvertFrom-Json
if ($Doctor.ok -ne $true) { throw "HolyCrab doctor did not report ok" }

$CliPath = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\holycrab_cli.py"
$Python = (Get-Command python).Source
& $Python (Join-Path $PSScriptRoot "windows_mcp_smoke.py") $CliPath
if ($LASTEXITCODE -ne 0) { throw "MCP smoke test failed" }
