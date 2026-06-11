# Azure deployment

## Resources

- App Service (Linux) or Container Apps × 2 — `api`, `web`
- Container Apps Job — `worker` and `scheduler`
- Azure SQL Database
- Azure Storage Account (blob container `taskradar-attachments`)
- Azure Service Bus namespace + queue `taskradar-scans`
- Azure Key Vault
- Azure OpenAI (GPT-5.2 deployment)
- Application Insights

See `infra/azure/main.bicep` for a starter template.

## Steps

1. `az login && az account set --subscription <sub>`
2. `az deployment group create -g <rg> -f infra/azure/main.bicep -p prefix=taskradar`
3. Create Entra app registration (see `docs/graph-permissions.md`).
4. Push secrets into Key Vault. Reference them from App Service via
   `@Microsoft.KeyVault(SecretUri=...)`.
5. Build images and push to ACR, then deploy.
6. Run `alembic upgrade head` against Azure SQL once.

## Required app settings

All keys from `.env.example` are set as App Service Application Settings,
with secrets pulled from Key Vault.

## Identity

Enable system-assigned managed identity on each App Service / Container App
and grant `Key Vault Secrets User` and `Storage Blob Data Contributor`.
