/*
  key-vault.bicep — Azure Key Vault
  Supports conditional creation. Uses RBAC authorization mode (recommended over access policies).
  Run role-assignments.bicep separately to grant managed identities access.
*/

targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Key Vault name — 3-24 chars, globally unique')
param keyVaultName string

@description('Resource tags')
param tags object

@description('Set to true to reference an existing Key Vault instead of creating one')
param useExisting bool = false

@description('Object ID of the deploying principal (user/SP) to allow secret writes during initial provisioning')
param deployPrincipalObjectId string = ''

@description('Principal type for the deploying principal: User or ServicePrincipal')
@allowed(['User', 'ServicePrincipal'])
param deployPrincipalType string = 'ServicePrincipal'

// ── Existing resource reference ───────────────────────────────────────────────

resource existingKv 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (useExisting) {
  name: keyVaultName
}

// ── New Key Vault ─────────────────────────────────────────────────────────────

resource newKv 'Microsoft.KeyVault/vaults@2023-07-01' = if (!useExisting) {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true          // RBAC mode — no access policies needed
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true            // required by subscription policy; irreversible once set
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

// ── Grant the deploying principal Key Vault Secrets Officer so CI can write secrets ──

var kvSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7' // Key Vault Secrets Officer (verified correct)

resource deployPrincipalKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExisting && !empty(deployPrincipalObjectId)) {
  name: guid(newKv.id, deployPrincipalObjectId, kvSecretsOfficerRoleId)
  scope: newKv
  properties: {
    principalId: deployPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsOfficerRoleId)
    principalType: deployPrincipalType
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

output keyVaultName string = keyVaultName
output keyVaultUri string = useExisting ? existingKv.properties.vaultUri : newKv.properties.vaultUri
output keyVaultId string = useExisting ? existingKv.id : newKv.id
output keyVaultResourceName string = useExisting ? existingKv.name : newKv.name
