# One-Time Azure Setup Guide

This guide walks through the one-time Azure and GitHub configuration required before the first deployment.

---

## 1. Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Azure CLI | ≥ 2.60 | https://aka.ms/installazurecli |
| Azure Developer CLI (azd) | ≥ 1.9 | https://aka.ms/azd |
| Node.js | 20 LTS | https://nodejs.org |
| Python | 3.12 | https://python.org |

---

## 2. Azure App Registration (Entra ID)

The application uses the existing **Meeting-Intelligence-Bot** registration.

- **Tenant ID**: `588cadf4-9902-4465-86c0-8bcf04f4f102`
- **Client ID**: `7ed650f2-28d9-4c03-b660-2fe0bbb98434`

### 2a. Expose the API scope (required once)

In Azure Portal → App registrations → Meeting-Intelligence-Bot:

1. **Expose an API** → Set Application ID URI:
   ```
   api://7ed650f2-28d9-4c03-b660-2fe0bbb98434
   ```
2. Add scope: `access_as_user`
   - Admins and users: **Enabled**
   - Display name: `Access Mela AI as user`

### 2b. Add redirect URIs

**Authentication** → Add platform → Single-page application:
- `http://localhost:3000`
- `https://armely-ai-web.azurewebsites.net`

---

## 3. CI/CD Service Principal (GitHub OIDC)

Create a dedicated service principal for GitHub Actions. This uses federated credentials — **no client secret required**.

### Step 1: Create the service principal

```bash
# Login first
az login
az account set --subscription armely-isv

# Create the service principal
az ad sp create-for-rbac \
  --name "github-mela-ai-deploy" \
  --role Contributor \
  --scopes /subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-ai \
  --output json
```

Note the `appId` (client ID) and `tenant` from the output. **Do not use the password** — we'll replace it with OIDC.

### Step 2: Grant User Access Administrator (needed for RBAC role assignments in Bicep)

```bash
SP_OBJECT_ID=$(az ad sp show --id <appId-from-above> --query id -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

az role assignment create \
  --assignee "$SP_OBJECT_ID" \
  --role "User Access Administrator" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/rg-ai"
```

### Step 3: Add federated credentials for GitHub OIDC

Replace `YOUR_GITHUB_ORG` and `YOUR_REPO_NAME` with your actual GitHub org/user and repository name.

```bash
APP_ID=<appId-from-step-1>

# Credential for pushes to main
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:YOUR_GITHUB_ORG/YOUR_REPO_NAME:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# Credential for workflow_dispatch
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters '{
    "name": "github-environment-dev",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:YOUR_GITHUB_ORG/YOUR_REPO_NAME:environment:dev",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

---

## 4. GitHub Repository Settings

### Repository Variables (Settings → Secrets and variables → Actions → Variables)

These are non-secret configuration values — safe to store as variables:

| Variable | Value |
|----------|-------|
| `AZURE_CLIENT_ID` | `<appId of github-mela-ai-deploy SP>` |
| `AZURE_TENANT_ID` | `588cadf4-9902-4465-86c0-8bcf04f4f102` |
| `AZURE_SUBSCRIPTION_ID` | `<your Azure subscription ID>` |
| `AZURE_AD_CLIENT_ID` | `7ed650f2-28d9-4c03-b660-2fe0bbb98434` |
| `NEXT_PUBLIC_API_SCOPE` | `api://7ed650f2-28d9-4c03-b660-2fe0bbb98434/access_as_user` |
| `AI_FOUNDRY_ENDPOINT` | `https://AI-FOUNDRY-MAIN-001.cognitiveservices.azure.com` |
| `AZURE_OPENAI_ENDPOINT` | `https://AI-FOUNDRY-MAIN-001.cognitiveservices.azure.com` |
| `AZURE_SPEECH_REGION` | `eastus2` |
| `AZURE_DALLE_ENDPOINT` | *(your DALL-E endpoint)* |
| `AZURE_SEARCH_ENDPOINT` | *(your AI Search endpoint, if used)* |

### Repository Secrets (Settings → Secrets and variables → Actions → Secrets)

These are encrypted and never echoed in logs:

| Secret | Purpose |
|--------|---------|
| `JWT_SECRET_KEY` | JWT signing key — generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `AZURE_CLIENT_SECRET` | Entra app client secret (rotate via Azure portal; never paste the value into docs or code) |
| `AI_FOUNDRY_API_KEY` | Azure AI Foundry API key |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key (same as AI Foundry if shared) |
| `AZURE_SPEECH_KEY` | Azure Speech service key |
| `AZURE_DALLE_API_KEY` | DALL-E API key |
| `AZURE_SEARCH_ADMIN_KEY` | AI Search admin key |
| `AZURE_STORAGE_ACCOUNT_KEY` | Storage account key (if blob storage used) |
| `DATABASE_URL` | Azure SQL connection string (prod database) |

### GitHub Environment

Create a GitHub environment named **dev** (Settings → Environments → New environment):
- No approval gates needed for dev
- The CD workflow uses `environment: dev` which maps to this

---

## 5. Populate Key Vault Secrets (after first Bicep deploy)

After `infra/main.bicep` provisions the Key Vault, write secrets from your local env:

```bash
# Load your local secrets
source env/.env.local

KV=kv-armely-ai
az keyvault secret set --vault-name $KV --name jwt-secret-key         --value "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
az keyvault secret set --vault-name $KV --name azure-client-secret    --value "$AZURE_CLIENT_SECRET"
az keyvault secret set --vault-name $KV --name ai-foundry-api-key     --value "$AI_FOUNDRY_API_KEY"
az keyvault secret set --vault-name $KV --name azure-openai-api-key   --value "$AZURE_OPENAI_API_KEY"
az keyvault secret set --vault-name $KV --name azure-speech-key       --value "$AZURE_SPEECH_KEY"
az keyvault secret set --vault-name $KV --name azure-dalle-api-key    --value "$AZURE_DALLE_API_KEY"
az keyvault secret set --vault-name $KV --name azure-search-admin-key --value "$AZURE_SEARCH_ADMIN_KEY"
az keyvault secret set --vault-name $KV --name database-url           --value "$DATABASE_URL"
```

---

## 6. First Deployment

After completing steps 1–5, run the one-command deployment:

**Linux / macOS:**
```bash
chmod +x scripts/azd-up.sh
./scripts/azd-up.sh
```

**Windows:**
```powershell
.\scripts\azd-up.ps1
```

Or directly with azd:
```bash
az login
az account set --subscription armely-isv
azd env new armely-dev
azd env set AZURE_LOCATION eastus2
azd env set AZURE_RESOURCE_GROUP rg-ai
azd up
```
