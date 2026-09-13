$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ChinesePath = -join @(
    [char]0x4E2D, [char]0x6587, [char]0x7528, [char]0x6237,
    [char]0x5B89, [char]0x88C5, [char]0x6D4B, [char]0x8BD5
)
$TestRoot = Join-Path $env:RUNNER_TEMP ("HolyCrab-" + $ChinesePath)
$env:HOLYCRAB_INSTALL_SOURCE_DIR = $RepoRoot
$env:HOLYCRAB_INSTALL_PREFIX = Join-Path $TestRoot "prefix"
$env:HOLYCRAB_CONFIG_DIR = Join-Path $TestRoot "config"
$env:HOLYCRAB_INSTALL_MCP = "0"
$env:HOLYCRAB_INSTALL_AGENTS = "none"
$env:HOLYCRAB_NO_UPDATE_CHECK = "1"

& (Join-Path $RepoRoot "install.ps1")
& (Join-Path $RepoRoot "install.ps1")

$BinDir = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "bin"
$Launcher = Join-Path $BinDir "holycrab.cmd"
if (-not (Test-Path $Launcher)) { throw "Missing Windows launcher: $Launcher" }

$PathEntries = @($env:Path -split ";" | Where-Object {
    $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq $BinDir.TrimEnd([IO.Path]::DirectorySeparatorChar)
})
if ($PathEntries.Count -ne 1) { throw "HolyCrab bin directory should occur once in current PATH" }

$Version = & $Launcher --version
if ($Version -notmatch "0\.4\.1") { throw "Unexpected version: $Version" }

$Doctor = & $Launcher doctor --json | ConvertFrom-Json
if ($Doctor.ok -ne $true) { throw "HolyCrab doctor did not report ok" }

$CliPath = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\holycrab_cli.py"
$Python = (Get-Command python).Source

$SavedKey = "hc_test_windows_dpapi_123456789"
$SetupOutput = ($SavedKey | & $Launcher setup --stdin --no-verify | Out-String)
if ($SetupOutput.Contains($SavedKey)) { throw "API Key leaked in setup output" }
$ConfigPath = Join-Path $env:HOLYCRAB_CONFIG_DIR "config.json"
$ConfigText = Get-Content -LiteralPath $ConfigPath -Raw
$Config = $ConfigText | ConvertFrom-Json
if ($Config.PSObject.Properties.Name -contains "apiKey") { throw "Windows config retained plaintext apiKey" }
if (-not ($Config.PSObject.Properties.Name -contains "apiKeyDpapi")) { throw "Windows config omitted DPAPI ciphertext" }
if ($ConfigText.Contains($SavedKey)) { throw "Windows config contains plaintext API Key" }

$LegacyKey = "hc_test_legacy_plaintext_123456789"
[IO.File]::WriteAllText($ConfigPath, ('{"apiKey":"' + $LegacyKey + '"}'), [Text.UTF8Encoding]::new($false))
$MigrationCheck = @'
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("installed_holycrab", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.credential()[1] == sys.argv[2]
saved = json.loads(pathlib.Path(module.config_path()).read_text(encoding="utf-8"))
assert "apiKey" not in saved and isinstance(saved.get("apiKeyDpapi"), str)
'@
$MigrationScript = Join-Path $TestRoot "migration-check.py"
[IO.File]::WriteAllText($MigrationScript, $MigrationCheck, [Text.UTF8Encoding]::new($false))
& $Python $MigrationScript $CliPath $LegacyKey
if ($LASTEXITCODE -ne 0) { throw "Legacy plaintext API Key migration failed" }

$FakeBin = Join-Path $TestRoot "fake-bin"
New-Item -ItemType Directory -Force -Path $FakeBin | Out-Null
$env:FAKE_CODEX_LOG = Join-Path $TestRoot "codex-mcp.log"
$FakeCodex = @'
@echo off
if "%1 %2 %3" == "mcp get holycrab" (
  echo command: C:\old\holycrab_cli.py mcp serve
  exit /b 0
)
echo %*>>"%FAKE_CODEX_LOG%"
exit /b 0
'@
[IO.File]::WriteAllText((Join-Path $FakeBin "codex.cmd"), $FakeCodex, [Text.Encoding]::ASCII)
$env:Path = "$FakeBin;$env:Path"
$env:HOLYCRAB_INSTALL_MCP = "1"
$env:HOLYCRAB_INSTALL_AGENTS = "codex"
& (Join-Path $RepoRoot "install.ps1")
$McpLog = Get-Content -LiteralPath $env:FAKE_CODEX_LOG -Raw
if ($McpLog -notmatch "mcp remove holycrab") { throw "Stale HolyCrab MCP was not removed" }
if ($McpLog -notmatch "mcp add holycrab" -or -not $McpLog.Contains($CliPath)) {
    throw "HolyCrab MCP was not restored with the current CLI path"
}

$ManifestText = Get-Content -LiteralPath (Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\installation.json") -Raw
if ($ManifestText.Contains($SavedKey) -or $ManifestText.Contains($LegacyKey)) { throw "installation.json contains an API Key" }

& $Python (Join-Path $PSScriptRoot "windows_mcp_smoke.py") $CliPath
if ($LASTEXITCODE -ne 0) { throw "MCP smoke test failed" }
