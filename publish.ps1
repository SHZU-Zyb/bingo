param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,

    [switch]$SkipTests,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $repoRoot

function Assert-LastCommandSucceeded {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath ".git")) {
    throw "publish.ps1 must be run from a Git repository."
}

$branch = (& git branch --show-current).Trim()
Assert-LastCommandSucceeded "Reading the current branch"
if (-not $branch) {
    throw "The repository is in detached HEAD state; switch to a branch before publishing."
}

$remote = (& git remote get-url origin).Trim()
Assert-LastCommandSucceeded "Reading the origin remote"

if (Test-Path -LiteralPath ".env") {
    & git check-ignore -q -- .env
    if ($LASTEXITCODE -ne 0) {
        throw ".env is not ignored. Add it to .gitignore before publishing."
    }
}

$sensitivePatterns = @(
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "credentials.json",
    "secrets.json",
    "secrets.*.json",
    ".npmrc",
    ".pypirc"
)
$trackedSensitive = @(& git ls-files -- $sensitivePatterns) |
    Where-Object { $_ -and $_ -ne ".env.example" }
Assert-LastCommandSucceeded "Checking tracked sensitive files"
if ($trackedSensitive.Count -gt 0) {
    throw "Refusing to publish tracked sensitive files: $($trackedSensitive -join ', ')"
}

$changes = @(& git status --porcelain)
Assert-LastCommandSucceeded "Reading repository status"
if ($changes.Count -eq 0) {
    Write-Host "No changes to publish."
    exit 0
}

Write-Host "Repository: $remote"
Write-Host "Branch:     $branch"
Write-Host "Changes:"
& git status --short
Assert-LastCommandSucceeded "Listing changes"

if ($DryRun) {
    Write-Host "Dry run complete; no files were staged, committed, or pushed."
    exit 0
}

if (-not $SkipTests) {
    Write-Host "Running the full test suite with warnings treated as errors..."
    & python -m pytest -q -W error
    Assert-LastCommandSucceeded "Tests"
}

& git add --all
Assert-LastCommandSucceeded "Staging changes"

$staged = @(& git diff --cached --name-only)
Assert-LastCommandSucceeded "Reading staged changes"
if ($staged.Count -eq 0) {
    Write-Host "No staged changes to publish."
    exit 0
}

& git diff --cached --check
Assert-LastCommandSucceeded "Checking staged diff"

& git commit -m $Message
Assert-LastCommandSucceeded "Creating commit"

& git push -u origin $branch
Assert-LastCommandSucceeded "Pushing to GitHub"

Write-Host "Published successfully: $remote ($branch)"
