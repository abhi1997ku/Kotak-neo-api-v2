[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$NoShortcuts,
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot '.runtime'
$venvRoot = Join-Path $repoRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
$frontendRoot = Join-Path $repoRoot 'frontend'
$pythonSeries = '3.14'
$minimumNodeMajor = 22

function Update-ToolPath {
    $entries = @(
        $env:Path
        [Environment]::GetEnvironmentVariable('Path', 'Machine')
        [Environment]::GetEnvironmentVariable('Path', 'User')
        (Join-Path $env:ProgramFiles 'nodejs')
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs')
    )
    $env:Path = (($entries -join ';') -split ';' | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Install-Prerequisite([string]$PackageId) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "WinGet is missing. Install/update 'App Installer' from Microsoft Store, then run Setup.cmd again. Alternatively install Python $pythonSeries from python.org and Node.js LTS from nodejs.org."
    }
    Write-Host "Installing $PackageId using Windows Package Manager..." -ForegroundColor Cyan
    $installArgs = @('install', '--id', $PackageId, '--exact', '--source', 'winget', '--silent', '--accept-package-agreements', '--accept-source-agreements')
    if ($PackageId -like 'Python.Python.*') { $installArgs += @('--scope', 'user') }
    if ($NonInteractive) { $installArgs += '--disable-interactivity' }
    & $winget.Source @installArgs | Out-Host
    if ($LASTEXITCODE -notin @(0, 3010)) {
        throw "Installation of $PackageId failed (exit $LASTEXITCODE). Install it manually, then rerun Setup.cmd."
    }
    Update-ToolPath
}

function Test-ProjectPython([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        $details = & $Path -c "import sys; print('%s.%s' % sys.version_info[:2])" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$details".Trim() -eq $pythonSeries)
    } catch { return $false }
}

function Find-ProjectPython {
    $candidates = @()
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $candidate = & $launcher.Source "-$pythonSeries" -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += "$candidate".Trim() }
        } catch { }
    }
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
    $candidates += Join-Path $env:ProgramFiles 'Python314\python.exe'
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*') { $candidates += $command.Source }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-ProjectPython $candidate) { return $candidate }
    }
    return $null
}

function Find-ProjectNode {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($command) {
        $versionText = & $command.Source --version 2>$null
        if ($LASTEXITCODE -eq 0 -and "$versionText" -match '^v(\d+)\.' -and [int]$Matches[1] -ge $minimumNodeMajor) {
            return $command.Source
        }
    }
    return $null
}

function Get-SetupHash([string[]]$Files, [string]$Extra) {
    $parts = @('windows-setup-v1', $repoRoot, $Extra)
    foreach ($file in $Files) { $parts += (Get-FileHash -LiteralPath (Join-Path $repoRoot $file) -Algorithm SHA256).Hash }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($parts -join '|'))))).Replace('-', '') }
    finally { $sha.Dispose() }
}

function Test-SetupStamp([string]$Name, [string]$Expected) {
    $path = Join-Path $runtimeRoot $Name
    return ((Test-Path -LiteralPath $path) -and ((Get-Content -LiteralPath $path -Raw).Trim() -eq $Expected))
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$setupLock = $null
try {
    try { $setupLock = [IO.File]::Open((Join-Path $runtimeRoot 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Setup is already running for this folder. Wait for it to finish, then try Start again.' }
    Update-ToolPath

    if (-not (Test-ProjectPython $venvPython)) {
        $basePython = Find-ProjectPython
        if (-not $basePython) {
            Install-Prerequisite "Python.Python.$pythonSeries"
            $basePython = Find-ProjectPython
        }
        if (-not $basePython) { throw "Python $pythonSeries is still unavailable. Reopen Setup.cmd after installation." }
        if (Test-Path -LiteralPath $venvRoot) {
            # Keep an old/moved environment recoverable; never delete user files.
            $resolvedVenv = [IO.Path]::GetFullPath($venvRoot)
            $expectedVenv = [IO.Path]::GetFullPath((Join-Path $repoRoot '.venv'))
            $oldVenv = [IO.Path]::GetFullPath((Join-Path $runtimeRoot ('old-venv-' + [Guid]::NewGuid().ToString('N'))))
            if ($resolvedVenv -ne $expectedVenv -or -not $oldVenv.StartsWith([IO.Path]::GetFullPath($runtimeRoot) + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Refusing to move a Python environment outside this project.'
            }
            Move-Item -LiteralPath $resolvedVenv -Destination $oldVenv
            Write-Host "Previous environment retained at $oldVenv" -ForegroundColor Yellow
        }
        Write-Host 'Creating the project Python environment...' -ForegroundColor Cyan
        & $basePython -m venv $venvRoot | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
    }

    $node = Find-ProjectNode
    if (-not $node) {
        Install-Prerequisite 'OpenJS.NodeJS.LTS'
        $node = Find-ProjectNode
    }
    if (-not $node) { throw "Node.js $minimumNodeMajor or newer is unavailable. Reopen Setup.cmd after installing Node.js LTS." }
    $npm = Join-Path (Split-Path -Parent $node) 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npm)) { throw "npm.cmd is missing beside $node. Repair/reinstall Node.js LTS, then rerun Setup.cmd." }

    $pythonIdentity = & $venvPython -c "import sys; print(sys.version + '|' + sys.base_prefix)" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'The Python environment is not usable.' }
    $pythonHash = Get-SetupHash @('requirements.txt', 'requirements-windows.lock.txt') "$pythonIdentity"
    if ($Force -or -not (Test-SetupStamp 'python-deps.sha256' $pythonHash) -or -not (Test-Path -LiteralPath (Join-Path $venvRoot 'Scripts\uvicorn.exe'))) {
        Write-Host 'Installing the saved Python package versions (first setup or dependencies changed)...' -ForegroundColor Cyan
        & $venvPython -m pip install --no-input --disable-pip-version-check -r (Join-Path $repoRoot 'requirements.txt') -r (Join-Path $repoRoot 'requirements-windows.lock.txt') | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed. Check the message above and rerun Setup.cmd.' }
        & $venvPython -m pip check | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Python dependencies conflict; setup has not been marked complete.' }
        Set-Content -LiteralPath (Join-Path $runtimeRoot 'python-deps.sha256') -Value $pythonHash -Encoding ASCII
    }

    $nodeVersion = & $node --version
    $nodeHash = Get-SetupHash @('frontend\package.json', 'frontend\package-lock.json') "$node|$nodeVersion"
    if ($Force -or -not (Test-SetupStamp 'node-deps.sha256' $nodeHash) -or -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\vite\bin\vite.js')) -or -not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\.package-lock.json'))) {
        Write-Host 'Installing the saved frontend packages...' -ForegroundColor Cyan
        Push-Location $frontendRoot
        try {
            & $npm ci --no-audit --no-fund | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Frontend package installation failed. Check the message above and rerun Setup.cmd.' }
        } finally { Pop-Location }
        Set-Content -LiteralPath (Join-Path $runtimeRoot 'node-deps.sha256') -Value $nodeHash -Encoding ASCII
    }

    $envPath = Join-Path $repoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        Copy-Item -LiteralPath (Join-Path $repoRoot '.env.example') -Destination $envPath
        Write-Host 'Created .env from the example. Restore your private .env backup or fill in your API details before logging in.' -ForegroundColor Yellow
    }
    if (-not $NoShortcuts) { & (Join-Path $repoRoot 'create_desktop_shortcuts.ps1') }
    Write-Host 'Setup ready. Use the desktop Start and Stop shortcuts.' -ForegroundColor Green
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
