# Mela Task Radar — Azure infrastructure

This folder provisions the production Azure footprint with a single Bicep template.

## Deploy

```powershell
az login
az group create -n rg-task-radar -l eastus

az deployment group create `
  -g rg-task-radar `
  -f infra/azure/main.bicep `
  -p namePrefix=mtr `
     sqlAdminLogin=mtradmin `
     sqlAdminPassword='YourStrongPassword!1'
```

## Resources created

| Resource | Purpose |
|---|---|
| App Service Plan (Linux, P1v3) | Hosts API / Worker / Scheduler / MCP |
| Web App `mtr-api` | FastAPI backend (Gunicorn + Uvicorn) |
| Web App `mtr-worker` | Background scan worker |
| Web App `mtr-sched` | Daily scheduler (APScheduler) |
| Web App `mtr-mcp` | MCP server for Mela AI |
| Azure SQL Server + DB | Tenant-scoped relational store |
| Storage Account | Attachment archive (Blob) |
| Service Bus Namespace + `scan-jobs` queue | Durable job dispatch |
| Key Vault | Holds `FERNET_KEY`, OpenAI key, MSAL secrets |
| App Insights + Log Analytics | Telemetry & logs |

## Post-deploy steps

1. Push container images (or zip-deploy code) for each Web App from CI.
2. Set required app settings on each Web App via `az webapp config appsettings set`:
   - `DATABASE_URL` (Azure SQL aioodbc URL)
   - `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`
   - `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT`
   - `APP_SECRET_KEY`, `FERNET_KEY`
   - `QUEUE_PROVIDER=servicebus`, `SERVICE_BUS_CONNECTION_STRING`
   - `AZURE_BLOB_CONNECTION_STRING`
   - `MCP_API_KEY`
   - `FRONTEND_URL` (Static Web App URL)
3. Grant the Web App managed identity Key Vault Secrets User RBAC if reading secrets via reference.
4. Add the API URL to your Entra app registration redirect URIs.

See `docs/deployment-azure.md` for the full step-by-step.
