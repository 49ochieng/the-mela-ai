/*
  main.bicep — Mela AI infrastructure top-level orchestrator
  Target scope: resource group (rg-ai in subscription armely-isv)

  Conditional creation:
    Set useExisting* = true to reference existing resources instead of creating them.
    The preflight scripts detect existing resources and set these flags automatically.

  Safe to run repeatedly — all resources and role assignments are idempotent.
*/

targetScope = 'resourceGroup'

// ── Environment & Location ────────────────────────────────────────────────────

@description('Short environment name used for tags (dev | prod)')
param environmentName string = 'dev'

@description('Azure region. Default: eastus2 (East US 2). Override via AZURE_LOCATION env var.')
param location string = 'eastus2'

// ── Resource Names ────────────────────────────────────────────────────────────
// Defined in scripts/naming.json — pass overrides only if you need non-default names.

@description('App Service Plan name')
param appServicePlanName string = 'asp-armely-ai'

@description('Backend (FastAPI/Python) Web App name — must be globally unique')
param backendAppName string = 'armely-ai-api'

@description('Frontend (Next.js) Web App name — must be globally unique')
param frontendAppName string = 'armely-ai-web'

@description('Key Vault name — 3-24 chars, globally unique')
param keyVaultName string = 'kv-mela-mcpp'

@description('Application Insights name')
param appInsightsName string = 'ai-armely-ai'

@description('Log Analytics Workspace name')
param logAnalyticsName string = 'log-armely-ai'

@description('Storage Account name — 3-24 lowercase alphanumeric, globally unique')
param storageAccountName string = 'starmelyai'

// ── Reuse Flags (set by scripts/preflight.sh / preflight.ps1) ─────────────────
// false = create new resource; true = reference existing resource

param useExistingAppServicePlan bool = false
param useExistingBackendApp     bool = false
param useExistingFrontendApp    bool = false
param useExistingKeyVault       bool = false
param useExistingAppInsights    bool = false
param useExistingStorage        bool = false

@description('Set to true to provision a Storage Account (required for blob upload features)')
param provisionStorage bool = false

// ── Redis ─────────────────────────────────────────────────────────────────────

@description('Set to true to provision Azure Cache for Redis')
param provisionRedis bool = false

@description('Redis cache name — globally unique')
param redisCacheName string = 'redis-mela-ai'

@description('Redis SKU tier: Basic (no HA), Standard (replicated, recommended for prod), Premium')
@allowed(['Basic', 'Standard', 'Premium'])
param redisSkuName string = 'Basic'

@description('Redis SKU capacity. C0=250 MB (Basic only, cheapest), C1=1 GB.')
param redisSkuCapacity int = 0

// ── Proactive Alerting ──────────────────────────────────────────────────────

@description('Enable proactive Azure Monitor alerts (Action Group + metric/log alerts)')
param enableProactiveAlerts bool = true

@description('Enable diagnostic settings for App Services to Log Analytics')
param enableDiagnosticSettings bool = true

@description('Action Group name for ops alerts')
param alertActionGroupName string = 'ag-mela-ops'

@description('Action Group short name (<=12 chars)')
param alertActionGroupShortName string = 'melaops'

@description('Alert email recipients for Azure Monitor action group')
param alertEmailRecipients array = [
  'edgar.mcochieng@armely.com'
]

@description('Backend App Service HTTP 5xx threshold over 5 minutes')
param backendHttp5xxThreshold int = 5

@description('Backend average response time threshold in seconds over 5 minutes')
param backendResponseTimeThresholdSeconds int = 2

@description('App Insights exception threshold over 5 minutes')
param appExceptionsThreshold int = 3

@description('Background error signature threshold over 5 minutes')
param backgroundErrorThreshold int = 1

@description('Enable synthetic backend availability probe and alert')
param enableSyntheticAvailability bool = true

@description('Synthetic test monitor name')
param syntheticTestName string = 'mela-backend-health'

@description('Synthetic test URL override. Empty uses backend default /health URL')
param syntheticTestUrl string = ''

@description('Synthetic probe frequency in seconds')
param syntheticTestFrequencySeconds int = 300

@description('Synthetic probe timeout in seconds')
param syntheticTestTimeoutSeconds int = 30

@description('Number of failed synthetic locations required for alert')
param syntheticFailedLocationThreshold int = 1

@description('Synthetic probe locations')
param syntheticTestLocations array = [
  'us-ca-sjc-azr'
  'emea-nl-ams-azr'
]

// ── App Service Plan SKU ──────────────────────────────────────────────────────
// P1v3 is the recommended floor for production deployments (Sprint 4.1).
// Dev/staging can override to B1/B2 via the parameters file. The CD pipeline
// passes appServicePlanSku explicitly when 'inputs.environment == dev' to
// keep development cheap.
@description('App Service Plan pricing tier')
@allowed(['F1', 'B1', 'B2', 'B3', 'S1', 'S2', 'S3', 'P0v3', 'P1v3', 'P2v3'])
param appServicePlanSku string = 'P1v3'

// ── Deploy Principal (CI Service Principal) ───────────────────────────────────
@description('Object ID of the CI/CD service principal. Used to grant Key Vault Secrets Officer for secret writes.')
param deployPrincipalObjectId string = ''

@description('Type of the deploying principal: User (interactive CLI) or ServicePrincipal (CI/CD).')
@allowed(['User', 'ServicePrincipal'])
param deployPrincipalType string = 'ServicePrincipal'

// ── Tags applied to all resources ────────────────────────────────────────────

var tags = {
  environment: environmentName
  project: 'mela-ai'
  managedBy: 'bicep'
  organization: 'armely'
  repository: 'mela-ai'
}

// ── Module: Monitoring (Log Analytics + App Insights) ─────────────────────────

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    tags: tags
    useExisting: useExistingAppInsights
  }
}

// ── Module: Key Vault ─────────────────────────────────────────────────────────

module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  params: {
    location: location
    keyVaultName: keyVaultName
    tags: tags
    useExisting: useExistingKeyVault
    deployPrincipalObjectId: deployPrincipalObjectId
    deployPrincipalType: deployPrincipalType
  }
}

// ── Module: Storage Account (optional) ────────────────────────────────────────

module storage 'modules/storage.bicep' = if (provisionStorage) {
  name: 'storage'
  params: {
    location: location
    storageAccountName: storageAccountName
    tags: tags
    useExisting: useExistingStorage
  }
}

// ── Module: App Service (Plan + Backend + Frontend) ───────────────────────────

module appService 'modules/app-service.bicep' = {
  name: 'app-service'
  dependsOn: [monitoring, keyVault]
  params: {
    location: location
    tags: tags
    planName: appServicePlanName
    backendAppName: backendAppName
    frontendAppName: frontendAppName
    planSku: appServicePlanSku
    useExistingPlan: useExistingAppServicePlan
    useExistingBackendApp: useExistingBackendApp
    useExistingFrontendApp: useExistingFrontendApp
    keyVaultUri: keyVault.outputs.keyVaultUri
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    appEnv: environmentName == 'prod' ? 'production' : 'development'
    #disable-next-line BCP318
    redisKeyVaultSecretUri: provisionRedis ? redis.outputs.keyVaultSecretUri : ''
  }
}

// ── Module: Alerts + Diagnostics ───────────────────────────────────────────

module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  dependsOn: [appService]
  params: {
    location: location
    tags: tags
    backendAppName: backendAppName
    frontendAppName: frontendAppName
    appInsightsName: appInsightsName
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    enableAlerts: enableProactiveAlerts
    enableDiagnostics: enableDiagnosticSettings
    actionGroupName: alertActionGroupName
    actionGroupShortName: alertActionGroupShortName
    alertEmailRecipients: alertEmailRecipients
    backendHttp5xxThreshold: backendHttp5xxThreshold
    backendResponseTimeThresholdSeconds: backendResponseTimeThresholdSeconds
    appExceptionsThreshold: appExceptionsThreshold
    backgroundErrorThreshold: backgroundErrorThreshold
    enableSyntheticAvailability: enableSyntheticAvailability
    syntheticTestName: syntheticTestName
    syntheticTestUrl: syntheticTestUrl
    syntheticTestFrequencySeconds: syntheticTestFrequencySeconds
    syntheticTestTimeoutSeconds: syntheticTestTimeoutSeconds
    syntheticFailedLocationThreshold: syntheticFailedLocationThreshold
    syntheticTestLocations: syntheticTestLocations
  }
}

// ── Module: Redis Cache (optional) ───────────────────────────────────────────

module redis 'modules/redis.bicep' = if (provisionRedis) {
  name: 'redis'
  dependsOn: [keyVault]
  params: {
    name: redisCacheName
    location: location
    tags: tags
    skuName: redisSkuName
    skuFamily: 'C'
    skuCapacity: redisSkuCapacity
    keyVaultName: keyVaultName
    secretName: 'redis-connection-string'
  }
}

// ── Module: RBAC Role Assignments ─────────────────────────────────────────────

module rbac 'modules/role-assignments.bicep' = {
  name: 'rbac'
  dependsOn: [keyVault, appService]
  params: {
    keyVaultId: keyVault.outputs.keyVaultId
    backendPrincipalId: appService.outputs.backendPrincipalId
    frontendPrincipalId: appService.outputs.frontendPrincipalId
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

output backendUrl string = 'https://${appService.outputs.backendDefaultHostname}'
output frontendUrl string = 'https://${appService.outputs.frontendDefaultHostname}'
output keyVaultName string = keyVault.outputs.keyVaultResourceName
output keyVaultUri string = keyVault.outputs.keyVaultUri
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output resourceGroupName string = resourceGroup().name
output subscriptionId string = subscription().subscriptionId
#disable-next-line BCP318
output redisHostName string = provisionRedis ? redis.outputs.redisHostName : ''
#disable-next-line BCP318
output redisKeyVaultSecretUri string = provisionRedis ? redis.outputs.keyVaultSecretUri : ''
output monitoringActionGroupId string = alerts.outputs.actionGroupId
output syntheticWebTestId string = alerts.outputs.syntheticWebTestId
