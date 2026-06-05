#!/usr/bin/env bash
# =============================================================================
# scripts/preflight.sh — Mela AI Azure Preflight Check
# Detects existing Azure resources and emits flags consumed by Bicep + azd.
# Run before every `azd provision` and `azd deploy`.
# =============================================================================
set -euo pipefail

# ── Load naming defaults (override via env vars) ───────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMING_FILE="$SCRIPT_DIR/naming.json"

SUBSCRIPTION_NAME="${AZURE_SUBSCRIPTION_NAME:-armely-isv}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ai}"
LOCATION="${AZURE_LOCATION:-eastus2}"
ENVIRONMENT_NAME="${AZURE_ENV_NAME:-armely-dev}"

APP_SERVICE_PLAN_NAME="${APP_SERVICE_PLAN_NAME:-asp-armely-ai}"
BACKEND_APP_NAME="${BACKEND_APP_NAME:-armely-ai-api}"
FRONTEND_APP_NAME="${FRONTEND_APP_NAME:-armely-ai-web}"
KEY_VAULT_NAME="${KEY_VAULT_NAME:-kv-mela-mcpp}"
APP_INSIGHTS_NAME="${APP_INSIGHTS_NAME:-ai-armely-ai}"
LOG_ANALYTICS_NAME="${LOG_ANALYTICS_NAME:-log-armely-ai}"
STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME:-starmelyai}"

PREFLIGHT_OUT_DIR="${PREFLIGHT_BASE_DIR:-.azure}/${ENVIRONMENT_NAME}"
PREFLIGHT_JSON="$PREFLIGHT_OUT_DIR/preflight.json"

# ── Colours ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }
err()  { echo -e "${RED}[✗]${RESET} $*"; }
info() { echo -e "${CYAN}[-]${RESET} $*"; }

# ── Ensure az CLI is available ─────────────────────────────────────────────────
if ! command -v az &>/dev/null; then
  err "Azure CLI not found. Install from https://aka.ms/installazureclilinux"
  exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Mela AI — Azure Preflight Check"
echo "  Subscription : $SUBSCRIPTION_NAME"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Location     : $LOCATION"
echo "  Environment  : $ENVIRONMENT_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Azure login check ──────────────────────────────────────────────────────────
info "Checking Azure login..."
if ! az account show &>/dev/null; then
  warn "Not logged in. Running az login..."
  az login --output none
fi
ok "Azure login confirmed"

# ── Set subscription ───────────────────────────────────────────────────────────
info "Setting subscription to: $SUBSCRIPTION_NAME"
if az account set --subscription "$SUBSCRIPTION_NAME" 2>/dev/null; then
  ok "Subscription set: $SUBSCRIPTION_NAME"
else
  warn "Subscription name lookup failed — trying as subscription ID..."
  SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
  if [[ -z "$SUBSCRIPTION_ID" ]]; then
    err "Cannot resolve subscription '$SUBSCRIPTION_NAME'. Set AZURE_SUBSCRIPTION_ID."
    exit 1
  fi
  az account set --subscription "$SUBSCRIPTION_ID"
  ok "Subscription set by ID"
fi

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
info "Subscription ID : $SUBSCRIPTION_ID"
info "Tenant ID       : $TENANT_ID"

# ── Ensure resource group exists ───────────────────────────────────────────────
info "Checking resource group: $RESOURCE_GROUP"
if az group show --name "$RESOURCE_GROUP" &>/dev/null; then
  ok "Resource group exists: $RESOURCE_GROUP"
  RG_EXISTING=true
else
  warn "Resource group '$RESOURCE_GROUP' not found — creating..."
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
  ok "Created resource group: $RESOURCE_GROUP"
  RG_EXISTING=false
fi

# ── Helper: check if a resource exists ────────────────────────────────────────
resource_exists() {
  local type="$1"
  local name="$2"
  az resource list \
    --resource-group "$RESOURCE_GROUP" \
    --resource-type "$type" \
    --name "$name" \
    --query "[0].id" -o tsv 2>/dev/null | grep -q "."
}

# ── Check each required resource ──────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Resource Detection"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

check_resource() {
  local label="$1"
  local type="$2"
  local name="$3"
  local var_name="$4"

  if resource_exists "$type" "$name"; then
    ok "FOUND   — $label ($name)"
    eval "$var_name=true"
  else
    warn "MISSING — $label ($name) — will be created"
    eval "$var_name=false"
  fi
}

check_resource "App Service Plan" "Microsoft.Web/serverfarms"                       "$APP_SERVICE_PLAN_NAME"  USE_EXISTING_PLAN
check_resource "Backend Web App"  "Microsoft.Web/sites"                             "$BACKEND_APP_NAME"       USE_EXISTING_BACKEND
check_resource "Frontend Web App" "Microsoft.Web/sites"                             "$FRONTEND_APP_NAME"      USE_EXISTING_FRONTEND
check_resource "Key Vault"        "Microsoft.KeyVault/vaults"                       "$KEY_VAULT_NAME"         USE_EXISTING_KEYVAULT
check_resource "App Insights"     "Microsoft.Insights/components"                   "$APP_INSIGHTS_NAME"      USE_EXISTING_APPINSIGHTS
check_resource "Log Analytics"    "Microsoft.OperationalInsights/workspaces"        "$LOG_ANALYTICS_NAME"     USE_EXISTING_LOGANALYTICS
check_resource "Storage Account"  "Microsoft.Storage/storageAccounts"               "$STORAGE_ACCOUNT_NAME"   USE_EXISTING_STORAGE

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  useExistingAppServicePlan : $USE_EXISTING_PLAN"
echo "  useExistingBackendApp     : $USE_EXISTING_BACKEND"
echo "  useExistingFrontendApp    : $USE_EXISTING_FRONTEND"
echo "  useExistingKeyVault       : $USE_EXISTING_KEYVAULT"
echo "  useExistingAppInsights    : $USE_EXISTING_APPINSIGHTS"
echo "  useExistingStorage        : $USE_EXISTING_STORAGE"
echo ""

# ── Write machine-readable output ─────────────────────────────────────────────
mkdir -p "$PREFLIGHT_OUT_DIR"

cat > "$PREFLIGHT_JSON" <<JSON
{
  "subscriptionId": "$SUBSCRIPTION_ID",
  "tenantId": "$TENANT_ID",
  "resourceGroup": "$RESOURCE_GROUP",
  "location": "$LOCATION",
  "environmentName": "$ENVIRONMENT_NAME",
  "resources": {
    "appServicePlan": { "name": "$APP_SERVICE_PLAN_NAME", "exists": $USE_EXISTING_PLAN },
    "backendApp":     { "name": "$BACKEND_APP_NAME",      "exists": $USE_EXISTING_BACKEND },
    "frontendApp":    { "name": "$FRONTEND_APP_NAME",     "exists": $USE_EXISTING_FRONTEND },
    "keyVault":       { "name": "$KEY_VAULT_NAME",        "exists": $USE_EXISTING_KEYVAULT },
    "appInsights":    { "name": "$APP_INSIGHTS_NAME",     "exists": $USE_EXISTING_APPINSIGHTS },
    "logAnalytics":   { "name": "$LOG_ANALYTICS_NAME",    "exists": $USE_EXISTING_LOGANALYTICS },
    "storage":        { "name": "$STORAGE_ACCOUNT_NAME",  "exists": $USE_EXISTING_STORAGE }
  }
}
JSON

ok "Preflight output written: $PREFLIGHT_JSON"

# ── Export environment variables for Bicep / azd ──────────────────────────────
export AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
export AZURE_TENANT_ID="$TENANT_ID"
export RESOURCE_GROUP="$RESOURCE_GROUP"
export AZURE_LOCATION="$LOCATION"
export USE_EXISTING_PLAN="$USE_EXISTING_PLAN"
export USE_EXISTING_BACKEND="$USE_EXISTING_BACKEND"
export USE_EXISTING_FRONTEND="$USE_EXISTING_FRONTEND"
export USE_EXISTING_KEYVAULT="$USE_EXISTING_KEYVAULT"
export USE_EXISTING_APPINSIGHTS="$USE_EXISTING_APPINSIGHTS"
export USE_EXISTING_STORAGE="$USE_EXISTING_STORAGE"

# Make it source-able for CI
cat > "$PREFLIGHT_OUT_DIR/preflight.env" <<ENV
AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
AZURE_TENANT_ID=$TENANT_ID
RESOURCE_GROUP=$RESOURCE_GROUP
AZURE_LOCATION=$LOCATION
USE_EXISTING_PLAN=$USE_EXISTING_PLAN
USE_EXISTING_BACKEND=$USE_EXISTING_BACKEND
USE_EXISTING_FRONTEND=$USE_EXISTING_FRONTEND
USE_EXISTING_KEYVAULT=$USE_EXISTING_KEYVAULT
USE_EXISTING_APPINSIGHTS=$USE_EXISTING_APPINSIGHTS
USE_EXISTING_STORAGE=$USE_EXISTING_STORAGE
ENV

ok "Preflight env file: $PREFLIGHT_OUT_DIR/preflight.env"
echo ""
ok "Preflight complete. All required resources verified."
echo ""
