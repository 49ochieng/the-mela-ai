# Mela AI — Deployment Validation Checklist

Use this checklist after each deployment to verify a healthy state.

---

## Infrastructure

- [ ] Resource group `rg-ai` exists in subscription `armely-isv`
- [ ] App Service Plan `asp-armely-ai` exists (Linux, B1+)
- [ ] Backend Web App `armely-ai-api` exists and is running
- [ ] Frontend Web App `armely-ai-web` exists and is running
- [ ] Key Vault `kv-armely-ai` exists with RBAC authorization enabled
- [ ] App Insights `ai-armely-ai` exists and is collecting data
- [ ] Log Analytics workspace `log-armely-ai` exists

## Identity & Access

- [ ] Backend Web App has system-assigned managed identity enabled
- [ ] Frontend Web App has system-assigned managed identity enabled
- [ ] Backend managed identity has `Key Vault Secrets User` role on `kv-armely-ai`
- [ ] CD service principal has `Key Vault Secrets Officer` role on `kv-armely-ai`
- [ ] CD service principal has `Contributor` role on `rg-ai`
- [ ] CD service principal has `User Access Administrator` role on `rg-ai`

## Key Vault Secrets

Run to list all secrets (values not shown):
```bash
az keyvault secret list --vault-name kv-armely-ai --query "[].name" -o tsv
```

Expected secrets:
- [ ] `jwt-secret-key`
- [ ] `azure-client-secret`
- [ ] `ai-foundry-api-key`
- [ ] `azure-openai-api-key`
- [ ] `azure-speech-key`
- [ ] `azure-dalle-api-key`
- [ ] `azure-search-admin-key`
- [ ] `database-url`

## App Settings (Key Vault References)

Verify App Settings on backend app reference KV correctly:
```bash
az webapp config appsettings list \
  --name armely-ai-api \
  --resource-group rg-ai \
  --query "[?contains(value, '@Microsoft.KeyVault')].[name, value]" \
  -o table
```

Expected KV-referenced settings:
- [ ] `JWT_SECRET_KEY`
- [ ] `AZURE_CLIENT_SECRET`
- [ ] `AI_FOUNDRY_API_KEY`
- [ ] `DATABASE_URL`

## Health Check

```bash
curl -s https://armely-ai-api.azurewebsites.net/health | python3 -m json.tool
```

Expected response (HTTP 200):
```json
{
  "status": "healthy",
  "app": "Mela AI",
  "version": "1.0.0",
  "environment": "development"
}
```

- [ ] `/health` returns HTTP 200
- [ ] `status` field is `"healthy"`

## Frontend

```bash
curl -o /dev/null -s -w "%{http_code}" https://armely-ai-web.azurewebsites.net/
```

- [ ] Frontend returns HTTP 200
- [ ] Redirects to login page when unauthenticated

## CI/CD

- [ ] GitHub repository has `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` as **Variables**
- [ ] GitHub repository has all API keys as **Secrets**
- [ ] GitHub environment `dev` exists
- [ ] CD workflow (`cd.yml`) succeeded on last push to `main`
- [ ] Deployed commit SHA matches `github.sha` in last workflow run
- [ ] No secrets echoed in workflow logs

## Authentication

- [ ] Entra app registration `Meeting-Intelligence-Bot` has `access_as_user` scope exposed
- [ ] `api://7ed650f2-28d9-4c03-b660-2fe0bbb98434` is the Application ID URI
- [ ] Redirect URI `https://armely-ai-web.azurewebsites.net` is registered
- [ ] MSAL login succeeds in browser and redirects to `/chat`

## Troubleshooting Quick Reference

| Symptom | Check |
|---------|-------|
| 500 from backend | `az webapp log tail -n armely-ai-api -g rg-ai` |
| Blank frontend | `az webapp log tail -n armely-ai-web -g rg-ai` |
| KV secret not found | Check managed identity RBAC assignment |
| CORS errors | Verify `CORS_ORIGINS` App Setting on backend |
| MSAL 401 | Verify audience in `security.py` and Entra scope |
| Cold start timeout | Increase health check path timeout or upgrade to P1v3 |
