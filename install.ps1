$ErrorActionPreference = "Stop"

$Repository = "AstroxNetwork/skills"
$Version = "v0.6.0"
$SourceRef = if ($env:HOLYCRAB_INSTALL_REF) { $env:HOLYCRAB_INSTALL_REF } else { $Version }
if ($SourceRef -cnotmatch '^(?:[0-9a-f]{40}|v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$') {
    throw "HOLYCRAB_INSTALL_REF must be a full commit hash or stable version tag; installation stopped."
}
$SourceDir = $env:HOLYCRAB_INSTALL_SOURCE_DIR
$InstallMcp = [string]$env:HOLYCRAB_INSTALL_MCP
$InstallAgents = [string]$env:HOLYCRAB_INSTALL_AGENTS
$Prefix = if ($env:HOLYCRAB_INSTALL_PREFIX) { $env:HOLYCRAB_INSTALL_PREFIX } else { Join-Path $HOME ".local" }
$BinDir = Join-Path $Prefix "bin"
$LibDir = Join-Path $Prefix "lib\holycrab"
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("holycrab-install." + [Guid]::NewGuid().ToString("N"))
$BackupDir = Join-Path $TempDir "backup"
$InstallLockHeld = $false
$InstallLockDir = Join-Path $Prefix ".holycrab-install.lock"
$InstallStarted = $false
$InstallComplete = $false
$PathRegistration = $null
$PathAddedThisRun = $false
$PreviousPathManaged = $false

function Write-HolyCrabProgress([string]$Message) {
    if (-not [Console]::IsErrorRedirected) {
        [Console]::Error.WriteLine($Message)
        [Console]::Error.Flush()
    }
}

function Format-HolyCrabResult([hashtable]$Python, [string]$CliPath, [object]$Value, [string]$Mode, [string]$Upgrading = "0") {
    $SummaryHelper = @'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("holycrab_installer", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
value = json.loads(sys.stdin.read().lstrip("\ufeff"))
if sys.argv[3] == "doctor":
    print(cli.format_terminal_result(value))
else:
    print(cli.format_onboarding(value, installed=True, upgrading=sys.argv[2] == "1"))
'@
    # PS 5.1 does not reliably preserve Python -c quoting. Use a private file.
    $HelperPath = Join-Path $TempDir "summary.py"
    [IO.File]::WriteAllText($HelperPath, $SummaryHelper, [Text.UTF8Encoding]::new($false))
    $Arguments = @($Python.Arguments) + @("-X", "utf8", $HelperPath, $CliPath, $Upgrading, $Mode)
    $PreviousOutputEncoding = $OutputEncoding
    try {
        $OutputEncoding = [Text.UTF8Encoding]::new($false)
        $Result = $Value | ConvertTo-Json -Depth 12 -Compress | & $Python.Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Could not prepare the installation summary" }
        return $Result -join [Environment]::NewLine
    } finally {
        $OutputEncoding = $PreviousOutputEncoding
    }
}

$ReleaseFiles = @(
    @{ Relative = "holycrab/scripts/holycrab_cli.py"; Name = "holycrab_cli.py"; Sha256 = "ce732cda8548c77b53b513321fad5a52d5c695a02b04a9d5f45b240f302ab2fb" },
    @{ Relative = "holycrab/references/capabilities.json"; Name = "capabilities.json"; Sha256 = "7070c245eae8b022f0dfd678acb660a61a58c65644f3c0076edae8e4eec65eb4" },
    @{ Relative = "holycrab/SKILL.md"; Name = "SKILL.md"; Sha256 = "307c29ea603719485b21cc5e45087dd23417d56455cf121ef8b0236016d30a37" },
    @{ Relative = "holycrab/agents/openai.yaml"; Name = "openai.yaml"; Sha256 = "64bd549cd32e989324d5a17c2550cd54dfecccf70b4637b05b062a2fb709c1a7" },
    @{ Relative = "holycrab/scripts/vendor/segno-1.6.6-py3-none-any.whl"; Name = "segno.whl"; Sha256 = "28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7" },
    @{ Relative = "holycrab/scripts/vendor/LICENSE.segno"; Name = "LICENSE.segno"; Sha256 = "de6c85fccf5d52902aa13dfe2dc6d2a2a106fc3419ed438f3460f0d4b76a6935" },
# BEGIN GENERATED RUNTIME DOWNLOADS
    @{ Relative = "runtime-files.json"; Name = "runtime-files.json"; Sha256 = "46fb2b09a7cb8d606895b739e5ee977c36b1be7a4f7b455996800a88e4954e58" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/__init__.py"; Name = "holycrab_runtime/__init__.py"; Sha256 = "a957b310045f02b36e31a8fd60f5a3808b0838b59fd353c2a5318d0cceeb2ffe" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/capabilities.py"; Name = "holycrab_runtime/capabilities.py"; Sha256 = "0719040a1572e640980d6d216023f6060be5d2887f9001d3a5762f9f792372f7" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/cli.py"; Name = "holycrab_runtime/cli.py"; Sha256 = "05796328dcf726f22ea3bdab3242864d92ed6c4dbcf3d92822ce9d0b277c9200" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/exports.py"; Name = "holycrab_runtime/exports.py"; Sha256 = "28d6cdade7d99b88267f320488b1393a7881066b4d8bef5bc99bc36644336e5e" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/generation.py"; Name = "holycrab_runtime/generation.py"; Sha256 = "404a1e109a68d11a584340b4c6ef53401239e6322d5dc77fd0996b81569824d4" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/lifecycle.py"; Name = "holycrab_runtime/lifecycle.py"; Sha256 = "712895cc2ba5e1ee49cf64d04447e2ffc81684c2b8ec636399b06a814bf10a33" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/mcp.py"; Name = "holycrab_runtime/mcp.py"; Sha256 = "a40c9712251bcb0b61c4ecb972b3f8f6f2f527ed5f1ab2f53da9dee1ae87914c" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/presentation.py"; Name = "holycrab_runtime/presentation.py"; Sha256 = "e9f8c81062f3c1c3c7a116157958a070f546643582a7680d9c81876c1ff5c504" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/real_human.py"; Name = "holycrab_runtime/real_human.py"; Sha256 = "7ef4f7bef0cc2100e007f1a88ef0371c8c03c31c33eb7c2692b0fb228618cb9f" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/results.py"; Name = "holycrab_runtime/results.py"; Sha256 = "5d57e9c895f100f5e867f27a8662bfef05e8b1b4db7ae47218700860e7926853" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/settings.py"; Name = "holycrab_runtime/settings.py"; Sha256 = "80fce97557e6349f360b4e51c96f540c9f5a464310d5415e436cf3bdbb3aa309" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/state.py"; Name = "holycrab_runtime/state.py"; Sha256 = "3c12482ef7192ac7212f422b078ce0eecc0764130593f2e28b2dab0283c52dd1" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/tasks.py"; Name = "holycrab_runtime/tasks.py"; Sha256 = "2936d1574e9cb6caa5d7e6a21af5a2a55042972620ccf271264ae35d2fa7bc27" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/transport.py"; Name = "holycrab_runtime/transport.py"; Sha256 = "72c1c69d9dfd2de9c0d699f08ba43f5b59fcac8ae370490f7390704d55a5dd16" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/uploads.py"; Name = "holycrab_runtime/uploads.py"; Sha256 = "6d5a3edfff505c0501403d8d2e6e5f8c56847291b0a0673a33fbf9d98f6f4b10" }
# END GENERATED RUNTIME DOWNLOADS
)

function Find-HolyCrabPython {
    $Candidates = @(
        @{ Command = "py"; Arguments = @("-3") },
        @{ Command = "python"; Arguments = @() },
        @{ Command = "python3"; Arguments = @() }
    )
    foreach ($Candidate in $Candidates) {
        $Resolved = Get-Command $Candidate.Command -ErrorAction SilentlyContinue
        if (-not $Resolved) { continue }
        $VersionArguments = @($Candidate.Arguments) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        & $Resolved.Source @VersionArguments
        if ($LASTEXITCODE -eq 0) {
            return @{ Executable = $Resolved.Source; Arguments = @($Candidate.Arguments) }
        }
    }
    throw "Python 3.10 or newer is required for HolyCrab. Install it, then rerun this command: winget install --id Python.Python.3.12 -e"
}

function Copy-ReleaseFile([hashtable]$File) {
    if ($File.Name -eq "holycrab_cli.py") {
        Write-HolyCrabProgress "[2/5] Downloading and verifying installation files..."
    }
    $Destination = Join-Path $TempDir $File.Name
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    if ($SourceDir) {
        Copy-Item -LiteralPath (Join-Path $SourceDir $File.Relative) -Destination $Destination
    } else {
        $Url = "https://raw.githubusercontent.com/$Repository/$SourceRef/$($File.Relative)"
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
    if ($Actual -ne $File.Sha256) {
        throw "SHA-256 verification failed for $($File.Relative); installation stopped."
    }
    return $Destination
}

function Install-HolyCrabSkill([string]$Destination, [hashtable]$Downloaded) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Destination "references"), (Join-Path $Destination "agents") | Out-Null
    Copy-Item -LiteralPath $Downloaded.Skill -Destination (Join-Path $Destination "SKILL.md") -Force
    Copy-Item -LiteralPath $Downloaded.Capabilities -Destination (Join-Path $Destination "references\capabilities.json") -Force
    Copy-Item -LiteralPath $Downloaded.OpenAI -Destination (Join-Path $Destination "agents\openai.yaml") -Force
}

function Add-HolyCrabPath([string]$Directory, [bool]$PreviouslyManaged) {
    $CurrentEntries = @($env:Path -split ";" | Where-Object { $_ })
    if (-not ($CurrentEntries | Where-Object {
        $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq $Directory.TrimEnd([IO.Path]::DirectorySeparatorChar)
    })) {
        $env:Path = "$Directory;$env:Path"
    }

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $UserEntries = @($UserPath -split ";" | Where-Object { $_ })
    $Added = $false
    if (-not ($UserEntries | Where-Object {
        $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq $Directory.TrimEnd([IO.Path]::DirectorySeparatorChar)
    })) {
        $Updated = if ($UserPath) { "$Directory;$UserPath" } else { $Directory }
        [Environment]::SetEnvironmentVariable("Path", $Updated, "User")
        $Added = $true
    }
    return @{
        Registration = @{ kind = "windows-user-path"; directory = $Directory; profile = $null; addedByInstaller = ($Added -or $PreviouslyManaged) }
        AddedThisRun = $Added
    }
}

function Register-HolyCrabMcp([string]$Agent, [hashtable]$Python, [string]$CliPath) {
    $Resolved = Get-Command $Agent -ErrorAction SilentlyContinue
    $ClientPath = if ($Resolved) { $Resolved.Source } else { "--discover" }
    $ServerCommand = @($Python.Executable) + @($Python.Arguments) + @("-X", "utf8", $CliPath, "mcp", "serve")
    $Helper = @'
import importlib.util, json, sys
script, agent, client, previous = sys.argv[1:5]
server = json.loads(sys.stdin.read().lstrip("\ufeff"))
spec = importlib.util.spec_from_file_location("holycrab_installer", script)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
cli.register_installer_mcp(agent, client or None, server[0], server[1:], previous,
                         str(__import__("pathlib").Path(previous).parent.parent / "mcp-transaction.json"))
'@
    $HelperPath = Join-Path $TempDir "register-mcp.py"
    [IO.File]::WriteAllText($HelperPath, $Helper, [Text.UTF8Encoding]::new($false))
    $HelperArguments = @($Python.Arguments) + @("-X", "utf8", $HelperPath, $CliPath, $Agent, $ClientPath, (Join-Path $BackupDir "lib\installation.json"))
    $PreviousOutputEncoding = $OutputEncoding
    try {
        $OutputEncoding = [Text.UTF8Encoding]::new($false)
        (ConvertTo-Json -InputObject $ServerCommand -Compress) | & $Python.Executable @HelperArguments
    } finally {
        $OutputEncoding = $PreviousOutputEncoding
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Agent connection recovery failed; rolling back installation."
    }
}

Write-HolyCrabProgress "[1/5] Checking Python and installation settings..."
$Python = Find-HolyCrabPython
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
try {
    $Downloaded = @{}
    foreach ($File in $ReleaseFiles) {
        $Downloaded[$File.Name] = Copy-ReleaseFile $File
    }

    New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
    # os.mkdir uses exclusive OS directory creation, including under PowerShell 5.1.
    $LockArguments = @($Python.Arguments) + @("-c", "import os,sys;os.mkdir(sys.argv[1])", $InstallLockDir)
    & $Python.Executable @LockArguments
    if ($LASTEXITCODE -ne 0) { throw "Another lifecycle operation holds $InstallLockDir; check it before recovering an abandoned lock." }
    $InstallLockHeld = $true
    $SettingsHelper = @'
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("holycrab_settings", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
settings = json.loads(sys.stdin.read().lstrip("\ufeff"))
previous = cli.read_json_file(pathlib.Path(settings["prefix"]) / "lib/holycrab/installation.json", {})
print(json.dumps(cli.installer_settings(previous, **settings)))
'@
    $SettingsHelperPath = Join-Path $TempDir "settings.py"
    [IO.File]::WriteAllText($SettingsHelperPath, $SettingsHelper, [Text.UTF8Encoding]::new($false))
    $SettingsArguments = @($Python.Arguments) + @("-X", "utf8", $SettingsHelperPath, $Downloaded["holycrab_cli.py"])
    $SavedOutputEncoding = $OutputEncoding
    try {
        $OutputEncoding = [Text.UTF8Encoding]::new($false)
        $Settings = @{ prefix = $Prefix; agents = $InstallAgents; mcp = $InstallMcp } |
            ConvertTo-Json -Compress | & $Python.Executable @SettingsArguments | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw "Could not resolve the existing installation settings" }
    } finally { $OutputEncoding = $SavedOutputEncoding }
    $InstallAgents = $Settings.agents
    $InstallMcp = $Settings.mcp

    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $PreviousManifestPath = Join-Path $LibDir "installation.json"
    if (Test-Path -LiteralPath $PreviousManifestPath) {
        try {
            $PreviousManifest = Get-Content -LiteralPath $PreviousManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $PreviousPathDirectory = [string]$PreviousManifest.pathRegistration.directory
            $PreviousPathManaged = (
                $PreviousManifest.pathRegistration.addedByInstaller -eq $true -and
                $PreviousPathDirectory -ieq $BinDir
            )
        } catch {
            $PreviousPathManaged = $false
        }
    }
    if (Test-Path -LiteralPath $LibDir) { Copy-Item -LiteralPath $LibDir -Destination (Join-Path $BackupDir "lib") -Recurse }
    $ExistingLauncher = Join-Path $BinDir "holycrab.cmd"
    if (Test-Path -LiteralPath $ExistingLauncher) { Copy-Item -LiteralPath $ExistingLauncher -Destination (Join-Path $BackupDir "holycrab.cmd") }
    if (Test-Path -LiteralPath (Join-Path $HOME ".agents\skills\holycrab")) {
        Copy-Item -LiteralPath (Join-Path $HOME ".agents\skills\holycrab") -Destination (Join-Path $BackupDir "codex-skill") -Recurse
    }
    if (Test-Path -LiteralPath (Join-Path $HOME ".claude\skills\holycrab")) {
        Copy-Item -LiteralPath (Join-Path $HOME ".claude\skills\holycrab") -Destination (Join-Path $BackupDir "claude-skill") -Recurse
    }
    Write-HolyCrabProgress "[3/5] Installing verified program files and configuring PATH..."
    $InstallStarted = $true
    New-Item -ItemType Directory -Force -Path $BinDir, (Join-Path $LibDir "references"), (Join-Path $LibDir "vendor") | Out-Null
    $CliPath = Join-Path $LibDir "holycrab_cli.py"
    Copy-Item -LiteralPath $Downloaded["holycrab_cli.py"] -Destination $CliPath -Force
    Copy-Item -LiteralPath $Downloaded["capabilities.json"] -Destination (Join-Path $LibDir "references\capabilities.json") -Force
    Copy-Item -LiteralPath $Downloaded["segno.whl"] -Destination (Join-Path $LibDir "vendor\segno-1.6.6-py3-none-any.whl") -Force
    Copy-Item -LiteralPath $Downloaded["LICENSE.segno"] -Destination (Join-Path $LibDir "vendor\LICENSE.segno") -Force

# BEGIN GENERATED RUNTIME INSTALL
    New-Item -ItemType Directory -Force -Path (Join-Path $LibDir "holycrab_runtime") | Out-Null
    Copy-Item -LiteralPath $Downloaded["runtime-files.json"] -Destination (Join-Path $LibDir "runtime-files.json") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/__init__.py"] -Destination (Join-Path $LibDir "holycrab_runtime/__init__.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/capabilities.py"] -Destination (Join-Path $LibDir "holycrab_runtime/capabilities.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/cli.py"] -Destination (Join-Path $LibDir "holycrab_runtime/cli.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/exports.py"] -Destination (Join-Path $LibDir "holycrab_runtime/exports.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/generation.py"] -Destination (Join-Path $LibDir "holycrab_runtime/generation.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/lifecycle.py"] -Destination (Join-Path $LibDir "holycrab_runtime/lifecycle.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/mcp.py"] -Destination (Join-Path $LibDir "holycrab_runtime/mcp.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/presentation.py"] -Destination (Join-Path $LibDir "holycrab_runtime/presentation.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/real_human.py"] -Destination (Join-Path $LibDir "holycrab_runtime/real_human.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/results.py"] -Destination (Join-Path $LibDir "holycrab_runtime/results.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/settings.py"] -Destination (Join-Path $LibDir "holycrab_runtime/settings.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/state.py"] -Destination (Join-Path $LibDir "holycrab_runtime/state.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/tasks.py"] -Destination (Join-Path $LibDir "holycrab_runtime/tasks.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/transport.py"] -Destination (Join-Path $LibDir "holycrab_runtime/transport.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/uploads.py"] -Destination (Join-Path $LibDir "holycrab_runtime/uploads.py") -Force
# END GENERATED RUNTIME INSTALL

    $CacheHelper = @'
import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location("holycrab_cache",sys.argv[1])
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
cli.prepare_runtime_imports(pathlib.Path(sys.argv[2]))
'@
    $CachePath = Join-Path $TempDir "clear-cache.py"
    [IO.File]::WriteAllText($CachePath, $CacheHelper, [Text.UTF8Encoding]::new($false))
    $CacheArguments = @($Python.Arguments) + @("-X", "utf8", $CachePath, $Downloaded["holycrab_cli.py"], $LibDir)
    & $Python.Executable @CacheArguments
    if ($LASTEXITCODE -ne 0) { throw "Could not invalidate old runtime bytecode" }

    $PythonInvocation = '"' + $Python.Executable + '"'
    if ($Python.Arguments.Count -gt 0) { $PythonInvocation += " " + ($Python.Arguments -join " ") }
    $Launcher = Join-Path $BinDir "holycrab.cmd"
    $LauncherContent = "@echo off`r`nchcp 65001 >nul`r`nset `"PYTHONUTF8=1`"`r`nset `"PYTHONIOENCODING=utf-8`"`r`n$PythonInvocation `"%~dp0..\lib\holycrab\holycrab_cli.py`" %*`r`nset `"_HOLYCRAB_EXIT_CODE=%ERRORLEVEL%`"`r`nif exist `"%~dp0..\lib\holycrab\holycrab_cli.py`" exit /b %_HOLYCRAB_EXIT_CODE%`r`n(goto) 2>nul & del /f /q `"%~f0`" >nul 2>&1`r`n"
    [IO.File]::WriteAllText($Launcher, $LauncherContent, [Text.UTF8Encoding]::new($false))

    $PathResult = Add-HolyCrabPath $BinDir $PreviousPathManaged
    $PathRegistration = $PathResult.Registration
    $PathAddedThisRun = $PathResult.AddedThisRun

    $Manifest = @{
        schemaVersion = 3
        managedBy = "holycrab-installer"
        version = $Version.TrimStart("v")
        prefix = $Prefix
        agents = @($InstallAgents -split "," | Where-Object { $_ -and $_ -ne "none" })
        mcp = $InstallMcp -eq "1"
        coreFiles = @(
            @{ path = "holycrab_cli.py"; sha256 = $ReleaseFiles[0].Sha256 }
            @{ path = "references/capabilities.json"; sha256 = $ReleaseFiles[1].Sha256 }
            @{ path = "vendor/segno-1.6.6-py3-none-any.whl"; sha256 = $ReleaseFiles[4].Sha256 }
            @{ path = "vendor/LICENSE.segno"; sha256 = $ReleaseFiles[5].Sha256 }
            @{ path = "../../bin/holycrab.cmd"; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Launcher).Hash.ToLowerInvariant() }
        )
        pathRegistration = $PathRegistration
    }
    $RuntimeManifest = Get-Content -LiteralPath (Join-Path $LibDir "runtime-files.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($Entry in $RuntimeManifest.files) {
        if ($Entry.kind -eq "runtime" -and -not $Entry.legacyVariable) {
            $Manifest.coreFiles += @{ path = $Entry.target; sha256 = $Entry.sha256 }
        }
    }
    $Manifest.coreFiles += @{ path = "runtime-files.json"; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $LibDir "runtime-files.json")).Hash.ToLowerInvariant() }
    $SkillRoots = @{
        codex = (Join-Path $HOME ".agents\skills\holycrab")
        claude = (Join-Path $HOME ".claude\skills\holycrab")
    }
    foreach ($Agent in $Manifest.agents) {
        if ($SkillRoots.ContainsKey($Agent)) {
            $Root = $SkillRoots[$Agent]
            $Manifest.coreFiles += @(
                @{ path = (Join-Path $Root "SKILL.md"); sha256 = $ReleaseFiles[2].Sha256 }
                @{ path = (Join-Path $Root "references\capabilities.json"); sha256 = $ReleaseFiles[1].Sha256 }
                @{ path = (Join-Path $Root "agents\openai.yaml"); sha256 = $ReleaseFiles[3].Sha256 }
            )
        }
    }
    [IO.File]::WriteAllText((Join-Path $LibDir "installation.json"), ($Manifest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))

    Write-HolyCrabProgress "[4/5] Configuring the selected Agents..."
    if (",$InstallAgents," -like "*,codex,*") {
        Install-HolyCrabSkill (Join-Path $HOME ".agents\skills\holycrab") @{
            Skill = $Downloaded["SKILL.md"]; Capabilities = $Downloaded["capabilities.json"]; OpenAI = $Downloaded["openai.yaml"]
        }
    }
    if (",$InstallAgents," -like "*,claude,*") {
        Install-HolyCrabSkill (Join-Path $HOME ".claude\skills\holycrab") @{
            Skill = $Downloaded["SKILL.md"]; Capabilities = $Downloaded["capabilities.json"]; OpenAI = $Downloaded["openai.yaml"]
        }
    }

    if ($InstallMcp -eq "1") {
        if (",$InstallAgents," -like "*,codex,*") { Register-HolyCrabMcp "codex" $Python $CliPath }
        if (",$InstallAgents," -like "*,claude,*") { Register-HolyCrabMcp "claude" $Python $CliPath }
    }

    $FinalizeHelper = @'
import importlib.util,sys
spec=importlib.util.spec_from_file_location("holycrab_finalize",sys.argv[1])
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
cli.finalize_installation(sys.argv[2],sys.argv[3])
'@
    $FinalizePath = Join-Path $TempDir "finalize.py"
    [IO.File]::WriteAllText($FinalizePath, $FinalizeHelper, [Text.UTF8Encoding]::new($false))
    $FinalizeArgs = @($Python.Arguments) + @("-X", "utf8", $FinalizePath, $CliPath, (Join-Path $BackupDir "lib/installation.json"), (Join-Path $BackupDir "mcp-transaction.json"))
    & $Python.Executable @FinalizeArgs
    if ($LASTEXITCODE -ne 0) { throw "Could not finalize installation ownership" }

    Write-HolyCrabProgress "[5/5] Verifying the installed CLI version and local health..."
    $PreviousNoUpdate = $env:HOLYCRAB_NO_UPDATE_CHECK
    $env:HOLYCRAB_NO_UPDATE_CHECK = "1"
    try {
        $InstalledVersion = & $Launcher --version
        if ($LASTEXITCODE -ne 0 -or $InstalledVersion -ne ("holycrab " + $Version.TrimStart("v"))) {
            throw "Installed CLI version does not match $Version; installation stopped."
        }
        $Doctor = & $Launcher doctor --json | ConvertFrom-Json
        if (-not $Doctor.runtimeOk) {
            Write-Warning (Format-HolyCrabResult $Python $CliPath $Doctor "doctor")
            throw "holycrab runtime verification failed after installation"
        }
    } finally {
        $env:HOLYCRAB_NO_UPDATE_CHECK = $PreviousNoUpdate
    }
    if (-not $Doctor.ok) { Write-Warning "Program installed; existing local records need attention. Run holycrab doctor and preserve the records." }
    $Upgrading = if (Test-Path -LiteralPath (Join-Path $BackupDir "lib")) { "1" } else { "0" }
    $Summary = Format-HolyCrabResult $Python $CliPath $Doctor.onboarding "onboarding" $Upgrading
    $InstallComplete = $true
    Write-Host ""
    Write-Host ($Summary -join [Environment]::NewLine)
    Write-Host ""
    Write-Host "Installed command:"
    Write-Host "  $Launcher"
    Write-Host ""
    Write-Host "PATH is ready in this PowerShell session and future sessions."
} finally {
    $RecoveryComplete = $true
    if ($InstallStarted -and -not $InstallComplete) {
        try {
            $RestoreHelper = @'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("holycrab_rollback", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
cli.restore_installer_files(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]),
    [a for a in sys.argv[4].split(",") if a != "none"])
'@
            $RestorePath = Join-Path $TempDir "restore.py"
            [IO.File]::WriteAllText($RestorePath, $RestoreHelper, [Text.UTF8Encoding]::new($false))
            $RestoreArguments = @($Python.Arguments) + @("-X", "utf8", $RestorePath, $Downloaded["holycrab_cli.py"], $BackupDir, $Prefix, $InstallAgents)
            & $Python.Executable @RestoreArguments
            if ($LASTEXITCODE -ne 0) { throw "Could not restore all managed files and Agent connections" }
        } catch {
            $RecoveryComplete = $false
            Write-Warning $_.Exception.Message
        }
        try { if ($PathAddedThisRun) {
            $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
            $Kept = @($UserPath -split ";" | Where-Object {
                $_ -and $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $BinDir.TrimEnd([IO.Path]::DirectorySeparatorChar)
            })
            [Environment]::SetEnvironmentVariable("Path", ($Kept -join ";"), "User")
            $env:Path = (@($env:Path -split ";" | Where-Object { $_ -and $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ine $BinDir.TrimEnd([IO.Path]::DirectorySeparatorChar) }) -join ";")
        } } catch { $RecoveryComplete = $false; Write-Warning $_.Exception.Message }
        if ($RecoveryComplete) {
            Write-Warning "HolyCrab installation failed; previous managed files were restored."
        } else {
            Write-Warning "HolyCrab installation failed; recovery is incomplete. Backup retained: $BackupDir"
        }
    }
    if ($RecoveryComplete) { Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue }
    if ($InstallLockHeld) {
        try { [IO.Directory]::Delete($InstallLockDir, $false) }
        catch { Write-Warning "Could not remove installation lock: $InstallLockDir" }
    }
}
