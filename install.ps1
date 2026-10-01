<#
.SYNOPSIS
    Antigravity Supervisor Windows Installer for PowerShell
.DESCRIPTION
    Configures PowerShell $PROFILE with 'agy-c' and 'agy-sup' functions to run Antigravity
    with autonomous AI Supervisor supervision.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RunnerPs1 = Join-Path $ScriptDir "run-supervisor.ps1"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Antigravity Supervisor Windows Installer" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Check Python
$PythonBin = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonBin) {
    $PythonBin = Get-Command py -ErrorAction SilentlyContinue
}

if (-not $PythonBin) {
    Write-Warning "Python 3 is not found in PATH. Please install Python 3.9+ from python.org or Microsoft Store."
} else {
    Write-Host "[✓] Python found: $($PythonBin.Source)" -ForegroundColor Green
}

# 2. Check agy command
$AgyBin = Get-Command agy -ErrorAction SilentlyContinue
if (-not $AgyBin) {
    Write-Warning "Antigravity CLI ('agy') not found in PATH. Ensure agy is installed and added to PATH."
} else {
    Write-Host "[✓] Antigravity CLI found: $($AgyBin.Source)" -ForegroundColor Green
}

# 3. Setup PowerShell $PROFILE
if (-not (Test-Path $PROFILE)) {
    $ProfileDir = Split-Path -Parent $PROFILE
    if (-not (Test-Path $ProfileDir)) {
        New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
    }
    New-Item -ItemType File -Path $PROFILE -Force | Out-Null
    Write-Host "[✓] Created new PowerShell Profile: $PROFILE" -ForegroundColor Green
}

$ProfileContent = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue
$MarkerStart = "# >>> Antigravity Supervisor >>>"
$MarkerEnd = "# <<< Antigravity Supervisor <<<"

$FunctionBlock = @"
$MarkerStart
function agy-c {
    & "$RunnerPs1" `$args
}
function agy-sup {
    & "$RunnerPs1" `$args
}
$MarkerEnd
"@

if ($ProfileContent -match [regex]::Escape($MarkerStart)) {
    # Replace existing block
    $Regex = [regex]::Escape($MarkerStart) + "[\s\S]*?" + [regex]::Escape($MarkerEnd)
    $NewContent = [regex]::Replace($ProfileContent, $Regex, $FunctionBlock)
    Set-Content -Path $PROFILE -Value $NewContent -Encoding UTF8
    Write-Host "[✓] Updated existing Antigravity Supervisor functions in $PROFILE" -ForegroundColor Green
} else {
    # Append
    Add-Content -Path $PROFILE -Value "`n$FunctionBlock" -Encoding UTF8
    Write-Host "[✓] Appended 'agy-c' and 'agy-sup' functions to $PROFILE" -ForegroundColor Green
}

Write-Host "`nInstallation Complete! 🎉" -ForegroundColor Green
Write-Host "Restart your PowerShell terminal or run: . `$PROFILE" -ForegroundColor Yellow
Write-Host "Then simply run 'agy-c' to launch Antigravity with AI Supervisor!" -ForegroundColor Cyan
