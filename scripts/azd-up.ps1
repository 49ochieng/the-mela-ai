# =============================================================================
# scripts/azd-up.ps1 -- Mela AI one-command Azure deployment (Windows)
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#
# Usage (from repo root):
#   .\scripts\azd-up.ps1
#
# What it does:
#   1. az login + set subscription armely-isv
#   2. azd auth login
#   3. Create / select azd environment 'armely-dev'
#   4. Run preflight to detect existing resources
#   5. Push detected flags into azd environment (so Bicep receives them)
#   6. azd up  (provision infra + deploy both apps)
#
# Prerequisites:
#   - Azure CLI  : https://aka.ms/installazurecliwindows
#   - azd CLI    : https://aka.ms/azd
#   - Python 3.12+ in PATH  (for backend build)
#   - Node.js 20+ in PATH   (for frontend build)
#   - env/.env.local populated with Azure service keys
# =============================================================================
[CmdletBinding()]
param(
    [string]$EnvironmentName = '',
    [string]$ResourceGroup   = '',
    [string]$Location        = '',
    [string]$Subscription    = ''
)
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

# -- PS 5.1-compatible null-coalesce ------------------------------------------
function Get-EnvDefault {
    param([string]$Name, [string]$Default)
    $v = [System.Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($v)) { return $Default }
    return $v
}
function Coalesce { param($a, $b) if ([string]::IsNullOrWhiteSpace($a)) { $b } else { $a } }

$SubscriptionName = Coalesce $Subscription (Get-EnvDefault 'AZURE_SUBSCRIPTION_NAME' 'armely-isv')
$RG               = Coalesce $ResourceGroup (Get-EnvDefault 'RESOURCE_GROUP'          'rg-ai')
$Loc              = Coalesce $Location      (Get-EnvDefault 'AZURE_LOCATION'          'eastus2')
$EnvName          = Coalesce $EnvironmentName (Get-EnvDefault 'AZURE_ENV_NAME'        'armely-dev')

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir

# -- Console helpers ----------------------------------------------------------
function Write-Ok    { param($Msg) Write-Host "[OK]  $Msg" -ForegroundColor Green  }
function Write-Warn  { param($Msg) Write-Host "[!!]  $Msg" -ForegroundColor Yellow }
function Write-Err   { param($Msg) Write-Host "[ERR] $Msg" -ForegroundColor Red    }
function Write-Info  { param($Msg) Write-Host "[ - ] $Msg" -ForegroundColor Cyan   }
function Write-Step  { param($N, $T, $Msg) Write-Host "" ; Write-Host "  Step $N/$T : $Msg" -ForegroundColor White }

# -- Require tool -------------------------------------------------------------
function Require-Tool {
    param([string]$Cmd, [string]$InstallUrl)
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        Write-Err "$Cmd not found. Install from: $InstallUrl"
        exit 1
    }
}

Write-Host ""
Write-Host "================================================================"
Write-Host "  Mela AI -- Azure Deployment"
Write-Host "  Environment   : $EnvName"
Write-Host "  Subscription  : $SubscriptionName"
Write-Host "  Resource Group: $RG"
Write-Host "  Location      : $Loc"
Write-Host "================================================================"
Write-Host ""

Require-Tool 'az'  'https://aka.ms/installazurecliwindows'
Require-Tool 'azd' 'https://aka.ms/azd'

# -----------------------------------------------------------------------------
# Step 1: Azure CLI login
# -----------------------------------------------------------------------------
Write-Step 1 6 "Azure CLI login"

$null = az account show 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Info "Not logged in -- launching az login..."
    az login
    if ($LASTEXITCODE -ne 0) { Write-Err "az login failed."; exit 1 }
}

$null = az account set --subscription $SubscriptionName 2>$null
if ($LASTEXITCODE -ne 0) {
    $SubIdFallback = Get-EnvDefault 'AZURE_SUBSCRIPTION_ID' ''
    if ([string]::IsNullOrEmpty($SubIdFallback)) {
        Write-Err "Cannot resolve subscription '$SubscriptionName'. Set AZURE_SUBSCRIPTION_ID."
        exit 1
    }
    $null = az account set --subscription $SubIdFallback 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to set subscription."; exit 1 }
}
Write-Ok "Logged in to Azure -- subscription: $SubscriptionName"

# -----------------------------------------------------------------------------
# Step 2: azd login
# -----------------------------------------------------------------------------
Write-Step 2 6 "azd login"

$null = azd auth show 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Info "azd not authenticated -- launching azd auth login..."
    azd auth login
    if ($LASTEXITCODE -ne 0) { Write-Err "azd auth login failed."; exit 1 }
}
Write-Ok "Logged in to azd"

# -----------------------------------------------------------------------------
# Step 3: Create / select azd environment
# -----------------------------------------------------------------------------
Write-Step 3 6 "Configure azd environment '$EnvName'"

Push-Location $RepoRoot
try {
    # Check if environment already exists
    $existingEnvs = azd env list --output json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
    $envExists = $false
    if ($existingEnvs) {
        $envExists = ($existingEnvs | Where-Object { $_.Name -eq $EnvName }) -ne $null
    }

    if ($envExists) {
        azd env select $EnvName
        Write-Ok "azd environment selected: $EnvName"
    } else {
        # '--no-prompt' skips prompts in newer azd versions
        azd env new $EnvName --no-prompt 2>$null
        if ($LASTEXITCODE -ne 0) {
            # Older azd: try without --no-prompt
            azd env new $EnvName
        }
        Write-Ok "azd environment created: $EnvName"
    }

    # Set azd well-known variables
    azd env set AZURE_LOCATION       $Loc
    azd env set AZURE_RESOURCE_GROUP $RG
    Write-Ok "azd environment configured"

    # -------------------------------------------------------------------------
    # Step 4: Preflight -- detect existing resources
    # -------------------------------------------------------------------------
    Write-Step 4 6 "Preflight resource detection"

    $env:AZURE_CONFIG_DIR = '.azure'
    $env:AZURE_ENV_NAME   = $EnvName
    $env:RESOURCE_GROUP   = $RG
    $env:AZURE_LOCATION   = $Loc

    # Dot-source so env vars stay in this session
    . "$ScriptDir\preflight.ps1"

    Write-Ok "Preflight complete"

    # -------------------------------------------------------------------------
    # Step 5: Push preflight flags into the azd environment
    # azd reads its .env file when running provision, passing matching keys
    # directly to Bicep as parameter overrides via azure.yaml infra.parameters.
    # -------------------------------------------------------------------------
    Write-Step 5 6 "Pushing Bicep parameter flags into azd environment"

    # These names MUST match the Bicep parameter names in infra/main.bicep exactly.
    $bicepFlags = @{
        'useExistingAppServicePlan' = $env:USE_EXISTING_PLAN
        'useExistingBackendApp'     = $env:USE_EXISTING_BACKEND
        'useExistingFrontendApp'    = $env:USE_EXISTING_FRONTEND
        'useExistingKeyVault'       = $env:USE_EXISTING_KEYVAULT
        'useExistingAppInsights'    = $env:USE_EXISTING_APPINSIGHTS
        'useExistingStorage'        = $env:USE_EXISTING_STORAGE
    }

    foreach ($kv in $bicepFlags.GetEnumerator()) {
        $val = if ([string]::IsNullOrEmpty($kv.Value)) { 'false' } else { $kv.Value }
        azd env set $kv.Key $val
        Write-Info "  $($kv.Key) = $val"
    }

    Write-Ok "Bicep parameter flags set in azd environment"

    # -------------------------------------------------------------------------
    # Step 6: azd up (provision infrastructure + deploy both apps)
    # -------------------------------------------------------------------------
    Write-Step 6 6 "Running azd up (provision + deploy)"
    Write-Info "This will take several minutes on a fresh environment..."
    Write-Host ""

    azd up --no-prompt

    if ($LASTEXITCODE -ne 0) {
        Write-Err "azd up failed (exit code $LASTEXITCODE). Check the output above."
        exit $LASTEXITCODE
    }

    Write-Ok "azd up completed successfully"

} finally {
    Pop-Location
}

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
$BackendUrl  = "https://armely-ai-api.azurewebsites.net"
$FrontendUrl = "https://armely-ai-web.azurewebsites.net"

Write-Host ""
Write-Host "================================================================"
Write-Host "  Deployment complete!"
Write-Host ""
Write-Host "  Backend  : $BackendUrl"
Write-Host "  Frontend : $FrontendUrl"
Write-Host "  Health   : $BackendUrl/health"
Write-Host ""
Write-Host "  View logs:"
Write-Host "    az webapp log tail -n armely-ai-api -g $RG"
Write-Host "    az webapp log tail -n armely-ai-web -g $RG"
Write-Host ""
Write-Host "  Restart apps:"
Write-Host "    az webapp restart -n armely-ai-api -g $RG"
Write-Host "    az webapp restart -n armely-ai-web -g $RG"
Write-Host "================================================================"
Write-Host ""
