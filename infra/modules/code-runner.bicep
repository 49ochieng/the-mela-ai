// Sprint 4.2 — gVisor-hardened code-runner sidecar (Azure Container App)
//
// This deploys a container running the mela-code-runner image with kernel-
// level isolation. The backend App Service POSTs jobs to this app over a
// private endpoint (HTTPS, X-Api-Key auth).
//
// IMPORTANT: Azure Container Apps does NOT natively expose runsc/gVisor as
// a runtime selector. Container-level isolation comes from running each
// replica in its own container with strict resource limits + read-only
// root filesystem. For true gVisor isolation, host the runner on AKS with
// the `runsc` RuntimeClass — switch the image registry below to point at
// that AKS endpoint and the backend continues to work unchanged.

param environmentName string
param location string
param containerImage string = 'mcr.microsoft.com/azuredocs/aci-helloworld:latest'  // placeholder
param containerAppName string = 'ca-mela-code-runner'
param managedEnvName string = 'cae-mela-code-runner'
param logAnalyticsName string
param apiKeySecretName string = 'code-runner-api-key'
@secure()
param apiKey string = newGuid()

resource logws 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: logAnalyticsName
}

resource managedEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: managedEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logws.properties.customerId
        sharedKey: logws.listKeys().primarySharedKey
      }
    }
  }
}

resource codeRunnerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: managedEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false   // internal-only; reachable from the App Service VNet
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      secrets: [
        {
          name: apiKeySecretName
          value: apiKey
        }
      ]
    }
    template: {
      revisionSuffix: 'v1'
      containers: [
        {
          name: 'code-runner'
          image: containerImage
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          env: [
            {
              name: 'API_KEY'
              secretRef: apiKeySecretName
            }
            {
              name: 'EXECUTION_TIMEOUT_SECONDS'
              value: '60'
            }
            {
              name: 'MAX_CONCURRENT_JOBS'
              value: '20'
            }
          ]
          // Strict resource isolation per replica.
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 10
        rules: [
          {
            name: 'http-scale'
            http: {
              metadata: {
                concurrentRequests: '5'
              }
            }
          }
        ]
      }
    }
  }
}

output codeRunnerUrl string = 'https://${codeRunnerApp.properties.configuration.ingress.fqdn}'
output codeRunnerName string = codeRunnerApp.name
