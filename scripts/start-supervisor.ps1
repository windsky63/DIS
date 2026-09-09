param(
    [switch]$NoBrowser,
    [int]$RunSeconds = 0
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONIOENCODING = 'utf-8'

$systemRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$backendScript = Join-Path $systemRoot 'backend\server.py'
$frontendRoot = Join-Path $systemRoot 'frontend'
$frontendPackage = Join-Path $frontendRoot 'package.json'
$frontendModules = Join-Path $frontendRoot 'node_modules'
$viteScript = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class DrawingMarkRecognitionJob
{
    public const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    public const Int32 JobObjectExtendedLimitInformation = 9;

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetInformationJobObject(
        IntPtr job, Int32 informationClass, IntPtr information, UInt32 informationLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);
}
'@

function New-KillOnCloseJob {
    $job = [DrawingMarkRecognitionJob]::CreateJobObject([IntPtr]::Zero, $null)
    if ($job -eq [IntPtr]::Zero) { throw 'Cannot create the Windows process job.' }

    $information = New-Object DrawingMarkRecognitionJob+JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    $information.BasicLimitInformation.LimitFlags = [DrawingMarkRecognitionJob]::JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    $length = [Runtime.InteropServices.Marshal]::SizeOf($information)
    $pointer = [Runtime.InteropServices.Marshal]::AllocHGlobal($length)
    try {
        [Runtime.InteropServices.Marshal]::StructureToPtr($information, $pointer, $false)
        $configured = [DrawingMarkRecognitionJob]::SetInformationJobObject(
            $job, [DrawingMarkRecognitionJob]::JobObjectExtendedLimitInformation, $pointer, [uint32]$length
        )
        if (-not $configured) {
            throw "Cannot configure the process job. Windows error: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
        }
    }
    catch {
        [void][DrawingMarkRecognitionJob]::CloseHandle($job)
        throw
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($pointer)
    }
    return $job
}

function Add-ProcessToJob([IntPtr]$Job, [Diagnostics.Process]$Process) {
    if (-not [DrawingMarkRecognitionJob]::AssignProcessToJobObject($Job, $Process.Handle)) {
        throw "Cannot assign PID $($Process.Id) to the process job. Windows error: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
}

function Start-ConsoleProcess(
    [string]$FileName,
    [string]$Arguments,
    [string]$WorkingDirectory,
    [IntPtr]$Job
) {
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FileName
    $startInfo.Arguments = $Arguments
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $false
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Cannot start: $FileName $Arguments" }
    try {
        Add-ProcessToJob -Job $Job -Process $process
    }
    catch {
        if (-not $process.HasExited) { $process.Kill() }
        throw
    }
    return $process
}

function Wait-ServiceReady(
    [string]$Name,
    [string]$Url,
    [Diagnostics.Process]$Process,
    [int]$TimeoutSeconds = 60
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Process.HasExited) { throw "$Name exited early with code $($Process.ExitCode)." }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Start-Sleep -Milliseconds 750
                if ($Process.HasExited) {
                    throw "$Name exited during its readiness check with code $($Process.ExitCode)."
                }
                Write-Host "[READY] $Name - $Url" -ForegroundColor Green
                return
            }
        }
        catch { Start-Sleep -Milliseconds 500 }
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds: $Url"
}

function Assert-PortFree([int]$Port) {
    $listener = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $Port }
    if ($listener) {
        throw "Port $Port is already in use. Stop the old service and retry."
    }
}

$job = [IntPtr]::Zero
$backendProcess = $null
$frontendProcess = $null

try {
    if (-not (Test-Path -LiteralPath $backendScript -PathType Leaf)) { throw "Backend entry is missing: $backendScript" }
    if (-not (Test-Path -LiteralPath $frontendPackage -PathType Leaf)) { throw "Frontend package is missing: $frontendPackage" }
    if (-not (Test-Path -LiteralPath $frontendModules -PathType Container)) {
        throw 'Vue dependencies are missing. Run npm.cmd install in the frontend directory.'
    }
    if (-not (Test-Path -LiteralPath $viteScript -PathType Leaf)) {
        throw "Vite entry is missing: $viteScript"
    }

    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $node = (Get-Command node.exe -ErrorAction Stop).Source

    Assert-PortFree -Port 8768
    Assert-PortFree -Port 3004
    $job = New-KillOnCloseJob

    Write-Host '[START] Python topology API...' -ForegroundColor Cyan
    $backendProcess = Start-ConsoleProcess `
        -FileName $python -Arguments '-u -X utf8 backend\server.py' -WorkingDirectory $systemRoot -Job $job
    Wait-ServiceReady -Name 'Python API' -Url 'http://127.0.0.1:8768/api/health' -Process $backendProcess

    Write-Host '[START] Vue 3 frontend...' -ForegroundColor Cyan
    $viteArguments = '"' + $viteScript + '" --host 127.0.0.1 --port 3004'
    $frontendProcess = Start-ConsoleProcess `
        -FileName $node -Arguments $viteArguments -WorkingDirectory $frontendRoot -Job $job
    Wait-ServiceReady -Name 'Vue frontend' -Url 'http://127.0.0.1:3004/' -Process $frontendProcess

    Write-Host ''
    Write-Host 'System is ready: http://127.0.0.1:3004' -ForegroundColor Green
    Write-Host 'Keep this window open. Closing it stops both services.' -ForegroundColor Yellow
    if (-not $NoBrowser) {
        Start-Process 'http://127.0.0.1:3004'
    }

    $startedAt = [DateTime]::UtcNow
    while ($true) {
        if ($backendProcess.HasExited) { throw "Python API stopped with code $($backendProcess.ExitCode)." }
        if ($frontendProcess.HasExited) { throw "Vue frontend stopped with code $($frontendProcess.ExitCode)." }
        if ($RunSeconds -gt 0 -and ([DateTime]::UtcNow - $startedAt).TotalSeconds -ge $RunSeconds) { break }
        Start-Sleep -Seconds 1
    }
}
catch {
    Write-Host ''
    Write-Host "[FAILED] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($job -ne [IntPtr]::Zero) {
        Write-Host ''
        Write-Host '[STOP] Shutting down backend and frontend...' -ForegroundColor Yellow
        [void][DrawingMarkRecognitionJob]::CloseHandle($job)
    }
    foreach ($process in @($frontendProcess, $backendProcess)) {
        if (-not $process) { continue }
        try {
            if (-not $process.HasExited) {
                [void]$process.WaitForExit(5000)
            }
            if (-not $process.HasExited) {
                $process.Kill()
                [void]$process.WaitForExit(2000)
            }
        }
        catch { }
        finally { $process.Dispose() }
    }
}
