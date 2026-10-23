param(
    [Parameter(Mandatory=$true)][string]$Report,
    [string]$BaselineSource = "",
    [int]$TimeoutSeconds = 1200
)
$ErrorActionPreference = "Stop"
function Wait-HolyCrabNativeProcess {
    param([Diagnostics.Process]$Process, [int]$TimeoutMilliseconds, [string]$ReleaseFile = "")
    # Start-Process -RedirectStandard* in Windows PowerShell returns a Process
    # without an owned handle. Capture it while alive, before WaitForExit:
    # https://github.com/PowerShell/PowerShell/issues/5421
    $RetainedHandle = $Process.Handle
    if ($null -eq $RetainedHandle -or $RetainedHandle -eq [IntPtr]::Zero -or $RetainedHandle -eq [IntPtr](-1)) {
        throw "Could not retain the native process handle for PID $($Process.Id)."
    }
    if ($ReleaseFile) { [IO.File]::WriteAllText($ReleaseFile, "handle retained") }
    if (-not $Process.WaitForExit($TimeoutMilliseconds)) {
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
        if (-not $Process.WaitForExit(10000)) { throw "Native PID $($Process.Id) remained alive after timeout cleanup." }
        throw "Native PID $($Process.Id) timed out; process tree termination was requested and its root exit observed."
    }
    # Invoke the getter explicitly: PowerShell property access can suppress a
    # getter exception and yield null. Missing exit status must never become 0.
    $ExitCode = $Process.get_ExitCode()
    if ($null -eq $ExitCode -or $ExitCode -isnot [int]) {
        throw "Native PID $($Process.Id) exited without an integer exit status."
    }
    return @{ pid=$Process.Id; handleRetained=$true; hasExited=$Process.HasExited; exitCode=$ExitCode }
}
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$Report = [IO.Path]::GetFullPath($Report)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null
$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "The outer native harness needs an administrator only to provision its ordinary temporary account. Acceptance itself must run without administrator rights."
}
$UserName = "hcqa" + [Guid]::NewGuid().ToString("N").Substring(0,12)
$TestRoot = Join-Path ([Environment]::GetFolderPath("CommonDocuments")) $UserName
$Candidate = Join-Path $TestRoot "candidate"
$Baseline = Join-Path $TestRoot "baseline"
$WorkerReport = Join-Path $TestRoot "report.json"
$Created = $false
$Worker = $null
$WorkerExitEvidence = @{ probes=@(); worker=$null; pythonExitCode=$null }
$FirewallRule = "HolyCrab-QA-" + $UserName
$FirewallCreated = $false
New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null
try {
    $Preparation = @'
import hashlib, io, json, pathlib, shutil, subprocess, sys, tarfile
source, candidate, baseline, old = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(source / "tools"))
from run_checks import source_fingerprint, NETWORK_GUARD
stamp = {"sourceFingerprint": source_fingerprint(source),
         "commit": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()}
(candidate.parent / "source-stamp.json").write_text(json.dumps(stamp, sort_keys=True), encoding="utf-8")
guard = candidate.parent / "offline-guard"
guard.mkdir()
(guard / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
tag_object = "52f697e35ad2689791423cb10eae5c8259e4c943"
commit = "48126baa08521961de47d48bd2134c44a7e8cba2"
shutil.copytree(source, candidate, ignore=shutil.ignore_patterns(".git", "build", "__pycache__", ".venv"))
repo = old if str(old) != "." else source
tag_type = subprocess.check_output(["git", "-C", str(repo), "cat-file", "-t", tag_object], text=True).strip()
if tag_type != "tag":
    raise ValueError("Pinned v0.5.0 tag object is not an annotated tag")
resolved = subprocess.check_output(["git", "-C", str(repo), "rev-parse", tag_object + "^{commit}"], text=True).strip()
if resolved != commit:
    raise ValueError("Pinned v0.5.0 annotated tag does not resolve to the audited baseline commit")
if subprocess.check_output(["git", "-C", str(repo), "cat-file", "-t", commit], text=True).strip() != "commit":
    raise ValueError("Pinned v0.5.0 baseline is not a commit object")
archive = subprocess.check_output(["git", "-C", str(repo), "archive", "--format=tar", commit])
baseline.mkdir()
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
    for member in bundle.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise ValueError("Unsafe path in pinned baseline archive")
    bundle.extractall(baseline)
(baseline / ".holycrab-baseline.json").write_text(json.dumps({"tag": "v0.5.0", "tagObject": tag_object, "commit": commit}), encoding="ascii")
'@
    $PrepareFile = Join-Path $TestRoot "prepare.py"
    [IO.File]::WriteAllText($PrepareFile, $Preparation, [Text.UTF8Encoding]::new($false))
    $BaselineRepository = if ($BaselineSource) { $BaselineSource } else { "." }
    & $Python $PrepareFile $RepoRoot $Candidate $Baseline $BaselineRepository
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare the immutable v0.5.0 baseline" }
    Add-Type -AssemblyName System.Security
    $ForeignCipher = [Security.Cryptography.ProtectedData]::Protect(
        [Text.Encoding]::UTF8.GetBytes("hc_test_foreign_user_never_echo_123456789"), $null,
        [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $ForeignConfig = Join-Path $TestRoot "foreign-config.json"
    [IO.File]::WriteAllText($ForeignConfig, ('{"apiKeyDpapi":"' + [Convert]::ToBase64String($ForeignCipher) + '"}'), [Text.UTF8Encoding]::new($false))
    $Password = ConvertTo-SecureString ("Hc9!" + [Guid]::NewGuid().ToString("N")) -AsPlainText -Force
    New-LocalUser -Name $UserName -Password $Password -AccountNeverExpires -PasswordNeverExpires -Description "Ephemeral HolyCrab offline validation" | Out-Null
    $Created = $true
    $UsersGroup = (Get-LocalGroup -SID 'S-1-5-32-545').Name
    $Sid = (Get-LocalUser -Name $UserName).SID.Value
    if (-not (Get-LocalGroupMember -Group $UsersGroup | Where-Object { $_.SID.Value -eq $Sid })) {
        Add-LocalGroupMember -Group $UsersGroup -Member $UserName
    }
    & icacls.exe $TestRoot /grant ("*" + $Sid + ":(OI)(CI)M") /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not grant the temporary user access to isolated test files" }
    # Block non-loopback traffic for this temporary user, including PowerShell
    # and any native descendants; Python's audit hook independently enforces it.
    $UserFilter = "D:(A;;CC;;;" + $Sid + ")"
    New-NetFirewallRule -Name $FirewallRule -DisplayName $FirewallRule -Direction Outbound -Action Block -Authentication NotRequired -Profile Any -LocalUser $UserFilter -RemoteAddress @("0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255", "::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff") | Out-Null
    $FirewallCreated = $true
    $Rule = Get-NetFirewallRule -Name $FirewallRule
    if ($Rule.Enabled -ne "True" -or $Rule.Action -ne "Block") { throw "Offline account firewall was not enabled" }
    $Security = $Rule | Get-NetFirewallSecurityFilter
    if (-not ([string]$Security.LocalUser).Contains($Sid)) { throw "Offline firewall did not bind to the temporary user" }
    $StampPath = Join-Path $TestRoot "source-stamp.json"
    $Stamp = Get-Content -LiteralPath $StampPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $Stamp | Add-Member -NotePropertyName networkIsolation -NotePropertyValue @{firewallEnabled=$true; userSid=$Sid; rule=$FirewallRule; pythonAuditHook=$true}
    [IO.File]::WriteAllText($StampPath, ($Stamp | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    # Materialize arguments as JSON, avoiding nested command-line quoting in PS 5.1.
    $Payload = @{
        python=$Python; source=$Candidate; baseline=$Baseline; root=(Join-Path $TestRoot "work");
        report=$WorkerReport; username=$UserName; foreignConfig=$ForeignConfig;
        path=$env:PATH; guard=(Join-Path $TestRoot "offline-guard"); stamp=(Join-Path $TestRoot "source-stamp.json");
        exitReport=(Join-Path $TestRoot "worker-python-exit.json")
    }
    New-Item -ItemType Directory -Force -Path $Payload.root | Out-Null
    $PayloadFile = Join-Path $TestRoot "arguments.json"
    [IO.File]::WriteAllText($PayloadFile, ($Payload | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    $WorkerCode = @'
param([string]$PayloadFile)
$ErrorActionPreference = "Stop"
foreach ($Module in @("Microsoft.PowerShell.Management", "Microsoft.PowerShell.Utility")) {
    Import-Module -Name ([IO.Path]::Combine($PSHOME, "Modules", $Module, ($Module + ".psd1"))) -ErrorAction Stop
}
$p = Get-Content -LiteralPath $PayloadFile -Raw -Encoding UTF8 | ConvertFrom-Json
# Resolve profile from the logged-on token, not the administrator's inherited environment.
$env:USERPROFILE = [Environment]::GetFolderPath("UserProfile")
$env:APPDATA = [Environment]::GetFolderPath("ApplicationData")
$env:LOCALAPPDATA = [Environment]::GetFolderPath("LocalApplicationData")
$env:TEMP = Join-Path $env:LOCALAPPDATA "Temp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$env:PATH = (Split-Path -Parent $p.python) + ";" + $p.path
$env:PYTHONUTF8 = "1"
$env:PYTHONOPTIMIZE = $null
$env:PSModulePath = $null
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $p.guard
$env:HOLYCRAB_TEST_LOOPBACK = "1"
& $p.python (Join-Path $p.source "tests/windows_adversarial.py") --source $p.source --baseline $p.baseline --root $p.root --report $p.report --username $p.username --foreign-config $p.foreignConfig --source-stamp $p.stamp
$PythonExitCode = $LASTEXITCODE
if ($null -eq $PythonExitCode -or $PythonExitCode -isnot [int]) { throw "Native Python worker did not return an integer exit status." }
[IO.File]::WriteAllText($p.exitReport, (@{pythonExitCode=$PythonExitCode; workerPid=$PID} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
[Console]::WriteLine("HOLYCRAB_WORKER_PYTHON_EXIT=" + $PythonExitCode)
exit $PythonExitCode
'@
    $WorkerFile = Join-Path $TestRoot "worker.ps1"
    [IO.File]::WriteAllText($WorkerFile, $WorkerCode, [Text.UTF8Encoding]::new($false))
    $Credential = New-Object Management.Automation.PSCredential("$env:COMPUTERNAME\$UserName", $Password)
    $PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    # The supervisor itself must preserve success, failure, and cancellation
    # through the same account, redirection, and wait path as the real worker.
    $ProbeFile = Join-Path $TestRoot "exit-probe.ps1"
    $ProbeCode = @'
param([int]$Code, [string]$ReleaseFile)
$Deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not [IO.File]::Exists($ReleaseFile)) {
    if ([DateTime]::UtcNow -ge $Deadline) { throw "The supervisor did not release the exit-code probe." }
    [Threading.Thread]::Sleep(10)
}
exit $Code
'@
    [IO.File]::WriteAllText($ProbeFile, $ProbeCode, [Text.UTF8Encoding]::new($false))
    foreach ($ExpectedExit in @(0, 37, 130)) {
        $ReleaseFile = Join-Path $TestRoot ("probe-" + $ExpectedExit + ".release")
        $ProbeArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $ProbeFile + '" -Code ' + $ExpectedExit + ' -ReleaseFile "' + $ReleaseFile + '"'
        $Probe = Start-Process -FilePath $PowerShell -ArgumentList $ProbeArguments -Credential $Credential -LoadUserProfile -PassThru -WorkingDirectory $TestRoot -RedirectStandardOutput (Join-Path $TestRoot "probe.stdout.log") -RedirectStandardError (Join-Path $TestRoot "probe.stderr.log")
        try {
            $Observed = Wait-HolyCrabNativeProcess -Process $Probe -TimeoutMilliseconds 15000 -ReleaseFile $ReleaseFile
            $Observed.expectedExitCode = $ExpectedExit
            $WorkerExitEvidence.probes += $Observed
            if ($Observed.exitCode -ne $ExpectedExit) { throw "Native exit-code probe expected $ExpectedExit but observed $($Observed.exitCode)." }
        } finally { $Probe.Dispose() }
    }
    $Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $WorkerFile + '" -PayloadFile "' + $PayloadFile + '"'
    $Worker = Start-Process -FilePath $PowerShell -ArgumentList $Arguments -Credential $Credential -LoadUserProfile -PassThru -WorkingDirectory $TestRoot -RedirectStandardOutput (Join-Path $TestRoot "worker.stdout.log") -RedirectStandardError (Join-Path $TestRoot "worker.stderr.log")
    $WorkerExitEvidence.worker = Wait-HolyCrabNativeProcess -Process $Worker -TimeoutMilliseconds ($TimeoutSeconds * 1000)
    if (-not (Test-Path -LiteralPath $Payload.exitReport)) { throw "Native worker did not record its Python exit status." }
    $PythonExitEvidence = Get-Content -LiteralPath $Payload.exitReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $WorkerExitEvidence.pythonExitCode = $PythonExitEvidence.pythonExitCode
    if ($PythonExitEvidence.workerPid -ne $Worker.Id -or $null -eq $PythonExitEvidence.pythonExitCode -or $PythonExitEvidence.pythonExitCode -ne $WorkerExitEvidence.worker.exitCode) {
        throw "Native worker/Python exit status mismatch: $($WorkerExitEvidence | ConvertTo-Json -Depth 5 -Compress)"
    }
    if (-not (Test-Path -LiteralPath $WorkerReport)) { throw "Native worker did not produce its required case report" }
    Copy-Item -LiteralPath $WorkerReport -Destination $Report -Force
    $Evidence = Get-Content -LiteralPath $WorkerReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $WorkerExitEvidence.reportOk = $Evidence.ok
    $WorkerExitEvidence.caseCount = @($Evidence.cases).Count
    $WorkerExitEvidence.expectedCaseCount = @($Evidence.expectedCaseIds).Count
    Write-Output ("Native worker exit evidence: " + ($WorkerExitEvidence | ConvertTo-Json -Depth 5 -Compress))
    if ($WorkerExitEvidence.worker.exitCode -ne 0 -or $Evidence.ok -ne $true -or @($Evidence.cases).Count -ne @($Evidence.expectedCaseIds).Count) {
        throw "Native Windows adversarial validation failed: $($WorkerExitEvidence | ConvertTo-Json -Depth 5 -Compress)"
    }
} catch {
    $WorkerExitEvidence.supervisorError = $_.Exception.Message
    throw
} finally {
    $CleanupErrors = @()
    try {
        [IO.File]::WriteAllText(($Report + ".worker-exit.json"), ($WorkerExitEvidence | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    } catch { $CleanupErrors += "Could not write worker exit evidence: $($_.Exception.Message)" }
    try {
        if ($Worker -and -not $Worker.HasExited) { & taskkill.exe /PID $Worker.Id /T /F | Out-Null }
    } catch { $CleanupErrors += "Could not terminate the worker directly: $($_.Exception.Message)" }
    foreach ($Name in @("worker.stdout.log", "worker.stderr.log", "probe.stdout.log", "probe.stderr.log")) {
        $Log = Join-Path $TestRoot $Name
        try {
            if (Test-Path -LiteralPath $Log) { Copy-Item -LiteralPath $Log -Destination ($Report + "." + $Name) -Force }
        } catch { $CleanupErrors += "Could not preserve ${Name}: $($_.Exception.Message)" }
        try {
            if (Test-Path -LiteralPath $Log) { Get-Content -LiteralPath $Log -Encoding UTF8 | Write-Output }
        } catch { $CleanupErrors += "Could not display ${Name}: $($_.Exception.Message)" }
    }
    try {
        if ((Test-Path -LiteralPath $WorkerReport) -and -not (Test-Path -LiteralPath $Report)) {
            Copy-Item -LiteralPath $WorkerReport -Destination $Report -Force
        }
    } catch { $CleanupErrors += "Could not preserve the native case report: $($_.Exception.Message)" }
    $Cleanup = @{ user=$UserName; killedProcessIds=@(); remainingProcessIds=@(); verified=$false }
    try {
        $OwnedProcesses = @(Get-Process -IncludeUserName -ErrorAction Stop | Where-Object { $_.UserName -ieq "$env:COMPUTERNAME\$UserName" })
        if ($OwnedProcesses.Count -gt 0) {
            $Cleanup.killedProcessIds = @($OwnedProcesses | ForEach-Object { $_.Id })
            foreach ($Owned in $OwnedProcesses) { Stop-Process -Id $Owned.Id -Force -ErrorAction Stop }
            $Deadline = [DateTime]::UtcNow.AddSeconds(10)
            do {
                Start-Sleep -Milliseconds 100
                $OwnedProcesses = @(Get-Process -IncludeUserName -ErrorAction Stop | Where-Object { $_.UserName -ieq "$env:COMPUTERNAME\$UserName" })
            } while ($OwnedProcesses.Count -gt 0 -and [DateTime]::UtcNow -lt $Deadline)
            # Cleanup is mandatory, but cleaning a leaked tree must not turn the
            # original test into a pass.
            $CleanupErrors += "Native worker left ordinary-user processes; cleanup terminated the recorded PIDs."
        }
        $Cleanup.remainingProcessIds = @($OwnedProcesses | ForEach-Object { $_.Id })
        $Cleanup.verified = $OwnedProcesses.Count -eq 0
        if (-not $Cleanup.verified) { $CleanupErrors += "Ordinary-user process cleanup remains incomplete." }
    } catch { $CleanupErrors += $_.Exception.Message }
    if ($FirewallCreated -and $Cleanup.verified) {
        try { Remove-NetFirewallRule -Name $FirewallRule -ErrorAction Stop }
        catch { $CleanupErrors += $_.Exception.Message }
    }
    if ($Created) {
        try { Remove-LocalUser -Name $UserName -ErrorAction Stop }
        catch { $CleanupErrors += $_.Exception.Message }
    }
    $Cleanup.errors = $CleanupErrors
    try {
        [IO.File]::WriteAllText(($Report + ".cleanup.json"), ($Cleanup | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    } catch { $CleanupErrors += "Could not write cleanup evidence: $($_.Exception.Message)" }
    if ($CleanupErrors.Count -gt 0) {
        if ($WorkerExitEvidence.supervisorError) { $CleanupErrors += "Original validation error: $($WorkerExitEvidence.supervisorError)" }
        throw ($CleanupErrors -join " ")
    }
}
