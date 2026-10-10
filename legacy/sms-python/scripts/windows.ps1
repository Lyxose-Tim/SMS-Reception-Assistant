#requires -Version 7.5
<#
Project-local Windows setup and synthetic, foreground service acceptance.
This launcher never activates real sending or stores client credentials.
#>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Start', 'Stop', 'Test', 'Backup', 'VerifyBackup', 'Restore', 'Maintenance', 'AcknowledgeRestore')]
    [string]$Action = 'Start',
    [string]$Database,
    [string]$RunDirectory,
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 30,
    [ValidateRange(60,86400)]
    [int]$SessionTtlSeconds = 3600,
    [string]$PythonExecutable,
    [string]$BackupPath,
    [ValidateRange(1, 365)]
    [int]$RetentionDays = 30,
    [switch]$OfflineConfirmed,
    [switch]$ReviewConfirmed,
    [string]$TestPattern = 'test*.py',
    [string]$IdsFile,
    [string]$SyntheticPassword = 'Synthetic Windows password 123!',
    [string]$SyntheticPrivacyKey = 'windows-synthetic-privacy-key-32-characters'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$venvExecutable = Join-Path $projectDirectory '.venv/Scripts/python.exe'
$runsDirectory = Join-Path $projectDirectory 'artifacts/windows-alignment-runs'

function Resolve-ProjectPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'A project path is required.' }
    $resolved = [IO.Path]::GetFullPath($Value, $projectDirectory)
    $prefix = $projectDirectory.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'This synthetic launcher only uses paths inside the project.'
    }
    $externalEvidence = Join-Path $projectDirectory 'artifacts/windows-alignment-evidence'
    if ($resolved.Equals($externalEvidence, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($externalEvidence + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'External evidence is read-only and cannot be used as a run directory.'
    }
    $probePath = $resolved
    while ($probePath.Length -ge $projectDirectory.Length) {
        if (Test-Path -LiteralPath $probePath) {
            $item = Get-Item -LiteralPath $probePath -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Synthetic run paths must not traverse reparse points.'
            }
        }
        if ($probePath.Equals($projectDirectory, [StringComparison]::OrdinalIgnoreCase)) { break }
        $probePath = [IO.Path]::GetDirectoryName($probePath)
    }
    return $resolved
}

function Resolve-RunPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = Join-Path $runsDirectory (([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')) + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    }
    $resolved = Resolve-ProjectPath $Value
    $allowedRunRoots = @($runsDirectory, (Join-Path $projectDirectory 'artifacts/final-acceptance-runs'))
    $allowedRun = $false
    foreach ($allowedRoot in $allowedRunRoots) {
        if ($resolved.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { $allowedRun = $true }
    }
    if (-not $allowedRun) {
        throw 'RunDirectory must be a dedicated directory under artifacts/windows-alignment-runs or artifacts/final-acceptance-runs.'
    }
    return $resolved
}

function Read-JsonFile([string]$Path) {
    # Lifecycle JSON is atomically replaced by the app. Readers must permit
    # Windows replacement while holding a complete old-file snapshot.
    $sharing = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $sharing)
    $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false), $true)
    try { $json = $reader.ReadToEnd() }
    finally { $reader.Dispose() }
    return $json | ConvertFrom-Json -DateKind String
}

function Write-NewJson([string]$Path, [object]$Value) {
    $json = ($Value | ConvertTo-Json -Depth 8) + [Environment]::NewLine
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Write-CommandLog([string]$Path, [string]$Started, [int]$ExitCode, [string]$Output, [string]$Diagnostic) {
    $text = "started_utc: $Started`nfinished_utc: $([DateTime]::UtcNow.ToString('o'))`nexit_code: $ExitCode`n--- stdout ---`n$Output`n--- stderr ---`n$Diagnostic"
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($text)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function New-ProjectProcess([string]$Executable, [string[]]$Arguments, [string]$Cache, [bool]$Capture = $false) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable
    $info.WorkingDirectory = $projectDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $Capture
    $info.RedirectStandardError = $Capture
    $info.RedirectStandardInput = $Capture
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    foreach ($name in @($info.Environment.Keys)) {
        if ($name -match '^(TWILIO_|DEMO_|PYTHON|PIP_)' -or $name -in @(
            'APP_MODE', 'ENABLE_REAL_SEND', 'ADMIN_PASSWORD_HASH', 'PRIVACY_HMAC_KEY',
            'ADMIN_ORIGIN', 'TRUSTED_PROXY_CIDRS', 'RECIPIENT_POLICY',
            'INBOUND_SERVICE_APPROVAL_RECORDED', 'CLIENT_READINESS_APPROVED',
            'TEST_SEND_APPROVAL_RECORDED', 'TEST_RECIPIENT_ALLOWLIST', 'EMERGENCY_STOP_FILE',
            'MAX_MESSAGES_PER_MINUTE', 'MAX_MESSAGES_PER_DAY', 'MAX_SEGMENTS_PER_MESSAGE',
            'MAX_DAILY_SEGMENTS', 'REPLY_WINDOW_HOURS', 'REQUEST_TIMEOUT_SECONDS',
            'SESSION_TTL_SECONDS', 'RETENTION_DAYS', 'INBOUND_MAX_AGE_SECONDS')) {
            [void]$info.Environment.Remove($name)
        }
    }
    $info.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    $info.Environment['PYTHONUTF8'] = '1'
    if ($Cache) {
        $info.Environment['TMP'] = $Cache
        $info.Environment['TEMP'] = $Cache
    }
    if ($Capture) {
        $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    return $process
}

function Get-ProcessIdentity([int]$ProcessId) {
    $process = [Diagnostics.Process]::GetProcessById($ProcessId)
    try {
        $row = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$ProcessId"
        if ($null -eq $row) { throw 'Owned process is no longer present.' }
        return [ordered]@{
            pid = $ProcessId
            process_started_at_os = $process.StartTime.ToUniversalTime().ToString('o')
            parent_pid = [int]$row.ParentProcessId
            os_executable = $process.MainModule.FileName
            os_command_line = $row.CommandLine
        }
    } finally { $process.Dispose() }
}

function Assert-Identity([object]$Expected) {
    $actual = Get-ProcessIdentity ([int]$Expected.pid)
    if ($actual.process_started_at_os -ne $Expected.process_started_at_os -or
        -not $actual.os_executable.Equals([string]$Expected.os_executable, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Process identity changed; refusing to act on a reused PID.'
    }
    return $actual
}

function Request-OwnedStop([string]$RunPath, [object]$Ready) {
    $manifest = Read-JsonFile (Join-Path $RunPath 'run-manifest.json')
    if ($manifest.run_id -ne $Ready.run_id -or [int]$manifest.pid -ne [int]$Ready.app_identity.pid) {
        throw 'Application manifest does not match this owned run.'
    }
    [void](Assert-Identity $Ready.app_identity)
    if ($manifest.real_outbound -ne $false) { throw 'This stop entry only controls its synthetic dry-run instance.' }
    $requestPath = Join-Path $RunPath 'stop-request.json'
    $request = [ordered]@{ run_id = $manifest.run_id; pid = [int]$manifest.pid }
    if (Test-Path -LiteralPath $requestPath) {
        $existing = Read-JsonFile $requestPath
        if ($existing.run_id -ne $request.run_id -or [int]$existing.pid -ne $request.pid) {
            throw 'An unrelated stop request already exists; refusing to overwrite it.'
        }
        return
    }
    $temporary = Join-Path $RunPath ('stop-request-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    Write-NewJson $temporary $request
    try { [IO.File]::Move($temporary, $requestPath) }
    finally { if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) } }
}

function Assert-SyntheticStore([string]$Path, [string]$Cache) {
    $code = @'
import sqlite3,sys
from pathlib import Path
path=Path(sys.argv[1])
connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
try:
    row=connection.execute("SELECT value FROM settings WHERE key='windows_acceptance_synthetic'").fetchone()
    if not row or row[0]!='true':
        raise ValueError('Store is not marked for synthetic Windows acceptance')
finally:
    connection.close()
'@
    $check = New-ProjectProcess $venvExecutable @('-B', '-c', $code, $Path) $Cache $true
    try {
        [void]$check.Start()
        $check.StandardInput.Close()
        $output = $check.StandardOutput.ReadToEnd()
        $diagnostic = $check.StandardError.ReadToEnd()
        $check.WaitForExit()
        if ($check.ExitCode -ne 0) { throw 'Synthetic-only store validation failed; no operation was started.' }
        if ($diagnostic.Trim()) { Write-Warning $diagnostic.Trim() }
    } finally { $check.Dispose() }
}

function Invoke-SetupCommand([string]$Executable, [string[]]$Arguments, [string]$RunPath, [string]$Label) {
    $process = New-ProjectProcess $Executable $Arguments (Join-Path $RunPath 'cache') $true
    # pip honors this special value even in isolated mode: no machine, user or
    # environment pip configuration can add another index or install target.
    $process.StartInfo.Environment['PIP_CONFIG_FILE'] = 'nul'
    $started = [DateTime]::UtcNow.ToString('o')
    try {
        [void]$process.Start()
        $process.StandardInput.Close()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $output = $outputTask.GetAwaiter().GetResult()
        $diagnostic = $errorTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    } finally { $process.Dispose() }
    $record = [ordered]@{command = $Label; executable = $Executable; arguments = $Arguments; started_utc = $started; finished_utc = [DateTime]::UtcNow.ToString('o'); exit_code = $exitCode; stdout = $output; stderr = $diagnostic}
    Write-NewJson (Join-Path $RunPath ($Label + '.json')) $record
    if ($output.Trim()) { Write-Host $output.Trim() }
    if ($diagnostic.Trim()) { Write-Warning $diagnostic.Trim() }
    if ($exitCode -ne 0) { throw ($Label + ' failed; inspect this run. Global Python/configuration was not changed.') }
}

if ($Action -eq 'Setup') {
    $setupRun = Resolve-RunPath $RunDirectory
    if (Test-Path -LiteralPath $setupRun) { throw 'Setup requires a new evidence directory.' }
    [void][IO.Directory]::CreateDirectory((Join-Path $setupRun 'cache'))
    if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) {
        if (-not $PythonExecutable) { $PythonExecutable = (Get-Command python -CommandType Application).Source }
        if (-not [IO.Path]::IsPathFullyQualified($PythonExecutable)) { throw 'PythonExecutable must be an absolute existing executable.' }
        Invoke-SetupCommand $PythonExecutable @('-B', '-m', 'venv', (Join-Path $projectDirectory '.venv')) $setupRun '01-create-venv'
    }
    $prefixCheck = 'from pathlib import Path; import sys; expected=Path(sys.argv[1]).resolve(); assert Path(sys.prefix).resolve()==expected and sys.prefix!=sys.base_prefix, "Project venv prefix mismatch"; import pip; assert Path(pip.__file__).resolve().is_relative_to(expected), "pip resolved outside project venv"; print({"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix})'
    Invoke-SetupCommand $venvExecutable @('-B', '-c', $prefixCheck, (Join-Path $projectDirectory '.venv')) $setupRun '02-verify-prefix'
    Invoke-SetupCommand $venvExecutable @('-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-cache-dir', '--index-url', 'https://pypi.org/simple', '--only-binary=:all:', '-r', (Join-Path $projectDirectory 'requirements-windows.lock.txt')) $setupRun '03-install-declared'
    Invoke-SetupCommand $venvExecutable @('-B', '-m', 'pip', '--isolated', 'check') $setupRun '04-pip-check'
    Invoke-SetupCommand $venvExecutable @('-B', '-c', 'import ssl,sqlite3,sys,twilio; print({"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,"python":sys.version,"sqlite":sqlite3.sqlite_version,"twilio":twilio.__version__,"ssl":ssl.OPENSSL_VERSION})') $setupRun '05-runtime-imports'
    [ordered]@{status = 'setup_checked'; run_directory = $setupRun; requested_venv_executable = $venvExecutable} | ConvertTo-Json -Compress
    exit 0
}

if ($Action -eq 'Test') {
    if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) { throw 'Create the project venv with Setup first.' }
    $runPath = Resolve-RunPath $RunDirectory
    $runner = Join-Path $projectDirectory 'scripts/run_offline_regression.py'
    if (-not [IO.File]::ReadAllText($runner).Contains('--output-dir')) {
        throw 'The regression runner has not yet been upgraded for run-isolated output; old evidence is protected.'
    }
    if (Test-Path -LiteralPath $runPath) { throw 'Test output directory must not exist; the runner creates it exclusively.' }
    $launchCache = Resolve-ProjectPath ($runPath + '.launch-temp')
    if (Test-Path -LiteralPath $launchCache) { throw 'Test launch cache belongs to an earlier run.' }
    [void][IO.Directory]::CreateDirectory($launchCache)
    $testArguments = @('-B', $runner, '--output-dir', $runPath, '--pattern', $TestPattern)
    if ($IdsFile) { $testArguments += @('--ids-file', (Resolve-ProjectPath $IdsFile)) }
    $test = New-ProjectProcess $venvExecutable $testArguments $launchCache $true
    $test.StartInfo.Environment['ENABLE_REAL_SEND'] = 'false'
    $testStarted = [DateTime]::UtcNow.ToString('o')
    try {
        [void]$test.Start()
        $test.StandardInput.Close()
        $outputTask = $test.StandardOutput.ReadToEndAsync()
        $errorTask = $test.StandardError.ReadToEndAsync()
        $test.WaitForExit()
        $output = $outputTask.GetAwaiter().GetResult()
        $diagnostic = $errorTask.GetAwaiter().GetResult()
        $testExit = $test.ExitCode
    } finally { $test.Dispose() }
    Write-CommandLog (Join-Path $launchCache 'test-launch.log') $testStarted $testExit $output $diagnostic
    if ($output.Trim()) { Write-Host $output.Trim() }
    if ($diagnostic.Trim()) { Write-Warning $diagnostic.Trim() }
    exit $testExit
}

$runPath = Resolve-RunPath $RunDirectory
if ($Action -in @('Backup', 'VerifyBackup', 'Restore', 'Maintenance', 'AcknowledgeRestore')) {
    if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) { throw 'Create the project venv with Setup first.' }
    if (Test-Path -LiteralPath $runPath) { throw 'Operator commands require a new run directory; prior evidence is preserved.' }
    if ($Action -in @('Restore', 'Maintenance', 'AcknowledgeRestore') -and -not $OfflineConfirmed) {
        throw 'Stop all database users, then explicitly supply -OfflineConfirmed.'
    }
    if ($Action -eq 'AcknowledgeRestore' -and -not $ReviewConfirmed) {
        throw 'Post-backup sending/withdrawal review requires explicit -ReviewConfirmed.'
    }
    [void][IO.Directory]::CreateDirectory($runPath)
    $cache = Join-Path $runPath 'cache'
    [void][IO.Directory]::CreateDirectory($cache)
    $operatorArguments = @('-B', (Join-Path $projectDirectory 'manage.py'))
    if ($Action -eq 'VerifyBackup') {
        $backupFile = Resolve-ProjectPath $BackupPath
        Assert-SyntheticStore $backupFile $cache
        $operatorArguments += @('verify-backup', '--backup', $backupFile)
    } else {
        $databasePath = Resolve-ProjectPath $Database
        Assert-SyntheticStore $databasePath $cache
        switch ($Action) {
            'Backup' {
                $backupFile = Resolve-ProjectPath $(if ($BackupPath) { $BackupPath } else { Join-Path $runPath 'verified-backup.sqlite3' })
                if (Test-Path -LiteralPath $backupFile) { throw 'Backup output must be new; nothing is overwritten.' }
                $operatorArguments += @('backup', '--db', $databasePath, '--output', $backupFile)
            }
            'Restore' {
                $backupFile = Resolve-ProjectPath $BackupPath
                Assert-SyntheticStore $backupFile $cache
                $operatorArguments += @('restore', '--db', $databasePath, '--backup', $backupFile, '--offline-confirmed')
            }
            'Maintenance' { $operatorArguments += @('maintenance', '--db', $databasePath, '--retention-days', [string]$RetentionDays, '--offline-confirmed') }
            'AcknowledgeRestore' { $operatorArguments += @('acknowledge-restore', '--db', $databasePath, '--offline-confirmed', '--review-confirmed') }
        }
    }
    $record = [ordered]@{action = $Action; requested_venv_executable = $venvExecutable; arguments = $operatorArguments; synthetic_only = $true; started_utc = [DateTime]::UtcNow.ToString('o')}
    Write-NewJson (Join-Path $runPath 'operation-request.json') $record
    $operation = New-ProjectProcess $venvExecutable $operatorArguments $cache $true
    $operation.StartInfo.Environment['ENABLE_REAL_SEND'] = 'false'
    $operation.StartInfo.Environment['PRIVACY_HMAC_KEY'] = $SyntheticPrivacyKey
    try {
        [void]$operation.Start()
        $operation.StandardInput.Close()
        $outputTask = $operation.StandardOutput.ReadToEndAsync()
        $errorTask = $operation.StandardError.ReadToEndAsync()
        $operation.WaitForExit()
        $output = $outputTask.GetAwaiter().GetResult()
        $diagnostic = $errorTask.GetAwaiter().GetResult()
        $operationExit = $operation.ExitCode
    } finally { $operation.Dispose() }
    Write-CommandLog (Join-Path $runPath 'operation.log') $record.started_utc $operationExit $output $diagnostic
    if ($output.Trim()) { Write-Host $output.Trim() }
    if ($diagnostic.Trim()) { Write-Warning $diagnostic.Trim() }
    $record = [ordered]@{action = $Action; exit_code = $operationExit; synthetic_only = $true; finished_utc = [DateTime]::UtcNow.ToString('o')}
    Write-NewJson (Join-Path $runPath 'operation-result.json') $record
    exit $operationExit
}
if ($Action -eq 'Stop') {
    $ready = Read-JsonFile (Join-Path $runPath 'launcher-ready.json')
    $manifest = Read-JsonFile (Join-Path $runPath 'run-manifest.json')
    if ($manifest.run_id -ne $ready.run_id -or [int]$manifest.pid -ne [int]$ready.app_identity.pid) {
        throw 'The requested directory belongs to a different application run.'
    }
    if ($manifest.status -eq 'stopped' -and $manifest.instance_lock -eq 'released') {
        [ordered]@{ status = 'already_stopped'; run_id = $ready.run_id; run_directory = $runPath } | ConvertTo-Json -Compress
        exit 0
    }
    $owned = [Diagnostics.Process]::GetProcessById([int]$ready.app_identity.pid)
    try {
        [void]$owned.SafeHandle
        if ($owned.StartTime.ToUniversalTime().ToString('o') -ne $ready.app_identity.process_started_at_os) {
            throw 'App PID creation time changed; no stop request was written.'
        }
        Request-OwnedStop $runPath $ready
        if (-not $owned.WaitForExit($TimeoutSeconds * 1000)) {
            throw 'Graceful stop timed out. No process was terminated; inspect this owned run.'
        }
    } finally { $owned.Dispose() }
    $manifest = Read-JsonFile (Join-Path $runPath 'run-manifest.json')
    if ($manifest.status -ne 'stopped' -or $manifest.instance_lock -ne 'released') {
        throw 'The process exited without a recorded completed drain and lock release.'
    }
    [ordered]@{ status = 'stopped'; run_id = $ready.run_id; app_pid = $ready.app_identity.pid; run_directory = $runPath } | ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) { throw 'Create the project venv with Setup first.' }
foreach ($name in @('launcher-ownership.json', 'launcher-ready.json', 'launcher-exit.json', 'run-manifest.json', 'stop-request.json')) {
    if (Test-Path -LiteralPath (Join-Path $runPath $name)) { throw 'Start requires a fresh application run directory; existing records are never overwritten.' }
}
[void][IO.Directory]::CreateDirectory($runPath)
$cache = Resolve-ProjectPath (Join-Path $runPath 'cache')
[void][IO.Directory]::CreateDirectory($cache)
$databasePath = Resolve-ProjectPath $(if ($Database) { $Database } else { Join-Path $runPath 'synthetic-service.sqlite3' })
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($databasePath))
$bootstrapCode = @'
import json,sqlite3,sys
from pathlib import Path
from reception.auth import hash_password
value=json.load(sys.stdin)
path=Path(value['database'])
if path.exists():
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        row=connection.execute("SELECT value FROM settings WHERE key='windows_acceptance_synthetic'").fetchone()
        if not row or row[0]!='true':
            raise ValueError('Existing database is not marked for synthetic Windows acceptance')
    finally:
        connection.close()
print(hash_password(value['password']))
'@
$bootstrap = New-ProjectProcess $venvExecutable @('-B', '-c', $bootstrapCode) $cache $true
try {
    [void]$bootstrap.Start()
    $bootstrap.StandardInput.WriteLine((@{database = $databasePath; password = $SyntheticPassword} | ConvertTo-Json -Compress))
    $bootstrap.StandardInput.Close()
    $hash = $bootstrap.StandardOutput.ReadToEnd().Trim()
    $bootstrapError = $bootstrap.StandardError.ReadToEnd()
    $bootstrap.WaitForExit()
    if ($bootstrap.ExitCode -ne 0) { throw ('Synthetic bootstrap rejected the input: ' + $bootstrapError) }
    if ($bootstrapError.Trim()) { Write-Warning $bootstrapError.Trim() }
} finally { $bootstrap.Dispose() }

$arguments = @('-B', (Join-Path $projectDirectory 'app.py'), '--db', $databasePath, '--port', [string]$Port, '--run-dir', $runPath)
$child = New-ProjectProcess $venvExecutable $arguments $cache $true
$child.StartInfo.Environment['APP_MODE'] = 'service'
$child.StartInfo.Environment['ENABLE_REAL_SEND'] = 'false'
$child.StartInfo.Environment['ADMIN_PASSWORD_HASH'] = $hash
$child.StartInfo.Environment['PRIVACY_HMAC_KEY'] = $SyntheticPrivacyKey
$child.StartInfo.Environment['ADMIN_ORIGIN'] = ''
$child.StartInfo.Environment['TRUSTED_PROXY_CIDRS'] = ''
$child.StartInfo.Environment['RECIPIENT_POLICY'] = 'test_allowlist'
$child.StartInfo.Environment['SESSION_TTL_SECONDS'] = [string]$SessionTtlSeconds
$child.StartInfo.Environment['TWILIO_PHONE_NUMBER'] = '+12025550100'
$child.StartInfo.Environment['TWILIO_ACCOUNT_SID'] = ''
$child.StartInfo.Environment['TWILIO_AUTH_TOKEN'] = ''
$ready = $null
$childStarted = $false
$applicationStarted = [DateTime]::UtcNow.ToString('o')
$applicationOutputTask = $applicationErrorTask = $null
$applicationLogWritten = $false
try {
    [void]$child.Start()
    $childStarted = $true
    $child.StandardInput.Close()
    $applicationOutputTask = $child.StandardOutput.ReadToEndAsync()
    $applicationErrorTask = $child.StandardError.ReadToEndAsync()
    $launcherIdentity = Get-ProcessIdentity $child.Id
    $ownership = [ordered]@{
        requested_venv_executable = $venvExecutable
        arguments = $arguments
        launcher_identity = $launcherIdentity
        run_directory = $runPath
        requested_database = $databasePath
        synthetic_only = $true
        app_mode = 'service'
        real_outbound = $false
        started_utc = [DateTime]::UtcNow.ToString('o')
    }
    Write-NewJson (Join-Path $runPath 'launcher-ownership.json') $ownership
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $manifestPath = Join-Path $runPath 'run-manifest.json'
    while ([DateTime]::UtcNow -lt $deadline -and -not $child.HasExited) {
        if (Test-Path -LiteralPath $manifestPath) {
            $manifest = Read-JsonFile $manifestPath
            if ($manifest.status -eq 'failed') { throw 'The application recorded initialization failure.' }
            if ($manifest.status -eq 'running') {
                $appIdentity = Get-ProcessIdentity ([int]$manifest.pid)
                if ($appIdentity.pid -ne $launcherIdentity.pid -and $appIdentity.parent_pid -ne $launcherIdentity.pid) {
                    throw 'Application PID is not the process started by this launcher or its venv redirector child.'
                }
                # Retain minimal verified ownership for graceful cleanup even if
                # a later readiness-field check fails.
                $ready = [ordered]@{run_id = $manifest.run_id; app_identity = $appIdentity}
                if ($manifest.real_outbound -ne $false -or
                    -not ([string]$manifest.python_executable).Equals($venvExecutable, [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Application runtime does not match this project venv and dry-run launch.'
                }
                $ready = [ordered]@{
                    run_id = $manifest.run_id
                    run_directory = $runPath
                    requested_venv_executable = $venvExecutable
                    actual_sys_executable = $manifest.python_executable
                    python_prefix = $manifest.python_prefix
                    base_prefix = $manifest.base_prefix
                    launcher_identity = $launcherIdentity
                    app_identity = $appIdentity
                    database = $manifest.db_path
                    url = $manifest.url
                    port = $manifest.port
                    app_mode = 'service'
                    real_outbound = $false
                    synthetic_only = $true
                }
                Write-NewJson (Join-Path $runPath 'launcher-ready.json') $ready
                $ready | ConvertTo-Json -Depth 8 -Compress
                break
            }
        }
        Start-Sleep -Milliseconds 100
    }
    if ($null -eq $ready) { throw 'Application readiness was not established; inspect this run without reusing its directory.' }
    $child.WaitForExit()
    $exitCode = $child.ExitCode
    Write-CommandLog (Join-Path $runPath 'application.log') $applicationStarted $exitCode $applicationOutputTask.GetAwaiter().GetResult() $applicationErrorTask.GetAwaiter().GetResult()
    $applicationLogWritten = $true
    $exitRecord = [ordered]@{run_id = $ready.run_id; launcher_exit_code = $exitCode; finished_utc = [DateTime]::UtcNow.ToString('o'); run_directory = $runPath}
    Write-NewJson (Join-Path $runPath 'launcher-exit.json') $exitRecord
    $exitRecord | ConvertTo-Json -Compress
    exit $exitCode
} finally {
    if ($childStarted -and $null -ne $ready) {
        try {
            $stillOwned = [Diagnostics.Process]::GetProcessById([int]$ready.app_identity.pid)
            try {
                [void]$stillOwned.SafeHandle
                if ($stillOwned.StartTime.ToUniversalTime().ToString('o') -eq $ready.app_identity.process_started_at_os) {
                    Request-OwnedStop $runPath $ready
                    if (-not $stillOwned.WaitForExit($TimeoutSeconds * 1000)) { Write-Warning 'Owned app did not finish its graceful stop; no process was terminated.' }
                }
            } finally { $stillOwned.Dispose() }
        } catch [ArgumentException] {
            # An already-exited owned process requires no action.
        } catch { Write-Warning 'Owned stop could not be validated; no unrelated process was touched.' }
    }
    if ($childStarted -and $child.HasExited -and -not $applicationLogWritten) {
        Write-CommandLog (Join-Path $runPath 'application.log') $applicationStarted $child.ExitCode $applicationOutputTask.GetAwaiter().GetResult() $applicationErrorTask.GetAwaiter().GetResult()
    }
    $child.Dispose()
}
