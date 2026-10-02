[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopPath = [Environment]::GetFolderPath('Desktop')

if ([string]::IsNullOrWhiteSpace($desktopPath)) {
    throw 'Windows did not return a Desktop folder for the current user.'
}

$shell = New-Object -ComObject WScript.Shell

function New-TerminalShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Launcher,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $launcherPath = Join-Path $repoRoot $Launcher
    if (-not (Test-Path -LiteralPath $launcherPath)) {
        throw "Launcher not found: $launcherPath"
    }

    $shortcutPath = Join-Path $desktopPath "$Name.lnk"
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $env:ComSpec
    $shortcut.Arguments = '/c ""' + $launcherPath + '""'
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Description = $Description
    $shortcut.WindowStyle = 1
    $shortcut.Save()

    Write-Host "Created desktop shortcut: $shortcutPath" -ForegroundColor Green
}

New-TerminalShortcut -Name 'Kotak Neo Terminal - Start' -Launcher 'Start Trading Terminal.cmd' -Description 'Start the Kotak Neo trading dashboard servers'
New-TerminalShortcut -Name 'Kotak Neo Terminal - Stop' -Launcher 'Stop Trading Terminal.cmd' -Description 'Stop the Kotak Neo trading dashboard servers'

Write-Host 'Double-click either desktop shortcut. The command window shows the result; press a key to close it.'