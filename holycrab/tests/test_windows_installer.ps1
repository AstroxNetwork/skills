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
if ($Version -ne "holycrab 0.4.2") { throw "Unexpected version: $Version" }

$Doctor = & $Launcher doctor --json | ConvertFrom-Json
if ($Doctor.ok -ne $true) { throw "HolyCrab doctor did not report ok" }
if ($Doctor.onboarding.state -ne "CONNECT_ACCOUNT") { throw "Unconfigured account falsely reported ready" }

# Execute the actual colleague command against a fixed GitHub commit, not a version-string replacement.
if ($env:HOLYCRAB_TEST_INSTALL_REF) {
    $PreviousSourceDir = $env:HOLYCRAB_INSTALL_SOURCE_DIR
    $PreviousRef = $env:HOLYCRAB_INSTALL_REF
    $env:HOLYCRAB_INSTALL_SOURCE_DIR = $null
    $ref = $env:HOLYCRAB_TEST_INSTALL_REF
    try {
        $oldRef=$env:HOLYCRAB_INSTALL_REF; try { $env:HOLYCRAB_INSTALL_REF=$ref; & ([scriptblock]::Create((irm "https://raw.githubusercontent.com/AstroxNetwork/skills/$ref/install.ps1"))) } finally { $env:HOLYCRAB_INSTALL_REF=$oldRef }
        if ($env:HOLYCRAB_INSTALL_REF -ne $PreviousRef) { throw "Temporary download ref was not restored" }
        $PinnedVersion = & $Launcher --version
        if ($LASTEXITCODE -ne 0 -or $PinnedVersion -ne "holycrab 0.4.2") { throw "Fixed-commit Windows installation failed" }
        $PinnedHelp = & $Launcher --help | Out-String
        if ($PinnedHelp -notmatch "uninstall") { throw "Fixed-commit Windows installation omitted uninstall" }
    } finally {
        $env:HOLYCRAB_INSTALL_SOURCE_DIR = $PreviousSourceDir
        $env:HOLYCRAB_INSTALL_REF = $PreviousRef
    }
}

$CliPath = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\holycrab_cli.py"
$Python = (Get-Command python).Source

$SavedKey = "hc_test_windows_dpapi_123456789"
$SetupOutput = ($SavedKey | & $Launcher setup --stdin --no-verify | Out-String)
if ($SetupOutput.Contains($SavedKey)) { throw "API Key leaked in setup output" }
$SetupJson = $SetupOutput | ConvertFrom-Json
if ($SetupJson.valid -ne $false -or $SetupJson.onboarding.state -ne "VERIFY_ACCOUNT") { throw "Unverified setup falsely reported connected" }
$ConfigPath = Join-Path $env:HOLYCRAB_CONFIG_DIR "config.json"
$ConfigText = Get-Content -LiteralPath $ConfigPath -Raw
$Config = $ConfigText | ConvertFrom-Json
if ($Config.PSObject.Properties.Name -contains "apiKey") { throw "Windows config retained plaintext apiKey" }
if (-not ($Config.PSObject.Properties.Name -contains "apiKeyDpapi")) { throw "Windows config omitted DPAPI ciphertext" }
if ($ConfigText.Contains($SavedKey)) { throw "Windows config contains plaintext API Key" }

$OriginalCli = Get-Content -LiteralPath $CliPath -Raw -Encoding UTF8
foreach ($OldVersion in @("0.4.0", "0.4.1")) {
    [IO.File]::WriteAllText($CliPath, $OriginalCli.Replace('VERSION = "0.4.2"', ('VERSION = "' + $OldVersion + '"')), [Text.UTF8Encoding]::new($false))
    & (Join-Path $RepoRoot "install.ps1")
    if ((Get-Content -LiteralPath $ConfigPath -Raw) -ne $ConfigText) { throw "Upgrade changed the saved DPAPI credential" }
    $UpgradedVersion = & $Launcher --version
    if ($UpgradedVersion -ne "holycrab 0.4.2") { throw "Older-version upgrade failed" }
    $UpgradedDoctor = & $Launcher doctor --json | ConvertFrom-Json
    if ($UpgradedDoctor.onboarding.state -ne "VERIFY_ACCOUNT") { throw "Upgrade confused saved credentials with verified login" }
}

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
$env:FAKE_CODEX_STATE = Join-Path $TestRoot "codex-mcp-state.txt"
$env:PYTHON_FOR_HOLYCRAB_TEST = $Python
$FakeCodexPython = @'
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
state = Path(os.environ["FAKE_CODEX_STATE"])
if arguments[:3] == ["mcp", "get", "holycrab"]:
    if state.exists():
        print(state.read_text(encoding="utf-8"))
        raise SystemExit(0)
    print("No MCP server named 'holycrab' found.", file=sys.stderr)
    raise SystemExit(1)
with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(arguments) + "\n")
if arguments[:3] == ["mcp", "remove", "holycrab"]:
    state.unlink(missing_ok=True)
elif arguments[:3] == ["mcp", "add", "holycrab"]:
    server = arguments[arguments.index("--") + 1:]
    state.write_text(json.dumps({"transport": {"type": "stdio", "command": server[0], "args": server[1:]}}), encoding="utf-8")
'@
[IO.File]::WriteAllText((Join-Path $FakeBin "fake_codex.py"), $FakeCodexPython, [Text.UTF8Encoding]::new($false))
$FakeCodex = @'
@echo off
"%PYTHON_FOR_HOLYCRAB_TEST%" "%~dp0fake_codex.py" %*
'@
[IO.File]::WriteAllText((Join-Path $FakeBin "codex.cmd"), $FakeCodex, [Text.Encoding]::ASCII)
$env:Path = "$FakeBin;$env:Path"
$env:HOLYCRAB_INSTALL_MCP = "1"
$env:HOLYCRAB_INSTALL_AGENTS = "codex"
& (Join-Path $RepoRoot "install.ps1")
# Simulate an installer-owned registration pointing to an old Python executable.
$OldRegistration = Get-Content -LiteralPath $env:FAKE_CODEX_STATE -Raw -Encoding UTF8 | ConvertFrom-Json
$OldRegistration.transport.command = "C:\old\python.exe"
[IO.File]::WriteAllText($env:FAKE_CODEX_STATE, ($OldRegistration | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$OwnedManifestPath = Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\installation.json"
$OwnedManifest = Get-Content -LiteralPath $OwnedManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$OwnedManifest.agentRegistrations.codex.command = "C:\old\python.exe"
[IO.File]::WriteAllText($OwnedManifestPath, ($OwnedManifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
& (Join-Path $RepoRoot "install.ps1")
$McpLog = Get-Content -LiteralPath $env:FAKE_CODEX_LOG -Raw -Encoding UTF8
if ($McpLog -notmatch "mcp remove holycrab") { throw "Stale HolyCrab MCP was not removed" }
if ($McpLog -notmatch "mcp add holycrab" -or -not $McpLog.Contains($CliPath)) {
    throw "HolyCrab MCP was not restored with the current CLI path"
}

$ManifestText = Get-Content -LiteralPath (Join-Path $env:HOLYCRAB_INSTALL_PREFIX "lib\holycrab\installation.json") -Raw -Encoding UTF8
if ($ManifestText.Contains($SavedKey) -or $ManifestText.Contains($LegacyKey)) { throw "installation.json contains an API Key" }
$Manifest = $ManifestText | ConvertFrom-Json
if ($Manifest.schemaVersion -ne 2 -or $Manifest.managedBy -ne "holycrab-installer") { throw "Installation ownership metadata is missing" }
if ($Manifest.agentRegistrations.codex.executable -ne (Join-Path $FakeBin "codex.cmd") -or
    $Manifest.agentRegistrations.codex.managed -ne $true) { throw "Actual Agent client path and ownership were not recorded" }
if ($Manifest.pathRegistration.kind -ne "windows-user-path" -or $Manifest.pathRegistration.addedByInstaller -ne $true) {
    $PathRegistrationJson = $Manifest.pathRegistration | ConvertTo-Json -Compress
    throw "Windows PATH ownership was not preserved across reinstall: $PathRegistrationJson; expected directory: $BinDir"
}

& $Python (Join-Path $PSScriptRoot "windows_mcp_smoke.py") $CliPath
if ($LASTEXITCODE -ne 0) { throw "MCP smoke test failed" }

# The ordinary terminal no longer has the installation Agent's injected PATH.
$env:Path = (@($env:Path -split ";" | Where-Object { $_ -and $_ -ine $FakeBin }) -join ";")
$UninstallOutput = & $Launcher uninstall --yes | Out-String
if ($LASTEXITCODE -ne 0) { throw "Default Windows uninstall failed" }
if ($UninstallOutput -notmatch "cleanup is scheduled" -or $UninstallOutput -match "uninstall completed") {
    throw "Windows uninstall feedback incorrectly claims synchronous completion"
}
if ($UninstallOutput.Contains($SavedKey) -or $UninstallOutput.Contains($LegacyKey)) { throw "API Key leaked in uninstall output" }
if ($UninstallOutput -match "MCP cleanup pending" -or (Test-Path $env:FAKE_CODEX_STATE)) {
    throw "Recorded Agent path did not clean the MCP registration outside PATH"
}
for ($Attempt = 0; $Attempt -lt 100 -and ((Test-Path $Launcher) -or (Test-Path $CliPath)); $Attempt++) {
    Start-Sleep -Milliseconds 100
}
if ((Test-Path $Launcher) -or (Test-Path $CliPath)) {
    $CleanupLog = Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Filter "holycrab-uninstall-*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $CleanupDetail = if ($CleanupLog) { Get-Content -LiteralPath $CleanupLog.FullName -Raw -Encoding UTF8 } else { "no cleanup log" }
    throw "Windows self-uninstall did not finish within 10 seconds: $CleanupDetail"
}
if (-not (Test-Path $ConfigPath)) { throw "Default Windows uninstall removed local configuration" }
$UserPathAfterUninstall = [Environment]::GetEnvironmentVariable("Path", "User")
if (@($UserPathAfterUninstall -split ";" | Where-Object {
    $_ -and $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq $BinDir.TrimEnd([IO.Path]::DirectorySeparatorChar)
}).Count -ne 0) { throw "Windows uninstall retained its managed user PATH entry" }

& (Join-Path $RepoRoot "install.ps1")
& $Python $MigrationScript $CliPath $LegacyKey
if ($LASTEXITCODE -ne 0) { throw "Reinstall could not read the preserved API Key" }
& $Launcher uninstall --purge --yes
if ($LASTEXITCODE -ne 0) { throw "Purging Windows uninstall failed" }
for ($Attempt = 0; $Attempt -lt 100 -and ((Test-Path $Launcher) -or (Test-Path $CliPath)); $Attempt++) {
    Start-Sleep -Milliseconds 100
}
if ((Test-Path $Launcher) -or (Test-Path $CliPath)) { throw "Purging Windows self-uninstall did not finish within 10 seconds" }
if (Test-Path $env:HOLYCRAB_CONFIG_DIR) { throw "Purging Windows uninstall retained known local state" }
