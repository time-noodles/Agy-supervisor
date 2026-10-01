<#
.SYNOPSIS
    Antigravity CLI Supervisor Launcher for Windows PowerShell
.DESCRIPTION
    Launches Antigravity with AI Supervisor wrapper and ConPTY terminal handling.
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AgyArgs
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$SupervisorPy = Join-Path $ScriptDir "supervisor.py"

# UTF-8 Console Output
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Locate Python executable
$PythonBin = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonBin) {
    $PythonBin = Get-Command py -ErrorAction SilentlyContinue
}

if (-not $PythonBin) {
    Write-Error "Python 3 is required but was not found in PATH. Please install Python 3.9+."
    exit 1
}

& $PythonBin.Source $SupervisorPy @AgyArgs
exit $LASTEXITCODE
