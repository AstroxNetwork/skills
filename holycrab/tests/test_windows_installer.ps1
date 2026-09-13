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

$Initialize = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"ci","version":"1"}}}'
$CliPath = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\holycrab_cli.py"
$Python = (Get-Command python).Source
$ProcessInfo = [System.Diagnostics.ProcessStartInfo]::new()
$ProcessInfo.FileName = $Python
$ProcessInfo.ArgumentList.Add($CliPath)
$ProcessInfo.ArgumentList.Add("mcp")
$ProcessInfo.ArgumentList.Add("serve")
$ProcessInfo.RedirectStandardInput = $true
$ProcessInfo.RedirectStandardOutput = $true
$ProcessInfo.RedirectStandardError = $true
$ProcessInfo.UseShellExecute = $false
$McpProcess = [System.Diagnostics.Process]::Start($ProcessInfo)
$McpProcess.StandardInput.WriteLine($Initialize)
$McpProcess.StandardInput.Close()
$RawHandshake = $McpProcess.StandardOutput.ReadToEnd().Trim()
$McpError = $McpProcess.StandardError.ReadToEnd().Trim()
$McpProcess.WaitForExit()
if ($McpProcess.ExitCode -ne 0) { throw "MCP process failed: $McpError" }
$Handshake = $RawHandshake | ConvertFrom-Json
if ($Handshake.result.serverInfo.name -ne "holycrab-local") { throw "Unexpected MCP handshake: $RawHandshake" }
