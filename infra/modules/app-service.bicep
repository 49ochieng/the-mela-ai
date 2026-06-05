/*
  app-service.bicep — App Service Plan + Backend (Python/FastAPI) + Frontend (Node.js/Next.js)
  Both apps get system-assigned managed identities for Key Vault access.
  Supports conditional creation of the plan and each web app.
*/

targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('App Service Plan name')
param planName string

@description('Backend (FastAPI/Python) Web App name — globally unique')
param backendAppName string

@description('Frontend (Next.js) Web App name — globally unique')
param frontendAppName string

@description('App Service Plan SKU — B1 (dev), P1v3 (prod)')
@allowed(['F1', 'B1', 'B2', 'B3', 'S1', 'S2', 'S3', 'P0v3', 'P1v3', 'P2v3'])
param planSku string = 'B1'

@description('Set to true to reference an existing App Service Plan')
param useExistingPlan bool = false

@description('Set to true to reference an existing backend Web App')
param useExistingBackendApp bool = false

@description('Set to true to reference an existing frontend Web App')
param useExistingFrontendApp bool = false

// ── Configuration injected into both apps ─────────────────────────────────────

@description('Key Vault URI for KV references in App Settings')
param keyVaultUri string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Backend API URL visible to the frontend (e.g. https://armely-ai-api.azurewebsites.net)')
param backendApiUrl string = ''

@description('CORS origins — comma-separated list of allowed frontend origins')
param corsOrigins string = ''

@description('App environment tag (development / production)')
param appEnv string = 'production'

@description('Key Vault secret URI for the Redis connection string. Empty = Redis disabled.')
param redisKeyVaultSecretUri string = ''

@description('ACS sender address for ops alert emails')
param acsSenderAddress string = 'DoNotReply@armely.com'

@description('Comma-separated ops alert recipients (always includes edgar.mcochieng@armely.com)')
param alertRecipients string = 'edgar.mcochieng@armely.com'

// ── App Service Plan ──────────────────────────────────────────────────────────

resource existingPlan 'Microsoft.Web/serverfarms@2023-01-01' existing = if (useExistingPlan) {
  name: planName
}

resource newPlan 'Microsoft.Web/serverfarms@2023-01-01' = if (!useExistingPlan) {
  name: planName
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: planSku
  }
  properties: {
    reserved: true   // required for Linux
  }
}

var planId = useExistingPlan ? existingPlan.id : newPlan.id

// F1 (Free) does not support alwaysOn or health checks
var isFreeTier = planSku == 'F1' || planSku == 'D1'

// ── Helper: Key Vault secret reference string ─────────────────────────────────
// Format: @Microsoft.KeyVault(SecretUri=https://kv-name.vault.azure.net/secrets/secret-name/)
// Note: vaultUri already ends with '/'

var _kvBase = '${keyVaultUri}secrets/'

// ── Backend Web App (Python 3.12 on Linux) ────────────────────────────────────

resource existingBackend 'Microsoft.Web/sites@2023-01-01' existing = if (useExistingBackendApp) {
  name: backendAppName
}

resource newBackend 'Microsoft.Web/sites@2023-01-01' = if (!useExistingBackendApp) {
  name: backendAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'backend' })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: planId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: isFreeTier ? false : true   // F1/D1 do not support alwaysOn
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: isFreeTier ? '' : '/health'  // F1 does not support health checks
      appCommandLine: 'bash /home/site/wwwroot/startup.sh'
    }
  }
}

// Unconditional web config — applies appCommandLine even for existing apps (useExistingBackendApp=true)
// startup.sh handles package installation via pip --target .packages/ on first boot,
// then exports PYTHONPATH and calls gunicorn. Subsequent boots use the cached .packages/.
resource backendWebConfig 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${backendAppName}/web'
  dependsOn: [newBackend, existingBackend]
  properties: {
    appCommandLine: 'bash /home/site/wwwroot/startup.sh'
    linuxFxVersion: 'PYTHON|3.12'
    alwaysOn: isFreeTier ? false : true
    ftpsState: 'Disabled'
    minTlsVersion: '1.2'
    http20Enabled: true
    healthCheckPath: isFreeTier ? '' : '/health'
  }
}

// Apply App Settings to whichever backend resource exists (idempotent — always runs)
resource backendSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${backendAppName}/appsettings'
  dependsOn: [newBackend, existingBackend]
  properties: {
    APP_ENV: appEnv
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    ApplicationInsightsAgent_EXTENSION_VERSION: '~3'
    // Secrets via Key Vault references — App Service resolves these at runtime using managed identity
    JWT_SECRET_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}jwt-secret-key/)'
    AZURE_CLIENT_SECRET: '@Microsoft.KeyVault(SecretUri=${_kvBase}azure-client-secret/)'
    AI_FOUNDRY_API_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}ai-foundry-api-key/)'
    AZURE_OPENAI_API_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}azure-openai-api-key/)'
    AZURE_SPEECH_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}azure-speech-key/)'
    AZURE_DALLE_API_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}azure-dalle-api-key/)'
    AZURE_SEARCH_ADMIN_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}azure-search-admin-key/)'
    AZURE_STORAGE_ACCOUNT_KEY: '@Microsoft.KeyVault(SecretUri=${_kvBase}azure-storage-account-key/)'
    DATABASE_URL: '@Microsoft.KeyVault(SecretUri=${_kvBase}database-url/)'
    REDIS_URL: empty(redisKeyVaultSecretUri) ? '' : '@Microsoft.KeyVault(SecretUri=${redisKeyVaultSecretUri})'
    // ── Ops alerting (ACS Email + Teams) ──────────────────────────────────
    ACS_CONNECTION_STRING: '@Microsoft.KeyVault(SecretUri=${_kvBase}acs-connection-string/)'
    ACS_SENDER_ADDRESS: acsSenderAddress
    ALERT_RECIPIENTS: alertRecipients
    TEAMS_WEBHOOK_URL: '@Microsoft.KeyVault(SecretUri=${_kvBase}teams-webhook-url/)'
    // Non-secret settings (fill remaining via azd env / App Settings)
    // JSON array format required by pydantic_settings v2 for List[str] fields
    CORS_ORIGINS: empty(corsOrigins) ? '["https://${frontendAppName}.azurewebsites.net","http://localhost:3000"]' : corsOrigins
    // startup.sh installs packages to .packages/ on first boot; 1800s allows pip install to complete
    WEBSITES_CONTAINER_START_TIME_LIMIT: '1800'
    // Disable Oryx build-during-deploy (startup.sh handles packages; avoids SCM-restart race condition)
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'false'
    // NOTE: WEBSITE_RUN_FROM_PACKAGE is intentionally absent — ZipDeploy needs filesystem writes
  }
}

// ── Frontend Web App (Node.js 20 on Linux) ────────────────────────────────────

resource existingFrontend 'Microsoft.Web/sites@2023-01-01' existing = if (useExistingFrontendApp) {
  name: frontendAppName
}

resource newFrontend 'Microsoft.Web/sites@2023-01-01' = if (!useExistingFrontendApp) {
  name: frontendAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'frontend' })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: planId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|20-lts'
      alwaysOn: isFreeTier ? false : true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: isFreeTier ? '' : '/'
      appCommandLine: 'node server.js'
    }
  }
}

resource frontendSettings 'Microsoft.Web/sites/config@2023-01-01' = {
  name: '${frontendAppName}/appsettings'
  dependsOn: [newFrontend, existingFrontend]
  properties: {
    NEXT_PUBLIC_API_URL: empty(backendApiUrl) ? 'https://${backendAppName}.azurewebsites.net' : backendApiUrl
    NEXT_PUBLIC_API_VERSION: 'v1'
    NEXT_PUBLIC_APP_NAME: 'Mela AI'
    NEXT_PUBLIC_ORG_NAME: 'Armely'
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    WEBSITE_RUN_FROM_PACKAGE: '1'
    NODE_ENV: 'production'
    PORT: '8080'
    WEBSITES_PORT: '8080'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

output backendPrincipalId string = useExistingBackendApp
  ? existingBackend.identity.principalId
  : newBackend.identity.principalId

output frontendPrincipalId string = useExistingFrontendApp
  ? existingFrontend.identity.principalId
  : newFrontend.identity.principalId

output backendDefaultHostname string = useExistingBackendApp
  ? existingBackend.properties.defaultHostName
  : newBackend.properties.defaultHostName

output frontendDefaultHostname string = useExistingFrontendApp
  ? existingFrontend.properties.defaultHostName
  : newFrontend.properties.defaultHostName

output backendAppId string = useExistingBackendApp ? existingBackend.id : newBackend.id
output frontendAppId string = useExistingFrontendApp ? existingFrontend.id : newFrontend.id
