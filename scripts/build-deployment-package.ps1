[CmdletBinding()]
param(
    [switch]$IncludeSecrets,
    [string]$OutputDirectory = 'releases'
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$projectPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $outputPath.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output directory must be inside the project directory.'
}

$packageJson = Get-Content -LiteralPath (Join-Path $projectRoot 'frontend/package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$packageJson.version
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$archiveName = "drawing-mark-recognition-deploy-v$version-$timestamp.zip"
$archivePath = Join-Path $outputPath $archiveName
$checksumPath = "$archivePath.sha256"
$stagePath = Join-Path $outputPath (".staging-" + [System.Guid]::NewGuid().ToString('N'))

New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
New-Item -ItemType Directory -Path $stagePath | Out-Null

try {
    $trackedAndUntracked = & git -C $projectRoot ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the Git file list.' }

    $rootFiles = @('.dockerignore', '.env.example', 'compose.yaml', 'Dockerfile', 'README.md', 'requirements.txt')
    $selected = $trackedAndUntracked | Where-Object {
        $relative = $_ -replace '\\', '/'
        $topLevelAllowed = ($rootFiles -contains $relative) -or $relative -match '^[^/]+\.bat$'
        $directoryAllowed = $relative -match '^(backend|frontend|docs|scripts)/'
        $excluded = $relative -match '(^|/)(tests?|node_modules|dist|__pycache__|\.pytest_cache|\.vite|coverage)(/|$)' -or
            $relative -match '^(data|releases)/' -or $relative -match '\.test\.[^/]+$' -or
            $relative -match '\.(zip|pyc|pyo)$'
        ($topLevelAllowed -or $directoryAllowed) -and -not $excluded
    } | Sort-Object -Unique

    foreach ($relative in $selected) {
        $source = Join-Path $projectRoot $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
        $destination = Join-Path $stagePath $relative
        $destinationDirectory = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination
    }

    if ($IncludeSecrets) {
        $environmentPath = Join-Path $projectRoot '.env'
        if (-not (Test-Path -LiteralPath $environmentPath -PathType Leaf)) { throw 'Project .env was not found; cannot build a package with secrets.' }
        $environmentText = Get-Content -LiteralPath $environmentPath -Raw -Encoding UTF8
        $keyMatch = [regex]::Match($environmentText, '(?m)^\s*DEEPSEEK_API_KEY\s*=\s*([^\r\n]*)$')
        $keyValue = $keyMatch.Groups[1].Value.Trim().Trim('"').Trim("'")
        if (-not $keyMatch.Success -or [string]::IsNullOrWhiteSpace($keyValue)) { throw 'DEEPSEEK_API_KEY is empty; refusing to build a package with secrets.' }
        Copy-Item -LiteralPath $environmentPath -Destination (Join-Path $stagePath '.env')
    }

    $requiredFiles = @('compose.yaml', 'Dockerfile', 'README.md', 'frontend/package.json', 'backend/server.py', 'docs/deployment.md')
    foreach ($relative in $requiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $stagePath $relative) -PathType Leaf)) { throw "Required package file is missing: $relative" }
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stagePath,
        $archivePath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    $hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksumPath -Value "$hash *$archiveName" -Encoding ASCII
    Write-Host "Deployment package: $archivePath"
    Write-Host "SHA-256 file: $checksumPath"
    Write-Host ($(if ($IncludeSecrets) { 'Included .env; protect it as a plaintext secret file.' } else { 'No .env or live secret was included.' }))
}
finally {
    $resolvedStage = [System.IO.Path]::GetFullPath($stagePath)
    $outputPrefix = $outputPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($resolvedStage.StartsWith($outputPrefix, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.IO.Path]::GetFileName($resolvedStage).StartsWith('.staging-', [System.StringComparison]::Ordinal)) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
