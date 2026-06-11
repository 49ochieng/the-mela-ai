// Mela Task Radar — Azure infra (Bicep)
// Provisions: Container Registry, App Service Plan, 5 Web Apps (api/worker/sched/mcp/web),
//             Storage, Service Bus, Key Vault, App Insights, Log Analytics.
// NOTE: SQL is NOT provisioned here — uses existing armely.database.windows.net / MelaBilling.
//
// Usage (first time):
//   az account set --subscription 582b74e6-6cea-4329-8cb4-473b9653ae03
//   az deployment group create \
//     -g EdgarO_RG_MCPP_WU2 \
//     -f infra/azure/main.bicep \
//     -p namePrefix=melatr
//
// After first deploy, run infra/azure/deploy.ps1 to push images and set secrets.

@description('Short prefix for resource names (lowercase, 5-9 chars). Must be ≥5 so the ACR name (prefix+acr) meets Azure minimum of 5.')
@minLength(5)
@maxLength(9)
param namePrefix string

@description('Azure region.')
param location string = resourceGroup().location

@description('App Service plan SKU.')
param planSku string = 'P1v3'

var planName  = '${namePrefix}-plan'
var apiName   = '${namePrefix}-api'
var workerName= '${namePrefix}-worker'
var schedName = '${namePrefix}-sched'
var mcpName   = '${namePrefix}-mcp'
var webName   = '${namePrefix}-web'
var stgName   = take(toLower('${namePrefix}stg${uniqueString(resourceGroup().id)}'), 24)
var sbName    = '${namePrefix}-bus'
// KV names are globally unique; use a stable unique suffix to avoid soft-delete collisions
var kvName    = take(toLower('${namePrefix}kv${uniqueString(resourceGroup().id)}'), 24)
var aiName    = '${namePrefix}-ai'
var lawName   = '${namePrefix}-law'
var acrName   = '${namePrefix}acr'

// ── Observability ────────────────────────────────────────────
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource ai 'Microsoft.Insights/components@2020-02-02' = {
  name: aiName
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: law.id }
}

// ── Container Registry ───────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
  }
}

// ── App Service Plan ─────────────────────────────────────────
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: { name: planSku }
  kind: 'linux'
  properties: { reserved: true }
}

// ── Shared app settings (non-secret, common to all backend apps) ─
// Secrets arrive via @Microsoft.KeyVault(SecretUri=...) references
// set by deploy.ps1 after Key Vault secrets are populated.
// commonAppSettings intentionally omits WEBSITES_PORT — each app sets its own port
// to avoid duplicates when union()-ing with per-app overrides.
var commonAppSettings = [
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: ai.properties.ConnectionString }
  { name: 'APP_ENV',                            value: 'production' }
  { name: 'DEBUG',                              value: 'false' }
  { name: 'LOG_LEVEL',                          value: 'WARNING' }
  { name: 'LOG_FORMAT',                         value: 'json' }
  { name: 'COOKIE_SECURE',                      value: 'true' }
  { name: 'COOKIE_SAMESITE',                    value: 'lax' }
  { name: 'COOKIE_HOST_PREFIX',                 value: 'true' }
  { name: 'CSRF_ENABLED',                       value: 'true' }
  { name: 'RATE_LIMIT_ENABLED',                 value: 'true' }
  { name: 'QUEUE_PROVIDER',                     value: 'servicebus' }
  { name: 'AZURE_SERVICE_BUS_QUEUE',            value: 'scan-jobs' }
  { name: 'KEY_VAULT_URL',                      value: kv.properties.vaultUri }
  { name: 'JWT_ALGORITHM',                      value: 'HS256' }
  { name: 'ACCESS_TOKEN_EXPIRE_MINUTES',        value: '480' }
  { name: 'AZURE_TENANT_ID',                    value: subscription().tenantId }
  { name: 'ENABLE_TEAMS_SCAN',                  value: 'false' }
  { name: 'ENABLE_EXCEL_SYNC',                  value: 'true' }
  { name: 'ENABLE_PLANNER_SYNC',                value: 'true' }
  { name: 'ENABLE_MCP_SERVER',                  value: 'true' }
  { name: 'ENABLE_REALTIME_WEBHOOKS',           value: 'false' }
  { name: 'DOCKER_REGISTRY_SERVER_URL',         value: 'https://${acr.properties.loginServer}' }
]

// ── Backend App Services ─────────────────────────────────────
resource api 'Microsoft.Web/sites@2023-12-01' = {
  name: apiName
  location: location
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --workers 2 --timeout 120'
      appSettings: union(commonAppSettings, [
        { name: 'WEBSITES_PORT',          value: '8000' }
        { name: 'FRONTEND_URL',           value: 'https://${webName}.azurewebsites.net' }
        { name: 'BACKEND_URL',            value: 'https://${apiName}.azurewebsites.net' }
        { name: 'MCP_SERVER_URL',         value: 'https://${mcpName}.azurewebsites.net' }
        { name: 'MICROSOFT_REDIRECT_URI', value: 'https://${apiName}.azurewebsites.net/api/auth/microsoft/callback' }
      ])
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
    }
  }
  identity: { type: 'SystemAssigned' }
}

resource worker 'Microsoft.Web/sites@2023-12-01' = {
  name: workerName
  location: location
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python -m app.workers.worker'
      appSettings: union(commonAppSettings, [{ name: 'WEBSITES_PORT', value: '8000' }])
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
  }
  identity: { type: 'SystemAssigned' }
}

resource scheduler 'Microsoft.Web/sites@2023-12-01' = {
  name: schedName
  location: location
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python -m app.scheduler.scheduler'
      appSettings: union(commonAppSettings, [{ name: 'WEBSITES_PORT', value: '8000' }])
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
  }
  identity: { type: 'SystemAssigned' }
}

resource mcp 'Microsoft.Web/sites@2023-12-01' = {
  name: mcpName
  location: location
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: 'SystemAssigned'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python -m app.mcp.server'
      appSettings: union(commonAppSettings, [
        { name: 'WEBSITES_PORT', value: '8090' }   // MCP listens on 8090
      ])
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
  }
  identity: { type: 'SystemAssigned' }
}

// ── Frontend Web App (Next.js) ───────────────────────────────
resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: webName
  location: location
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|20-lts'
      appSettings: [
        { name: 'WEBSITES_PORT',                        value: '2005' }
        { name: 'PORT',                                 value: '2005' }
        { name: 'NODE_ENV',                             value: 'production' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: ai.properties.ConnectionString }
        { name: 'NEXT_PUBLIC_API_URL',                  value: 'https://${apiName}.azurewebsites.net' }
        { name: 'NEXT_PUBLIC_MCP_URL',                  value: 'https://${mcpName}.azurewebsites.net' }
        { name: 'DOCKER_REGISTRY_SERVER_URL',           value: 'https://${acr.properties.loginServer}' }
      ]
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
    }
  }
  identity: { type: 'SystemAssigned' }
}

// ── Storage ──────────────────────────────────────────────────
resource stg 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: stgName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { allowBlobPublicAccess: false, minimumTlsVersion: 'TLS1_2' }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: stg
  name: 'default'
}

resource attachmentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'taskradar-attachments'
  properties: { publicAccess: 'None' }
}

// ── Service Bus ──────────────────────────────────────────────
resource sb 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: sbName
  location: location
  sku: { name: 'Standard', tier: 'Standard' }
}

resource sbQueue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: sb
  name: 'scan-jobs'
  properties: { maxDeliveryCount: 5, deadLetteringOnMessageExpiration: true }
}

// ── Key Vault ────────────────────────────────────────────────
resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: kvName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enabledForTemplateDeployment: true
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
  }
}

// ── KV role: "Key Vault Secrets User" for each App Service MI ─
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource kvRoleApi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, api.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvRoleWorker 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, worker.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: worker.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvRoleSched 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, scheduler.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: scheduler.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvRoleMcp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, mcp.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: mcp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── ACR role: "AcrPull" for each App Service MI ──────────────
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullApi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, api.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPullWorker 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, worker.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: worker.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPullSched 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, scheduler.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: scheduler.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPullMcp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, mcp.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: mcp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPullWeb 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, web.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: web.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ──────────────────────────────────────────────────
output apiAppName        string = api.name
output workerAppName     string = worker.name
output schedulerAppName  string = scheduler.name
output mcpAppName        string = mcp.name
output webAppName        string = web.name
output apiUrl            string = 'https://${api.properties.defaultHostName}'
output mcpUrl            string = 'https://${mcp.properties.defaultHostName}'
output webUrl            string = 'https://${web.properties.defaultHostName}'
output storageAccount    string = stg.name
output serviceBusNs      string = sb.name
output keyVaultName      string = kv.name
output keyVaultUri       string = kv.properties.vaultUri
output acrLoginServer    string = acr.properties.loginServer
output appInsightsCs     string = ai.properties.ConnectionString
