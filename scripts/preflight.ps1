# =============================================================================
# scripts/preflight.ps1 -- Mela AI Azure Preflight Check
# Detects existing Azure resources and emits flags consumed by Bicep / azd.
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#
# Usage:
#   .\scripts\preflight.ps1
#   # or dot-source to keep env vars in the current session:
#   . .\scripts\preflight.ps1
# =============================================================================
[CmdletBinding()]
param()

# Never auto-stop on external-process failures; we check $LASTEXITCODE manually.
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'   # suppress az progress bars

# -- PS 5.1-compatible null-coalesce helper -----------------------------------
function Get-EnvDefault {
    param([string]$Name, [string]$Default)
    $v = [System.Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($v)) { return $Default }
    return $v
}

# -- Load naming defaults (override via env vars) -----------------------------
$SubscriptionName   = Get-EnvDefault 'AZURE_SUBSCRIPTION_NAME'  'armely-isv'
$ResourceGroup      = Get-EnvDefault 'RESOURCE_GROUP'           'rg-ai'
$Location           = Get-EnvDefault 'AZURE_LOCATION'           'eastus2'
$EnvironmentName    = Get-EnvDefault 'AZURE_ENV_NAME'           'armely-dev'
$AppServicePlanName = Get-EnvDefault 'APP_SERVICE_PLAN_NAME'    'asp-armely-ai'
$BackendAppName     = Get-EnvDefault 'BACKEND_APP_NAME'         'armely-ai-api'
$FrontendAppName    = Get-EnvDefault 'FRONTEND_APP_NAME'        'armely-ai-web'
$KeyVaultName       = Get-EnvDefault 'KEY_VAULT_NAME'           'kv-mela-mcpp'
$AppInsightsName    = Get-EnvDefault 'APP_INSIGHTS_NAME'        'ai-armely-ai'
$LogAnalyticsName   = Get-EnvDefault 'LOG_ANALYTICS_NAME'       'log-armely-ai'
$StorageAccountName = Get-EnvDefault 'STORAGE_ACCOUNT_NAME'     'starmelyai'
$AzureConfigDir     = Get-EnvDefault 'PREFLIGHT_BASE_DIR'       '.azure'

$PreflightOutDir = Join-Path $AzureConfigDir $EnvironmentName
$PreflightJson   = Join-Path $PreflightOutDir 'preflight.json'
$PreflightEnv    = Join-Path $PreflightOutDir 'preflight.env'

# -- Console helpers ----------------------------------------------------------
function Write-Ok   { param($Msg) Write-Host "[OK]  $Msg" -ForegroundColor Green  }
function Write-Warn { param($Msg) Write-Host "[!!]  $Msg" -ForegroundColor Yellow }
function Write-Err  { param($Msg) Write-Host "[ERR] $Msg" -ForegroundColor Red    }
function Write-Info { param($Msg) Write-Host "[ - ] $Msg" -ForegroundColor Cyan   }

# -- Banner -------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================"
Write-Host "  Mela AI -- Azure Preflight Check"
Write-Host "  Subscription : $SubscriptionName"
Write-Host "  Resource Group: $ResourceGroup"
Write-Host "  Location     : $Location"
Write-Host "  Environment  : $EnvironmentName"
Write-Host "================================================================"
Write-Host ""

# -- Ensure az CLI ------------------------------------------------------------
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Err "Azure CLI not found. Install from: https://aka.ms/installazurecliwindows"
    exit 1
}

# -- Azure login check --------------------------------------------------------
Write-Info "Checking Azure login..."
$null = az account show 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Not logged in -- launching az login..."
    az login --output none
    if ($LASTEXITCODE -ne 0) {
        Write-Err "az login failed. Aborting."
        exit 1
    }
}
Write-Ok "Azure login confirmed"

# -- Set subscription ---------------------------------------------------------
Write-Info "Setting subscription: $SubscriptionName"
$null = az account set --subscription $SubscriptionName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Subscription name lookup failed -- trying AZURE_SUBSCRIPTION_ID..."
    $SubIdOverride = Get-EnvDefault 'AZURE_SUBSCRIPTION_ID' ''
    if ([string]::IsNullOrEmpty($SubIdOverride)) {
        Write-Err "Cannot resolve subscription '$SubscriptionName'. Set AZURE_SUBSCRIPTION_ID env var."
        exit 1
    }
    $null = az account set --subscription $SubIdOverride 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to set subscription by ID. Check your Azure access."
        exit 1
    }
}
Write-Ok "Subscription set"

$SubscriptionId = (az account show --query id       --output tsv 2>$null).Trim()
$TenantId       = (az account show --query tenantId --output tsv 2>$null).Trim()
Write-Info "Subscription ID : $SubscriptionId"
Write-Info "Tenant ID       : $TenantId"

# -- Ensure resource group exists ---------------------------------------------
Write-Info "Checking resource group: $ResourceGroup"
$null = az group show --name $ResourceGroup 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Resource group '$ResourceGroup' not found -- creating in $Location..."
    az group create --name $ResourceGroup --location $Location --output none
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to create resource group '$ResourceGroup'."
        exit 1
    }
    Write-Ok "Created resource group: $ResourceGroup"
} else {
    Write-Ok "Resource group exists: $ResourceGroup"
}

# -- Helper: check if a named resource exists in the resource group -----------
function Test-ResourceExists {
    param([string]$ResourceType, [string]$Name)
    $id = az resource list `
        --resource-group $ResourceGroup `
        --resource-type  $ResourceType `
        --name           $Name `
        --query          "[0].id" `
        --output         tsv 2>$null
    return (-not [string]::IsNullOrWhiteSpace($id))
}

# -- Detect each required resource --------------------------------------------
Write-Host ""
Write-Host "================================================================"
Write-Host "  Resource Detection"
Write-Host "================================================================"

$detected = @{}

function Invoke-ResourceCheck {
    param([string]$Label, [string]$RType, [string]$Name, [string]$Key)
    if (Test-ResourceExists -ResourceType $RType -Name $Name) {
        Write-Ok   "FOUND    $Label ($Name)"
        $script:detected[$Key] = $true
    } else {
        Write-Warn "MISSING  $Label ($Name)  -- will be created"
        $script:detected[$Key] = $false
    }
}

Invoke-ResourceCheck "App Service Plan" "Microsoft.Web/serverfarms"                $AppServicePlanName "useExistingAppServicePlan"
Invoke-ResourceCheck "Backend Web App"  "Microsoft.Web/sites"                      $BackendAppName     "useExistingBackendApp"
Invoke-ResourceCheck "Frontend Web App" "Microsoft.Web/sites"                      $FrontendAppName    "useExistingFrontendApp"
Invoke-ResourceCheck "Key Vault"        "Microsoft.KeyVault/vaults"                $KeyVaultName       "useExistingKeyVault"
Invoke-ResourceCheck "App Insights"     "Microsoft.Insights/components"            $AppInsightsName    "useExistingAppInsights"
Invoke-ResourceCheck "Log Analytics"    "Microsoft.OperationalInsights/workspaces" $LogAnalyticsName   "useExistingLogAnalytics"
Invoke-ResourceCheck "Storage Account"  "Microsoft.Storage/storageAccounts"        $StorageAccountName "useExistingStorage"

# -- Summary ------------------------------------------------------------------
Write-Host ""
Write-Host "================================================================"
Write-Host "  Summary"
Write-Host "================================================================"
foreach ($key in ($detected.Keys | Sort-Object)) {
    $flag = if ($detected[$key]) { 'true (reuse)' } else { 'false (create)' }
    Write-Host ("  {0,-35} {1}" -f $key, $flag)
}
Write-Host ""

# -- Write output files -------------------------------------------------------
$null = New-Item -ItemType Directory -Force -Path $PreflightOutDir

# JSON -- consumed by scripts and CI
$jsonObj = [ordered]@{
    subscriptionId  = $SubscriptionId
    tenantId        = $TenantId
    resourceGroup   = $ResourceGroup
    location        = $Location
    environmentName = $EnvironmentName
    resources = [ordered]@{
        appServicePlan = [ordered]@{ name = $AppServicePlanName;  exists = $detected["useExistingAppServicePlan"] }
        backendApp     = [ordered]@{ name = $BackendAppName;      exists = $detected["useExistingBackendApp"]     }
        frontendApp    = [ordered]@{ name = $FrontendAppName;     exists = $detected["useExistingFrontendApp"]    }
        keyVault       = [ordered]@{ name = $KeyVaultName;        exists = $detected["useExistingKeyVault"]       }
        appInsights    = [ordered]@{ name = $AppInsightsName;     exists = $detected["useExistingAppInsights"]    }
        logAnalytics   = [ordered]@{ name = $LogAnalyticsName;    exists = $detected["useExistingLogAnalytics"]   }
        storage        = [ordered]@{ name = $StorageAccountName;  exists = $detected["useExistingStorage"]        }
    }
}
$jsonObj | ConvertTo-Json -Depth 5 | Set-Content -Path $PreflightJson -Encoding UTF8
Write-Ok "Preflight JSON  : $PreflightJson"

# .env -- sourced by bash CI steps; read by azd-up.ps1
$lines = @(
    "AZURE_SUBSCRIPTION_ID=$SubscriptionId"
    "AZURE_TENANT_ID=$TenantId"
    "RESOURCE_GROUP=$ResourceGroup"
    "AZURE_LOCATION=$Location"
    "USE_EXISTING_PLAN=$($detected['useExistingAppServicePlan'].ToString().ToLower())"
    "USE_EXISTING_BACKEND=$($detected['useExistingBackendApp'].ToString().ToLower())"
    "USE_EXISTING_FRONTEND=$($detected['useExistingFrontendApp'].ToString().ToLower())"
    "USE_EXISTING_KEYVAULT=$($detected['useExistingKeyVault'].ToString().ToLower())"
    "USE_EXISTING_APPINSIGHTS=$($detected['useExistingAppInsights'].ToString().ToLower())"
    "USE_EXISTING_STORAGE=$($detected['useExistingStorage'].ToString().ToLower())"
    "useExistingAppServicePlan=$($detected['useExistingAppServicePlan'].ToString().ToLower())"
    "useExistingBackendApp=$($detected['useExistingBackendApp'].ToString().ToLower())"
    "useExistingFrontendApp=$($detected['useExistingFrontendApp'].ToString().ToLower())"
    "useExistingKeyVault=$($detected['useExistingKeyVault'].ToString().ToLower())"
    "useExistingAppInsights=$($detected['useExistingAppInsights'].ToString().ToLower())"
)
$lines | Set-Content -Path $PreflightEnv -Encoding UTF8
Write-Ok "Preflight env   : $PreflightEnv"

# Export to current PowerShell session so callers can read $env:USE_EXISTING_*
$env:AZURE_SUBSCRIPTION_ID        = $SubscriptionId
$env:AZURE_TENANT_ID              = $TenantId
$env:RESOURCE_GROUP               = $ResourceGroup
$env:AZURE_LOCATION               = $Location
$env:USE_EXISTING_PLAN            = $detected["useExistingAppServicePlan"].ToString().ToLower()
$env:USE_EXISTING_BACKEND         = $detected["useExistingBackendApp"].ToString().ToLower()
$env:USE_EXISTING_FRONTEND        = $detected["useExistingFrontendApp"].ToString().ToLower()
$env:USE_EXISTING_KEYVAULT        = $detected["useExistingKeyVault"].ToString().ToLower()
$env:USE_EXISTING_APPINSIGHTS     = $detected["useExistingAppInsights"].ToString().ToLower()
$env:USE_EXISTING_STORAGE         = $detected["useExistingStorage"].ToString().ToLower()
# camelCase copies for azd env set (Bicep parameter names)
$env:useExistingAppServicePlan    = $env:USE_EXISTING_PLAN
$env:useExistingBackendApp        = $env:USE_EXISTING_BACKEND
$env:useExistingFrontendApp       = $env:USE_EXISTING_FRONTEND
$env:useExistingKeyVault          = $env:USE_EXISTING_KEYVAULT
$env:useExistingAppInsights       = $env:USE_EXISTING_APPINSIGHTS

Write-Host ""
Write-Ok "Preflight complete."
Write-Host ""
