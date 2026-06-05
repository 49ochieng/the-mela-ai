/*
  monitoring.bicep — Log Analytics Workspace + Application Insights
  Supports conditional creation: pass useExisting=true to reference an existing workspace.
*/

targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Log Analytics Workspace name')
param logAnalyticsName string

@description('Application Insights name')
param appInsightsName string

@description('Resource tags')
param tags object

@description('Set to true to reference an existing workspace instead of creating one')
param useExisting bool = false

// ── Existing resource references ─────────────────────────────────────────────

resource existingLogAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = if (useExisting) {
  name: logAnalyticsName
}

resource existingAppInsights 'Microsoft.Insights/components@2020-02-02' existing = if (useExisting) {
  name: appInsightsName
}

// ── New resource creation ─────────────────────────────────────────────────────

resource newLogAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = if (!useExisting) {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      searchVersion: 1
    }
  }
}

resource newAppInsights 'Microsoft.Insights/components@2020-02-02' = if (!useExisting) {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: newLogAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

output appInsightsConnectionString string = useExisting
  ? existingAppInsights.properties.ConnectionString
  : newAppInsights.properties.ConnectionString

output appInsightsInstrumentationKey string = useExisting
  ? existingAppInsights.properties.InstrumentationKey
  : newAppInsights.properties.InstrumentationKey

output appInsightsId string = useExisting
  ? existingAppInsights.id
  : newAppInsights.id

output logAnalyticsWorkspaceId string = useExisting
  ? existingLogAnalytics.id
  : newLogAnalytics.id
