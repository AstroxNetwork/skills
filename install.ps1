$ErrorActionPreference = "Stop"

$Repository = "AstroxNetwork/skills"
$Version = "v0.4.4"
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
    @{ Relative = "holycrab/scripts/holycrab_cli.py"; Name = "holycrab_cli.py"; Sha256 = "d849a1360864960d14f04a5848f7e7a754d15589349019353f3fb44edf22c28f" },
    @{ Relative = "holycrab/references/capabilities.json"; Name = "capabilities.json"; Sha256 = "75b18984adacec0444252a8e8a841520fe0f2ceddf05b3d0f9aeba0bb59c4308" },
    @{ Relative = "holycrab/SKILL.md"; Name = "SKILL.md"; Sha256 = "9e90c6ca370e552569f0f6e4389263a845bb79319b54c47c43927dea9b93fae3" },
    @{ Relative = "holycrab/agents/openai.yaml"; Name = "openai.yaml"; Sha256 = "64bd549cd32e989324d5a17c2550cd54dfecccf70b4637b05b062a2fb709c1a7" },
    @{ Relative = "holycrab/scripts/vendor/segno-1.6.6-py3-none-any.whl"; Name = "segno.whl"; Sha256 = "28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7" },
    @{ Relative = "holycrab/scripts/vendor/LICENSE.segno"; Name = "LICENSE.segno"; Sha256 = "de6c85fccf5d52902aa13dfe2dc6d2a2a106fc3419ed438f3460f0d4b76a6935" }
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
    if (",$InstallAgents," -like "*,codex,*" -and (Test-Path -LiteralPath (Join-Path $HOME ".agents\skills\holycrab"))) {
        Copy-Item -LiteralPath (Join-Path $HOME ".agents\skills\holycrab") -Destination (Join-Path $BackupDir "codex-skill") -Recurse
    }
    if (",$InstallAgents," -like "*,claude,*" -and (Test-Path -LiteralPath (Join-Path $HOME ".claude\skills\holycrab"))) {
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

    $PythonInvocation = '"' + $Python.Executable + '"'
    if ($Python.Arguments.Count -gt 0) { $PythonInvocation += " " + ($Python.Arguments -join " ") }
    $Launcher = Join-Path $BinDir "holycrab.cmd"
    $LauncherContent = "@echo off`r`nchcp 65001 >nul`r`nset `"PYTHONUTF8=1`"`r`nset `"PYTHONIOENCODING=utf-8`"`r`n$PythonInvocation `"%~dp0..\lib\holycrab\holycrab_cli.py`" %*`r`nset `"_HOLYCRAB_EXIT_CODE=%ERRORLEVEL%`"`r`nif exist `"%~dp0..\lib\holycrab\holycrab_cli.py`" exit /b %_HOLYCRAB_EXIT_CODE%`r`n(goto) 2>nul & del /f /q `"%~f0`" >nul 2>&1`r`n"
    [IO.File]::WriteAllText($Launcher, $LauncherContent, [Text.UTF8Encoding]::new($false))

    $PathResult = Add-HolyCrabPath $BinDir $PreviousPathManaged
    $PathRegistration = $PathResult.Registration
    $PathAddedThisRun = $PathResult.AddedThisRun

    $Manifest = @{
        schemaVersion = 2
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

    Write-HolyCrabProgress "[5/5] Verifying the installed CLI version and local health..."
    $PreviousNoUpdate = $env:HOLYCRAB_NO_UPDATE_CHECK
    $env:HOLYCRAB_NO_UPDATE_CHECK = "1"
    try {
        $InstalledVersion = & $Launcher --version
        if ($LASTEXITCODE -ne 0 -or $InstalledVersion -ne ("holycrab " + $Version.TrimStart("v"))) {
            throw "Installed CLI version does not match $Version; installation stopped."
        }
        $Doctor = & $Launcher doctor --json | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or -not $Doctor.ok) {
            Write-Warning (Format-HolyCrabResult $Python $CliPath $Doctor "doctor")
            throw "holycrab doctor failed after installation"
        }
    } finally {
        $env:HOLYCRAB_NO_UPDATE_CHECK = $PreviousNoUpdate
    }
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
}
