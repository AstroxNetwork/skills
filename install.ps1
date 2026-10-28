$ErrorActionPreference = "Stop"
# IEX can inherit a caller's automatic variables. A literal scriptblock keeps
# its own parsed source file; streamed text has no file even inside another .ps1.
$HolyCrabOwnScriptScope = (-not [string]::IsNullOrEmpty(({}).File)) -and ($MyInvocation.MyCommand -is [Management.Automation.ExternalScriptInfo]) -and ($MyInvocation.InvocationName -ne '.')

# PS7 -> Python/cmd -> Windows PowerShell can inherit incompatible PS7 module
# paths. Resolve these built-in modules from this interpreter, retaining the
# real Get-FileHash verification rather than weakening the integrity check.
foreach ($BuiltinModule in @("Microsoft.PowerShell.Management", "Microsoft.PowerShell.Utility")) {
    $BuiltinManifest = [IO.Path]::Combine($PSHOME, "Modules", $BuiltinModule, ($BuiltinModule + ".psd1"))
    Import-Module -Name $BuiltinManifest -Scope Local -ErrorAction Stop
}

$Repository = "AstroxNetwork/skills"
$Version = "v0.7.0"
$SourceRef = if ($env:HOLYCRAB_INSTALL_REF) { $env:HOLYCRAB_INSTALL_REF } else { $Version }
if ($SourceRef -cnotmatch '^(?:[0-9a-f]{40}|v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$') {
    throw "HOLYCRAB_INSTALL_REF must be a full commit hash or stable version tag; installation stopped."
}
$SourceDir = $env:HOLYCRAB_INSTALL_SOURCE_DIR
$InstallMcp = [string]$env:HOLYCRAB_INSTALL_MCP
$InstallAgents = [string]$env:HOLYCRAB_INSTALL_AGENTS
$Prefix = if ($env:HOLYCRAB_INSTALL_PREFIX) { $env:HOLYCRAB_INSTALL_PREFIX } else { Join-Path $HOME ".local" }
# Fail before any writes for paths PowerShell 5.1 cannot safely install into.
$RawPrefix = $Prefix
$Prefix = [IO.Path]::GetFullPath($Prefix)
if ($Prefix.StartsWith('\\')) {
    throw "Unsupported installation path: use a local directory; UNC network installations are not supported."
}
if ($Prefix.IndexOfAny([char[]]'%!') -ge 0) {
    throw "Unsupported installation directory: the CMD launcher cannot reliably start from a path containing % or !. Choose another HOLYCRAB_INSTALL_PREFIX without these characters. This restriction applies only to the installation directory, not request files."
}
$PrefixParts = @($RawPrefix -split '[\\/]' | Where-Object { $_ -and $_ -notin @('.', '..') -and $_ -notmatch '^[A-Za-z]:$' })
foreach ($Part in $PrefixParts) {
    if ($Part.EndsWith('.') -or $Part.EndsWith(' ') -or $Part.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
        $Part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
        throw "Unsupported installation path component; reserved names and trailing dots/spaces cannot be used."
    }
}
$Ancestor = $Prefix
while ($Ancestor) {
    if (Test-Path -LiteralPath $Ancestor) {
        $Entry = Get-Item -LiteralPath $Ancestor -Force
        if (($Entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Unsupported installation path: junctions and symbolic links cannot be used as an installation directory or ancestor."
        }
        if (-not $Entry.PSIsContainer) { throw "Installation path must be a directory." }
    }
    $Ancestor = [IO.Path]::GetDirectoryName($Ancestor)
}
$BinDir = Join-Path $Prefix "bin"
$LibDir = Join-Path $Prefix "lib\holycrab"
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("holycrab-install." + [Guid]::NewGuid().ToString("N"))
$BackupDir = Join-Path $TempDir "backup"
$InstallLockHeld = $false
$InstallLockDir = Join-Path $Prefix ".holycrab-install.lock"
$InstallStarted = $false
$InstallComplete = $false
$HolyCrabInstallFailure = $null
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
    @{ Relative = "holycrab/references/capabilities.json"; Name = "capabilities.json"; Sha256 = "9a3b05927bfbc75a13f44bd7ee5483d5f4dc3447b65b6d3b8a5ce477a61bcdee" },
    @{ Relative = "holycrab/SKILL.md"; Name = "SKILL.md"; Sha256 = "3ae4b9c16a3605521a5da092f6c47a1e2e7eb71eb456c610b6603535082d4421" },
    @{ Relative = "holycrab/agents/openai.yaml"; Name = "openai.yaml"; Sha256 = "dbd2fc02cf5625a67f6af88837ed208d8de63e85359dfc923d1c9d465f7557ad" },
    @{ Relative = "holycrab/scripts/vendor/segno-1.6.6-py3-none-any.whl"; Name = "segno.whl"; Sha256 = "28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7" },
    @{ Relative = "holycrab/scripts/vendor/LICENSE.segno"; Name = "LICENSE.segno"; Sha256 = "de6c85fccf5d52902aa13dfe2dc6d2a2a106fc3419ed438f3460f0d4b76a6935" },
# BEGIN GENERATED RUNTIME DOWNLOADS
    @{ Relative = "runtime-files.json"; Name = "runtime-files.json"; Sha256 = "da3ff815b735749b84779625d7debd6a1e5fe5e79a5f14cfbdd166202efd4ce8" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/__init__.py"; Name = "holycrab_runtime/__init__.py"; Sha256 = "a957b310045f02b36e31a8fd60f5a3808b0838b59fd353c2a5318d0cceeb2ffe" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/capabilities.py"; Name = "holycrab_runtime/capabilities.py"; Sha256 = "43585789f09a5d1e5f3d215c75f1fc888e6aa9fead3b09d4244f667301fd0f64" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/cli.py"; Name = "holycrab_runtime/cli.py"; Sha256 = "5313558d35b510053f5bf67828e0414fe89577f25879c7737aa0c6418fb3b520" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/exports.py"; Name = "holycrab_runtime/exports.py"; Sha256 = "d0dcf10cb10a719b2d8a5d32510c3a20608d5dd88fb14a4225d2d51d23c4a7c6" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/generation.py"; Name = "holycrab_runtime/generation.py"; Sha256 = "2c77b1a8d617f35e337cb775289f534ca1686b38b5d3a451b817883be3914485" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/lifecycle.py"; Name = "holycrab_runtime/lifecycle.py"; Sha256 = "6e193f20bdd1a6d2782640e513b1d870bf1a2e421a4bb90105e71ef04d7e6e19" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/mcp.py"; Name = "holycrab_runtime/mcp.py"; Sha256 = "cc68e2d8c6c8b80000f7d16656869cd60dec89a702fadfb99e12d24a254a9edf" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/presentation.py"; Name = "holycrab_runtime/presentation.py"; Sha256 = "483c3e830104c2a773341ea395e61f7d005b28c55282a33fdb852118399f671c" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/real_human.py"; Name = "holycrab_runtime/real_human.py"; Sha256 = "36ff3c4c5603647ccbc2e3bc12ccd69f0b83bdedf9a385c168e3b9c74af29e74" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/results.py"; Name = "holycrab_runtime/results.py"; Sha256 = "8e985392bef51aa6231389b9efc60bcd50f83b803ec5b41002f662b53f2f05a2" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/settings.py"; Name = "holycrab_runtime/settings.py"; Sha256 = "a561ae9baaa4a6e792152725f29ec0ee6bbffabbe1698d83dae9fab2c5bcfdf9" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/state.py"; Name = "holycrab_runtime/state.py"; Sha256 = "c87236d05ecdcefa64249aa3cf3fa4003918a887751f5469386531fefa330946" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/tasks.py"; Name = "holycrab_runtime/tasks.py"; Sha256 = "2936d1574e9cb6caa5d7e6a21af5a2a55042972620ccf271264ae35d2fa7bc27" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/transport.py"; Name = "holycrab_runtime/transport.py"; Sha256 = "0020469744a96b96959e1df42e7234b964909a90388ba165234cd380e81ca432" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/uploads.py"; Name = "holycrab_runtime/uploads.py"; Sha256 = "4a1c21aa5df008841d1e0425991800ff36ed219ca82284f05deaadf8b0b79cc5" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/downloads.py"; Name = "holycrab_runtime/downloads.py"; Sha256 = "4d9fd603d09a4df7cf1a0da7e90ebe43d7d64f9855d07f5d560a82fdb7abc14b" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/media.py"; Name = "holycrab_runtime/media.py"; Sha256 = "eb15a22314ba1be354b47eddfcc66884e8deb9379cfd6feedd7259740acdf6bd" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/skills.py"; Name = "holycrab_runtime/skills.py"; Sha256 = "c704273a473ca17a599d63e452cf56ffb11ca2cf92fc0b54caae93db2278900e" },
    @{ Relative = "holycrab/scripts/holycrab_runtime/workflows.py"; Name = "holycrab_runtime/workflows.py"; Sha256 = "081d378610bc31097b54e145647ea212f880a01e1f7cc4df451697f25783b00b" },
    @{ Relative = "holycrab/references/creative/SOURCES.md"; Name = "references/creative/SOURCES.md"; Sha256 = "c3410ef2b72003d9f6bfe0ca5d07d6afa91f0352aa21f27c6e9b672b5f33c814" },
    @{ Relative = "holycrab/references/creative/audio.md"; Name = "references/creative/audio.md"; Sha256 = "f6226800c0f6868188538d7db08e8cabbfbba938d5633e5e4f923fcc123c850a" },
    @{ Relative = "holycrab/references/creative/catalog.json"; Name = "references/creative/catalog.json"; Sha256 = "b56eb79eb7a08843187344a79b8c637825d1fa3680b9fde72ebb8d4b30af4980" },
    @{ Relative = "holycrab/references/creative/composition.md"; Name = "references/creative/composition.md"; Sha256 = "6abb8c32e9c4cae7365774b397860374c98117c1b2746abaeedb1230acf706df" },
    @{ Relative = "holycrab/references/creative/execution.md"; Name = "references/creative/execution.md"; Sha256 = "57f30155557e1d847954d33f847c3e641881743113bb76f9e0483649d82664bf" },
    @{ Relative = "holycrab/references/creative/h3.md"; Name = "references/creative/h3.md"; Sha256 = "44a7afc46320511c43013369d802e054628c853e6a60b6f6303a9f1298e1d8e6" },
    @{ Relative = "holycrab/references/creative/h3/base-en.txt"; Name = "references/creative/h3/base-en.txt"; Sha256 = "2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc" },
    @{ Relative = "holycrab/references/creative/h3/ref-en.txt"; Name = "references/creative/h3/ref-en.txt"; Sha256 = "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7" },
    @{ Relative = "holycrab/references/creative/image.md"; Name = "references/creative/image.md"; Sha256 = "25ab6ce720058b687272420be0124ac20be8c19b7f6cc68a886a6d9e431e9699" },
    @{ Relative = "holycrab/references/creative/seedance.md"; Name = "references/creative/seedance.md"; Sha256 = "c611181f833a4bff646195abed155728a46977e9a7a444231b616b6d2b3688fe" },
    @{ Relative = "holycrab/references/creative/seedance/advanced-techniques.md"; Name = "references/creative/seedance/advanced-techniques.md"; Sha256 = "ba96d48c29eb211328894b885e091137a874ea0b71bff6dd49c822340ed0d54a" },
    @{ Relative = "holycrab/references/creative/seedance/checklist-and-limits.md"; Name = "references/creative/seedance/checklist-and-limits.md"; Sha256 = "182be5d7a2047caa5dd25651dc4c01eda4cafd9ec916ee13838d4e6b1a599f72" },
    @{ Relative = "holycrab/references/creative/seedance/editing-and-extension.md"; Name = "references/creative/seedance/editing-and-extension.md"; Sha256 = "7aa000ebc123da8696e9f843725d4f38fa218221783702b1a1996a64eb87e8b4" },
    @{ Relative = "holycrab/references/creative/seedance/long-video-and-timing.md"; Name = "references/creative/seedance/long-video-and-timing.md"; Sha256 = "3234541b0927f62373692c93bc30e4c53d1abec2b16cf6a1d4d8efc7b780bbfd" },
    @{ Relative = "holycrab/references/creative/seedance/multi-reference.md"; Name = "references/creative/seedance/multi-reference.md"; Sha256 = "f0acf5b0d651b5bab2d4104f4f198df71fbc90ef349742f28cc1a681b2e4f070" },
    @{ Relative = "holycrab/references/creative/video.md"; Name = "references/creative/video.md"; Sha256 = "0ef629ff1447b8b358e8b118885677c788ec39a1ec3cfda5019cacb60bf43599" },
    @{ Relative = "holycrab/references/creative/workflows.md"; Name = "references/creative/workflows.md"; Sha256 = "2b613be0dfd7bde015a7c29d541a3db9ccbee26eb175813aea818e9d0916d9d0" }
# END GENERATED RUNTIME DOWNLOADS
)

function Find-HolyCrabPython {
    $Candidates = @(
        # Respect the Python environment the user selected in this terminal.
        @{ Command = "python"; Arguments = @() },
        @{ Command = "python3"; Arguments = @() },
        @{ Command = "py"; Arguments = @("-3") }
    )
    foreach ($Candidate in $Candidates) {
        $Resolved = Get-Command $Candidate.Command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $Resolved) { continue }
        $VersionArguments = @($Candidate.Arguments) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        try { & $Resolved.Source @VersionArguments } catch { continue }
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
    Copy-Item -LiteralPath (Join-Path $LibDir "references/creative") -Destination (Join-Path $Destination "references") -Recurse -Force
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

try {
    Write-HolyCrabProgress "[1/5] Checking Python and installation settings..."
    $Python = Find-HolyCrabPython
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
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
resolved = cli.installer_settings(previous, **settings)
from holycrab_runtime.lifecycle import check_skill_conflicts
check_skill_conflicts(previous, resolved["agents"], pathlib.Path(sys.argv[1]).parent / "runtime-files.json")
print(json.dumps(resolved))
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
    New-Item -ItemType Directory -Force -Path (Join-Path $LibDir "references/creative") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $LibDir "references/creative/h3") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $LibDir "references/creative/seedance") | Out-Null
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
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/downloads.py"] -Destination (Join-Path $LibDir "holycrab_runtime/downloads.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/media.py"] -Destination (Join-Path $LibDir "holycrab_runtime/media.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/skills.py"] -Destination (Join-Path $LibDir "holycrab_runtime/skills.py") -Force
    Copy-Item -LiteralPath $Downloaded["holycrab_runtime/workflows.py"] -Destination (Join-Path $LibDir "holycrab_runtime/workflows.py") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/SOURCES.md"] -Destination (Join-Path $LibDir "references/creative/SOURCES.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/audio.md"] -Destination (Join-Path $LibDir "references/creative/audio.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/catalog.json"] -Destination (Join-Path $LibDir "references/creative/catalog.json") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/composition.md"] -Destination (Join-Path $LibDir "references/creative/composition.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/execution.md"] -Destination (Join-Path $LibDir "references/creative/execution.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/h3.md"] -Destination (Join-Path $LibDir "references/creative/h3.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/h3/base-en.txt"] -Destination (Join-Path $LibDir "references/creative/h3/base-en.txt") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/h3/ref-en.txt"] -Destination (Join-Path $LibDir "references/creative/h3/ref-en.txt") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/image.md"] -Destination (Join-Path $LibDir "references/creative/image.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/seedance.md"] -Destination (Join-Path $LibDir "references/creative/seedance.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/seedance/advanced-techniques.md"] -Destination (Join-Path $LibDir "references/creative/seedance/advanced-techniques.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/seedance/checklist-and-limits.md"] -Destination (Join-Path $LibDir "references/creative/seedance/checklist-and-limits.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/seedance/editing-and-extension.md"] -Destination (Join-Path $LibDir "references/creative/seedance/editing-and-extension.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/seedance/long-video-and-timing.md"] -Destination (Join-Path $LibDir "references/creative/seedance/long-video-and-timing.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/seedance/multi-reference.md"] -Destination (Join-Path $LibDir "references/creative/seedance/multi-reference.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/video.md"] -Destination (Join-Path $LibDir "references/creative/video.md") -Force
    Copy-Item -LiteralPath $Downloaded["references/creative/workflows.md"] -Destination (Join-Path $LibDir "references/creative/workflows.md") -Force
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

    $PythonInvocation = '"' + $Python.Executable.Replace('%', '%%') + '"'
    if ($Python.Arguments.Count -gt 0) { $PythonInvocation += " " + ($Python.Arguments -join " ") }
    $Launcher = Join-Path $BinDir "holycrab.cmd"
    $LauncherContent = "@echo off`r`nsetlocal DisableDelayedExpansion`r`nchcp 65001 >nul`r`nset `"PYTHONUTF8=1`"`r`nset `"PYTHONIOENCODING=utf-8`"`r`n$PythonInvocation `"%~dp0..\lib\holycrab\holycrab_cli.py`" %*`r`nset `"_HOLYCRAB_EXIT_CODE=%ERRORLEVEL%`"`r`nexit /b %_HOLYCRAB_EXIT_CODE%`r`n"
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
            foreach ($Entry in $RuntimeManifest.files) {
                if ($Entry.target.StartsWith("references/creative/")) {
                    $Manifest.coreFiles += @{ path = (Join-Path $Root $Entry.target); sha256 = $Entry.sha256 }
                }
            }
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
} catch {
    $HolyCrabInstallFailure = $_
    throw
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
    if (-not $InstallComplete) {
        $global:LASTEXITCODE = 1
        if ($null -eq $HolyCrabInstallFailure -or $HolyCrabInstallFailure.Exception -is [Management.Automation.PipelineStoppedException]) {
            # Windows PowerShell -File reports Ctrl+C as success unless the
            # script explicitly exits. Wait until recovery and lock cleanup
            # finish; streamed irm|iex/dot-sourced installs must keep the host.
            $global:LASTEXITCODE = 130
            if ($HolyCrabOwnScriptScope) { exit 130 }
        }
    }
}
