#requires -Version 7.5
<# Current-user desktop shortcut and owned local synthetic demo. No installation or system changes. #>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Launch', 'Stop', 'Status')][string]$Action = 'Launch',
    [string]$StateDirectory,
    [ValidateRange(0,65535)][int]$Port = 8765,
    [ValidateRange(1,120)][int]$TimeoutSeconds = 30,
    [switch]$NoBrowser,
    [switch]$Quiet,
    [string]$ResultFile
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$venvExecutable = Join-Path $projectDirectory '.venv/Scripts/python.exe'
$launcher = Join-Path $PSScriptRoot 'desktop_launcher.py'
$resultStream = $null

function Get-CurrentDesktop {
    if (-not ('ReceptionDesktopKnownFolder' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ReceptionDesktopKnownFolder {
    [DllImport("shell32.dll")] private static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);
    public static string CurrentDesktop() {
        Guid id = new Guid("B4BFCC3A-DB2C-424C-B029-7FE99A87C641");
        IntPtr value;
        int result = SHGetKnownFolderPath(ref id, 0, IntPtr.Zero, out value);
        Marshal.ThrowExceptionForHR(result);
        try { return Marshal.PtrToStringUni(value); }
        finally { Marshal.FreeCoTaskMem(value); }
    }
}
'@
    }
    return [ReceptionDesktopKnownFolder]::CurrentDesktop()
}

try {
    if ($ResultFile) {
        # Validate and reserve evidence BEFORE Install/Launch/Stop can have a
        # side effect. Use the same native lexical/local/reparse path guard as
        # the app; CreateNew never replaces previous evidence.
        if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) {
            throw 'Project .venv is missing; no shortcut or app was created.'
        }
        $recordPath = $ResultFile
        $recordRoot = Join-Path $projectDirectory 'artifacts/final-acceptance-runs'
        $validateCode = 'import sys; from pathlib import Path; from reception.platform_fs import validate_creation_path; p=validate_creation_path(sys.argv[1]); root=validate_creation_path(sys.argv[2],directory=True); assert p.is_relative_to(root) and p!=root, "ResultFile must use isolated final-acceptance-runs"; assert not p.exists(), "ResultFile already exists; no action started"; print(p)'
        $validationInfo = [Diagnostics.ProcessStartInfo]::new()
        $validationInfo.FileName = $venvExecutable
        $validationInfo.WorkingDirectory = $projectDirectory
        $validationInfo.UseShellExecute = $false
        $validationInfo.CreateNoWindow = $true
        $validationInfo.RedirectStandardOutput = $validationInfo.RedirectStandardError = $true
        $validationInfo.StandardOutputEncoding = $validationInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
        foreach ($argument in @('-B','-c',$validateCode,$recordPath,$recordRoot)) { $validationInfo.ArgumentList.Add($argument) }
        foreach ($name in @($validationInfo.Environment.Keys)) {
            if ($name -match '^(TWILIO_|DEMO_|PYTHON|PIP_)') { [void]$validationInfo.Environment.Remove($name) }
        }
        $validationInfo.Environment['PYTHONUTF8'] = '1'
        $validationInfo.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
        $validation = [Diagnostics.Process]::new()
        $validation.StartInfo = $validationInfo
        try {
            [void]$validation.Start()
            $checkedPath = $validation.StandardOutput.ReadToEnd()
            $validationError = $validation.StandardError.ReadToEnd()
            $validation.WaitForExit()
            if ($validation.ExitCode -ne 0) { throw ('ResultFile validation failed before action: ' + $validationError.Trim()) }
            $recordPath = $checkedPath.Trim()
        } finally { $validation.Dispose() }
        $resultStream = [IO.File]::Open($recordPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    }
    if ($Action -eq 'Install') {
        # Use the actual PowerShell 7.5+ host that executed this script; do not assume PATH installation.
        $hostExecutable = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
        if (-not ([IO.Path]::GetFileName($hostExecutable)).Equals('pwsh.exe', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Run this installer with the available PowerShell 7.5+ pwsh.exe host.'
        }
        if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) {
            throw ('Project .venv is missing. Restore/create the project environment first; no dependency was installed. See ' + (Join-Path $projectDirectory 'docs/WINDOWS_QUICK_START.md'))
        }
        $desktopDirectory = Get-CurrentDesktop
        $shortcutPath = Join-Path $desktopDirectory 'SMS Reception Assistant.lnk'
        $arguments = '-NoLogo -NoProfile -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'desktop.ps1') + '" -Action Launch'
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $status = 'created'
        if (Test-Path -LiteralPath $shortcutPath) {
            if (-not $shortcut.TargetPath.Equals($hostExecutable, [StringComparison]::OrdinalIgnoreCase) -or
                $shortcut.Arguments -ne $arguments -or
                -not $shortcut.WorkingDirectory.Equals($projectDirectory, [StringComparison]::OrdinalIgnoreCase)) {
                throw ('A same-name Desktop shortcut points elsewhere; it was not overwritten: ' + $shortcutPath)
            }
            $status = 'already_installed'
        } else {
            $shortcut.TargetPath = $hostExecutable
            $shortcut.Arguments = $arguments
            $shortcut.WorkingDirectory = $projectDirectory
            $shortcut.Description = 'SMS Reception Assistant — local DEMO / SIMULATED; real sending disabled'
            $shortcut.IconLocation = $venvExecutable + ',0'
            $shortcut.WindowStyle = 7
            $shortcut.Save()
        }
        $result = [ordered]@{status=$status; shortcut=$shortcutPath; desktop=$desktopDirectory; target=$shortcut.TargetPath;
                    arguments=$shortcut.Arguments; working_directory=$shortcut.WorkingDirectory; window_style=$shortcut.WindowStyle;
                    project_venv=$venvExecutable; synthetic_only=$true; real_outbound=$false}
    } else {
        if (-not (Test-Path -LiteralPath $venvExecutable -PathType Leaf)) {
            throw ('Project .venv is missing. No interpreter or package was installed. See ' + (Join-Path $projectDirectory 'docs/WINDOWS_QUICK_START.md'))
        }
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $venvExecutable
        $info.WorkingDirectory = $projectDirectory
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        foreach ($argument in @('-B', $launcher, $Action.ToLowerInvariant(), '--port', [string]$Port, '--timeout', [string]$TimeoutSeconds)) {
            $info.ArgumentList.Add($argument)
        }
        if ($StateDirectory) { $info.ArgumentList.Add('--state-directory'); $info.ArgumentList.Add($StateDirectory) }
        if ($NoBrowser) { $info.ArgumentList.Add('--no-browser') }
        foreach ($name in @($info.Environment.Keys)) {
            if ($name -match '^(TWILIO_|DEMO_|PYTHON|PIP_)') { [void]$info.Environment.Remove($name) }
        }
        $info.Environment['PYTHONUTF8'] = '1'
        $info.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
        $info.StandardOutputEncoding = $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $info
        try {
            [void]$process.Start()
            $outputTask = $process.StandardOutput.ReadToEndAsync()
            $errorTask = $process.StandardError.ReadToEndAsync()
            $process.WaitForExit()
            $output = $outputTask.GetAwaiter().GetResult()
            $diagnostic = $errorTask.GetAwaiter().GetResult()
            if ($process.ExitCode -ne 0) { throw $diagnostic.Trim() }
            $result = $output.Trim() | ConvertFrom-Json -DateKind String
        } finally { $process.Dispose() }
    }
    $json = $result | ConvertTo-Json -Depth 10 -Compress
    if ($null -ne $resultStream) {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json + [Environment]::NewLine)
        $resultStream.Write($bytes,0,$bytes.Length)
        $resultStream.Flush($true)
    }
    Write-Output $json
} catch {
    $message = $_.Exception.Message
    if (-not $Quiet) {
        Add-Type -AssemblyName System.Windows.Forms
        [void][Windows.Forms.MessageBox]::Show($message, 'SMS Reception Assistant', 'OK', 'Error')
    }
    Write-Error $message -ErrorAction Continue
    exit 2
} finally {
    if ($null -ne $resultStream) { $resultStream.Dispose() }
}
