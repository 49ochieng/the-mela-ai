/*
  alerts.bicep — proactive monitoring and alerting for Mela AI.

  Creates:
  - Azure Monitor Action Group (email receivers)
  - Metric alerts for backend App Service (HTTP 5xx + response time)
  - Scheduled query alerts (exceptions + worker/background error signatures)
  - Synthetic availability web test + failed-location alert
  - Diagnostic settings for backend/frontend App Service to Log Analytics
*/

targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('Backend Web App name')
param backendAppName string

@description('Frontend Web App name')
param frontendAppName string

@description('Application Insights component name')
param appInsightsName string

@description('Log Analytics workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Enable monitor alerts (action group + metric/log alerts)')
param enableAlerts bool = true

@description('Enable App Service diagnostics to Log Analytics')
param enableDiagnostics bool = true

@description('Action Group name')
param actionGroupName string = 'ag-mela-ops'

@description('Action Group short name (<=12 chars)')
param actionGroupShortName string = 'melaops'

@description('Alert email recipients')
param alertEmailRecipients array = [
  'edgar.mcochieng@armely.com'
]

@description('HTTP 5xx threshold over 5 minutes')
param backendHttp5xxThreshold int = 5

@description('Average response time threshold in seconds over 5 minutes')
param backendResponseTimeThresholdSeconds int = 2

@description('Exception count threshold over 5 minutes')
param appExceptionsThreshold int = 3

@description('Background signature threshold over 5 minutes')
param backgroundErrorThreshold int = 1

@description('Enable synthetic availability probe for backend health endpoint')
param enableSyntheticAvailability bool = true

@description('Synthetic test monitor name')
param syntheticTestName string = 'mela-backend-health'

@description('Synthetic probe URL. Empty uses backend default /health URL')
param syntheticTestUrl string = ''

@description('Synthetic test frequency in seconds')
param syntheticTestFrequencySeconds int = 300

@description('Synthetic test timeout in seconds')
param syntheticTestTimeoutSeconds int = 30

@description('Failed locations required to trigger synthetic alert')
param syntheticFailedLocationThreshold int = 1

@description('Synthetic probe locations')
param syntheticTestLocations array = [
  'us-ca-sjc-azr'
  'emea-nl-ams-azr'
]

resource backendApp 'Microsoft.Web/sites@2023-01-01' existing = {
  name: backendAppName
}

resource frontendApp 'Microsoft.Web/sites@2023-01-01' existing = {
  name: frontendAppName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

var hasEmailReceivers = length(alertEmailRecipients) > 0
var alertsEnabled = enableAlerts && hasEmailReceivers
var syntheticEnabled = alertsEnabled && enableSyntheticAvailability
var resolvedSyntheticUrl = empty(syntheticTestUrl)
  ? 'https://${backendAppName}.azurewebsites.net/health'
  : syntheticTestUrl
var syntheticGuid = guid(resourceGroup().id, syntheticTestName)
var syntheticRequestGuid = guid(resourceGroup().id, syntheticTestName, 'request')
var syntheticXml = '<WebTest Name="${syntheticTestName}" Id="${syntheticGuid}" Enabled="True" CssProjectStructure="" CssIteration="" Timeout="${string(syntheticTestTimeoutSeconds)}" WorkItemIds="" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010" Description="Mela AI backend synthetic health probe" CredentialUserName="" CredentialPassword="" PreAuthenticate="True" Proxy="default" StopOnError="False" RecordedResultFile="" ResultsLocale=""> <Items> <Request Method="GET" Guid="${syntheticRequestGuid}" Version="1.1" Url="${resolvedSyntheticUrl}" ThinkTime="0" Timeout="${string(syntheticTestTimeoutSeconds)}" ParseDependentRequests="False" FollowRedirects="True" RecordResult="True" Cache="False" ResponseTimeGoal="0" Encoding="utf-8" ExpectedHttpStatusCode="200" ExpectedResponseUrl="" ReportingName="" IgnoreHttpStatusCode="False" /> </Items> </WebTest>'

var emailReceivers = [for (email, i) in alertEmailRecipients: {
  name: 'ops${i + 1}'
  emailAddress: string(email)
  useCommonAlertSchema: true
}]

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (alertsEnabled) {
  name: actionGroupName
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    groupShortName: actionGroupShortName
    emailReceivers: emailReceivers
  }
}

resource syntheticAvailabilityTest 'Microsoft.Insights/webtests@2022-06-15' = if (syntheticEnabled) {
  name: syntheticTestName
  location: location
  kind: 'ping'
  tags: union(tags, {
    'hidden-link:${appInsights.id}': 'Resource'
  })
  properties: {
    Name: syntheticTestName
    SyntheticMonitorId: syntheticTestName
    Description: 'Mela AI backend synthetic health probe'
    Enabled: true
    Frequency: syntheticTestFrequencySeconds
    Timeout: syntheticTestTimeoutSeconds
    Kind: 'ping'
    RetryEnabled: true
    Locations: [for loc in syntheticTestLocations: {
      Id: string(loc)
    }]
    Configuration: {
      WebTest: syntheticXml
    }
  }
}

resource syntheticAvailabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (syntheticEnabled) {
  name: '${syntheticTestName}-availability-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Synthetic backend health probe failed from one or more locations.'
    severity: 1
    enabled: true
    scopes: [
      appInsights.id
      syntheticAvailabilityTest.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria'
      webTestId: syntheticAvailabilityTest.id
      componentId: appInsights.id
      failedLocationCount: syntheticFailedLocationThreshold
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource backendHttp5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (alertsEnabled) {
  name: '${backendAppName}-http5xx-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Backend App Service HTTP 5xx count is above threshold.'
    severity: 1
    enabled: true
    scopes: [
      backendApp.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Web/sites'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Http5xxThreshold'
          metricName: 'Http5xx'
          metricNamespace: 'Microsoft.Web/sites'
          operator: 'GreaterThan'
          threshold: backendHttp5xxThreshold
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource backendLatencyAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (alertsEnabled) {
  name: '${backendAppName}-latency-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Backend App Service average response time is above threshold.'
    severity: 2
    enabled: true
    scopes: [
      backendApp.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    targetResourceType: 'Microsoft.Web/sites'
    targetResourceRegion: location
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'AvgResponseTimeThreshold'
          metricName: 'AverageResponseTime'
          metricNamespace: 'Microsoft.Web/sites'
          operator: 'GreaterThan'
          threshold: backendResponseTimeThresholdSeconds
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    autoMitigate: true
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

resource appExceptionsQueryAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (alertsEnabled) {
  name: '${appInsightsName}-exceptions-alert'
  location: location
  tags: tags
  properties: {
    displayName: 'Mela AI App Exceptions Spike'
    description: 'Exceptions are above threshold in the backend telemetry stream.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      appInsights.id
    ]
    criteria: {
      allOf: [
        {
          query: 'exceptions | where timestamp >= ago(5m) | summarize AggregatedValue = count()'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: appExceptionsThreshold
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource backgroundSignatureQueryAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (alertsEnabled) {
  name: '${appInsightsName}-background-errors-alert'
  location: location
  tags: tags
  properties: {
    displayName: 'Mela AI Background Worker Errors'
    description: 'Background tasks and ingestion worker signatures indicate failures.'
    enabled: true
    severity: 1
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [
      appInsights.id
    ]
    criteria: {
      allOf: [
        {
          query: 'traces | where timestamp >= ago(5m) | where message has_any ("IngestionWorkerLoopError", "IngestionJobDeadLetter", "Background task crashed", "Background task exited unexpectedly") | summarize AggregatedValue = count()'
          timeAggregation: 'Count'
          operator: 'GreaterThanOrEqual'
          threshold: backgroundErrorThreshold
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource backendDiagnosticSetting 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enableDiagnostics) {
  name: '${backendAppName}-diag'
  scope: backendApp
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'AppServiceHTTPLogs'
        enabled: true
      }
      {
        category: 'AppServiceConsoleLogs'
        enabled: true
      }
      {
        category: 'AppServiceAppLogs'
        enabled: true
      }
      {
        category: 'AppServicePlatformLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

resource frontendDiagnosticSetting 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enableDiagnostics) {
  name: '${frontendAppName}-diag'
  scope: frontendApp
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'AppServiceHTTPLogs'
        enabled: true
      }
      {
        category: 'AppServiceConsoleLogs'
        enabled: true
      }
      {
        category: 'AppServiceAppLogs'
        enabled: true
      }
      {
        category: 'AppServicePlatformLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output actionGroupId string = alertsEnabled ? actionGroup.id : ''
output alertsEnabled bool = alertsEnabled
output diagnosticsEnabled bool = enableDiagnostics
output syntheticEnabled bool = syntheticEnabled
output syntheticWebTestId string = syntheticEnabled ? syntheticAvailabilityTest.id : ''
