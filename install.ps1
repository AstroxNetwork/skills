$ErrorActionPreference = "Stop"

$Repository = "AstroxNetwork/skills"
$Version = "v0.4.1"
$SourceDir = $env:HOLYCRAB_INSTALL_SOURCE_DIR
$InstallMcp = if ($env:HOLYCRAB_INSTALL_MCP) { $env:HOLYCRAB_INSTALL_MCP } else { "1" }
$InstallAgents = if ($env:HOLYCRAB_INSTALL_AGENTS) { $env:HOLYCRAB_INSTALL_AGENTS } else { "codex,claude" }
$Prefix = if ($env:HOLYCRAB_INSTALL_PREFIX) { $env:HOLYCRAB_INSTALL_PREFIX } else { Join-Path $HOME ".local" }
$BinDir = Join-Path $Prefix "bin"
$LibDir = Join-Path $Prefix "lib\holycrab"
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("holycrab-install." + [Guid]::NewGuid().ToString("N"))
$BackupDir = Join-Path $TempDir "backup"
$InstallStarted = $false
$InstallComplete = $false

$ReleaseFiles = @(
    @{ Relative = "holycrab/scripts/holycrab_cli.py"; Name = "holycrab_cli.py"; Sha256 = "92d398e99d3c3d090011120e5600559aa5f948dc1af53eedbc4e91aa6bed7a24" },
    @{ Relative = "holycrab/references/capabilities.json"; Name = "capabilities.json"; Sha256 = "75b18984adacec0444252a8e8a841520fe0f2ceddf05b3d0f9aeba0bb59c4308" },
    @{ Relative = "holycrab/SKILL.md"; Name = "SKILL.md"; Sha256 = "508a610154f6937010a2c1d650106ba5305592ca72196e3fdcc50d1c2d04aaba" },
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
    $Destination = Join-Path $TempDir $File.Name
    if ($SourceDir) {
        Copy-Item -LiteralPath (Join-Path $SourceDir $File.Relative) -Destination $Destination
    } else {
        $Url = "https://raw.githubusercontent.com/$Repository/$Version/$($File.Relative)"
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
        $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
        if ($Actual -ne $File.Sha256) {
            throw "SHA-256 verification failed for $($File.Relative); installation stopped."
        }
    }
    return $Destination
}

function Install-HolyCrabSkill([string]$Destination, [hashtable]$Downloaded) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Destination "references"), (Join-Path $Destination "agents") | Out-Null
    Copy-Item -LiteralPath $Downloaded.Skill -Destination (Join-Path $Destination "SKILL.md") -Force
    Copy-Item -LiteralPath $Downloaded.Capabilities -Destination (Join-Path $Destination "references\capabilities.json") -Force
    Copy-Item -LiteralPath $Downloaded.OpenAI -Destination (Join-Path $Destination "agents\openai.yaml") -Force
}

function Add-HolyCrabPath([string]$Directory) {
    $CurrentEntries = @($env:Path -split ";" | Where-Object { $_ })
    if (-not ($CurrentEntries | Where-Object {
        $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq $Directory.TrimEnd([IO.Path]::DirectorySeparatorChar)
    })) {
        $env:Path = "$Directory;$env:Path"
    }

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $UserEntries = @($UserPath -split ";" | Where-Object { $_ })
    if (-not ($UserEntries | Where-Object {
        $_.TrimEnd([IO.Path]::DirectorySeparatorChar) -ieq $Directory.TrimEnd([IO.Path]::DirectorySeparatorChar)
    })) {
        $Updated = if ($UserPath) { "$Directory;$UserPath" } else { $Directory }
        [Environment]::SetEnvironmentVariable("Path", $Updated, "User")
    }
}

function Register-HolyCrabMcp([string]$Agent, [hashtable]$Python, [string]$CliPath) {
    $Resolved = Get-Command $Agent -ErrorAction SilentlyContinue
    if (-not $Resolved) { return }
    $Existing = (& $Resolved.Source mcp get holycrab 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0) {
        if ($Existing.Contains($CliPath) -and $Existing -match "mcp" -and $Existing -match "serve") { return }
        if ($Existing -match "holycrab_cli\.py|[\\/]holycrab") {
            & $Resolved.Source mcp remove holycrab *> $null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Could not remove the stale HolyCrab MCP entry for $Agent."
                return
            }
        } else {
            Write-Warning "An unmanaged MCP entry named holycrab already exists for $Agent; it was not changed."
            return
        }
    }

    $ServerCommand = @($Python.Executable) + @($Python.Arguments) + @($CliPath, "mcp", "serve")
    if ($Agent -eq "codex") {
        $Arguments = @("mcp", "add", "holycrab", "--") + $ServerCommand
    } else {
        $Arguments = @("mcp", "add", "--scope", "user", "holycrab", "--") + $ServerCommand
    }
    & $Resolved.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "$Agent MCP registration failed. Run the installer again after checking $Agent."
    }
}

$Python = Find-HolyCrabPython
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
try {
    $Downloaded = @{}
    foreach ($File in $ReleaseFiles) {
        $Downloaded[$File.Name] = Copy-ReleaseFile $File
    }

    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    if (Test-Path -LiteralPath $LibDir) { Copy-Item -LiteralPath $LibDir -Destination (Join-Path $BackupDir "lib") -Recurse }
    $ExistingLauncher = Join-Path $BinDir "holycrab.cmd"
    if (Test-Path -LiteralPath $ExistingLauncher) { Copy-Item -LiteralPath $ExistingLauncher -Destination (Join-Path $BackupDir "holycrab.cmd") }
    if (Test-Path -LiteralPath (Join-Path $HOME ".agents\skills\holycrab")) {
        Copy-Item -LiteralPath (Join-Path $HOME ".agents\skills\holycrab") -Destination (Join-Path $BackupDir "codex-skill") -Recurse
    }
    if (Test-Path -LiteralPath (Join-Path $HOME ".claude\skills\holycrab")) {
        Copy-Item -LiteralPath (Join-Path $HOME ".claude\skills\holycrab") -Destination (Join-Path $BackupDir "claude-skill") -Recurse
    }
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
    $LauncherContent = "@echo off`r`n$PythonInvocation `"%~dp0..\lib\holycrab\holycrab_cli.py`" %*`r`n"
    [IO.File]::WriteAllText($Launcher, $LauncherContent, [Text.UTF8Encoding]::new($false))

    $Manifest = @{
        version = "0.4.1"
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

    Add-HolyCrabPath $BinDir
    if ($InstallMcp -eq "1") {
        if (",$InstallAgents," -like "*,codex,*") { Register-HolyCrabMcp "codex" $Python $CliPath }
        if (",$InstallAgents," -like "*,claude,*") { Register-HolyCrabMcp "claude" $Python $CliPath }
    }

    Write-Host "HolyCrab CLI, local MCP, and Skill are installed."
    Write-Host "Command: $Launcher"
    Write-Host "PATH is active in this PowerShell session and saved for future sessions."
    $PreviousNoUpdate = $env:HOLYCRAB_NO_UPDATE_CHECK
    $env:HOLYCRAB_NO_UPDATE_CHECK = "1"
    try {
        & $Python.Executable @($Python.Arguments) $CliPath --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "holycrab --version failed after installation" }
        $Doctor = & $Python.Executable @($Python.Arguments) $CliPath doctor --json | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or -not $Doctor.ok) { throw "holycrab doctor failed after installation" }
    } finally {
        $env:HOLYCRAB_NO_UPDATE_CHECK = $PreviousNoUpdate
    }
    $InstallComplete = $true
    Write-Host "Next: holycrab setup"
} finally {
    if ($InstallStarted -and -not $InstallComplete) {
        Remove-Item -LiteralPath $LibDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath (Join-Path $BackupDir "lib")) { Copy-Item -LiteralPath (Join-Path $BackupDir "lib") -Destination $LibDir -Recurse }
        Remove-Item -LiteralPath (Join-Path $BinDir "holycrab.cmd") -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath (Join-Path $BackupDir "holycrab.cmd")) { Copy-Item -LiteralPath (Join-Path $BackupDir "holycrab.cmd") -Destination (Join-Path $BinDir "holycrab.cmd") }
        foreach ($Skill in @(@{ Backup = "codex-skill"; Target = (Join-Path $HOME ".agents\skills\holycrab") },
                              @{ Backup = "claude-skill"; Target = (Join-Path $HOME ".claude\skills\holycrab") })) {
            Remove-Item -LiteralPath $Skill.Target -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath (Join-Path $BackupDir $Skill.Backup)) { Copy-Item -LiteralPath (Join-Path $BackupDir $Skill.Backup) -Destination $Skill.Target -Recurse }
        }
        Write-Warning "HolyCrab installation failed; previous managed files were restored."
    }
    Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue
}
