#!/usr/bin/env bash
# =============================================================================
# scripts/azd-up.sh — Mela AI one-command Azure deployment (Linux / macOS)
#
# Usage:
#   chmod +x scripts/azd-up.sh
#   ./scripts/azd-up.sh
#
# Prerequisites:
#   - az CLI installed and logged in (or let this script trigger login)
#   - azd (Azure Developer CLI) installed: https://aka.ms/azd
#   - env/.env.local populated with all secrets
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SUBSCRIPTION_NAME="${AZURE_SUBSCRIPTION_NAME:-armely-isv}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ai}"
LOCATION="${AZURE_LOCATION:-eastus2}"
ENVIRONMENT_NAME="${AZURE_ENV_NAME:-armely-dev}"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}[✓]${RESET} $*"; }
info() { echo -e "${CYAN}[-]${RESET} $*"; }
warn() { echo -e "${YELLOW}[!]${RESET} $*"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Mela AI — Azure Deployment"
echo "  Environment  : $ENVIRONMENT_NAME"
echo "  Subscription : $SUBSCRIPTION_NAME"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Location     : $LOCATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: Azure login ────────────────────────────────────────────────────────
info "Step 1/6: Azure login"
if ! az account show &>/dev/null; then
  az login
fi
az account set --subscription "$SUBSCRIPTION_NAME" || \
  az account set --subscription "${AZURE_SUBSCRIPTION_ID:-}"
ok "Logged in to Azure"

# ── Step 2: azd login ──────────────────────────────────────────────────────────
info "Step 2/6: azd login"
if ! azd auth show &>/dev/null; then
  azd auth login
fi
ok "Logged in to azd"

# ── Step 3: Create/select azd environment ────────────────────────────────────
info "Step 3/6: Configure azd environment '$ENVIRONMENT_NAME'"
cd "$REPO_ROOT"

if azd env list 2>/dev/null | grep -q "$ENVIRONMENT_NAME"; then
  azd env select "$ENVIRONMENT_NAME"
  ok "azd environment selected: $ENVIRONMENT_NAME"
else
  azd env new "$ENVIRONMENT_NAME"
  ok "azd environment created: $ENVIRONMENT_NAME"
fi

# Set azd environment values
azd env set AZURE_LOCATION "$LOCATION"
azd env set AZURE_RESOURCE_GROUP "$RESOURCE_GROUP"
azd env set AZURE_SUBSCRIPTION_NAME "$SUBSCRIPTION_NAME"
ok "azd environment configured"

# ── Step 4: Preflight check ──────────────────────────────────────────────────
info "Step 4/6: Running preflight resource check"
bash "$SCRIPT_DIR/preflight.sh"
ok "Preflight complete"

# Source preflight env to get USE_EXISTING_* flags
PREFLIGHT_ENV=".azure/${ENVIRONMENT_NAME}/preflight.env"
if [[ -f "$PREFLIGHT_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$PREFLIGHT_ENV"
fi

# ── Step 5: Provision infrastructure ─────────────────────────────────────────
info "Step 5/6: Provisioning infrastructure (azd provision)"
azd provision \
  --no-prompt \
  --parameters useExistingAppServicePlan="${USE_EXISTING_PLAN:-false}" \
  --parameters useExistingBackendApp="${USE_EXISTING_BACKEND:-false}" \
  --parameters useExistingFrontendApp="${USE_EXISTING_FRONTEND:-false}" \
  --parameters useExistingKeyVault="${USE_EXISTING_KEYVAULT:-false}" \
  --parameters useExistingAppInsights="${USE_EXISTING_APPINSIGHTS:-false}"
ok "Infrastructure provisioned"

# ── Step 6: Deploy application ────────────────────────────────────────────────
info "Step 6/6: Deploying application (azd deploy)"
azd deploy --no-prompt
ok "Application deployed"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Deployment complete!"
echo ""
BACKEND_URL="https://armely-ai-api.azurewebsites.net"
FRONTEND_URL="https://armely-ai-web.azurewebsites.net"
echo "  Backend  : $BACKEND_URL"
echo "  Frontend : $FRONTEND_URL"
echo "  Health   : $BACKEND_URL/health"
echo ""
echo "  View logs:"
echo "    az webapp log tail -n armely-ai-api  -g $RESOURCE_GROUP"
echo "    az webapp log tail -n armely-ai-web  -g $RESOURCE_GROUP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
