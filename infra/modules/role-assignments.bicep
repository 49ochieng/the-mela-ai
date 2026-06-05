/*
  role-assignments.bicep — RBAC role assignments for managed identities on Key Vault.
  Safe to run repeatedly (idempotent via deterministic GUID names).
*/

targetScope = 'resourceGroup'

@description('Key Vault resource ID')
param keyVaultId string

@description('Principal ID of the backend Web App managed identity')
param backendPrincipalId string

@description('Principal ID of the frontend Web App managed identity (if it needs KV access)')
param frontendPrincipalId string = ''

// Built-in role definition IDs
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User

// ── Backend: Key Vault Secrets User (read secrets at runtime) ─────────────────

resource backendKvUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, backendPrincipalId, kvSecretsUserRoleId)
  scope: resourceGroup()   // scoped to the whole RG so it covers any KV in the group
  properties: {
    principalId: backendPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ── Frontend: Key Vault Secrets User (if frontend also needs KV secrets) ──────

resource frontendKvUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(frontendPrincipalId)) {
  name: guid(keyVaultId, frontendPrincipalId, kvSecretsUserRoleId)
  scope: resourceGroup()
  properties: {
    principalId: frontendPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// NOTE: Deploy principal Secrets Officer is assigned in key-vault.bicep (KV scope).
// Removed duplicate here to avoid GUID collision (same seed → same name → ARM conflict).
