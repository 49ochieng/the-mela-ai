// ─────────────────────────────────────────────────────────────────────────
// Phase 1: Azure Cache for Redis (Standard tier).
//
// Standard C1 (1 GB, ~$73/mo) is the smallest tier that gives:
//   - Replicated nodes (HA)
//   - 99.9% SLA
//   - SSL-only (TLS 1.2)
//
// The connection string is written to Key Vault as `redis-connection-string`
// so the App Service can read it via @Microsoft.KeyVault references.
// ─────────────────────────────────────────────────────────────────────────

@description('Resource name for the Redis cache.')
param name string

@description('Azure region for deployment.')
param location string = resourceGroup().location

@description('Tags applied to the Redis resource.')
param tags object = {}

@description('Sku tier — Standard is the smallest replicated tier.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Standard'

@description('Sku family — C for Basic/Standard, P for Premium.')
@allowed([
  'C'
  'P'
])
param skuFamily string = 'C'

@description('Sku capacity. C1 = 1 GB.')
@minValue(0)
@maxValue(6)
param skuCapacity int = 1

@description('Optional Key Vault to receive the connection string secret.')
param keyVaultName string = ''

@description('Secret name in Key Vault.')
param secretName string = 'redis-connection-string'

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: skuName
      family: skuFamily
      capacity: skuCapacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    redisConfiguration: {
      // Reasonable defaults for chat-cache + rate-limit usage:
      'maxmemory-policy': 'allkeys-lru'
    }
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (!empty(keyVaultName)) {
  name: keyVaultName
}

resource kvSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(keyVaultName)) {
  parent: kv
  name: secretName
  properties: {
    value: 'rediss://:${redis.listKeys().primaryKey}@${redis.properties.hostName}:${redis.properties.sslPort}'
    attributes: {
      enabled: true
    }
  }
}

output redisHostName string = redis.properties.hostName
output redisSslPort int = redis.properties.sslPort
output redisName string = redis.name
output keyVaultSecretUri string = !empty(keyVaultName) ? kvSecret.properties.secretUri : ''
