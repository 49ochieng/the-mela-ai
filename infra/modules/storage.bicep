/*
  storage.bicep — Azure Storage Account (optional)
  Used for document uploads and blob storage. Only deployed when useStorage=true.
*/

targetScope = 'resourceGroup'

@description('Azure region')
param location string

@description('Storage account name — 3-24 lowercase alphanumeric, globally unique')
param storageAccountName string

@description('Resource tags')
param tags object

@description('Set to true to reference an existing storage account')
param useExisting bool = false

// ── Existing resource reference ───────────────────────────────────────────────

resource existingStorage 'Microsoft.Storage/storageAccounts@2023-01-01' existing = if (useExisting) {
  name: storageAccountName
}

// ── New Storage Account ───────────────────────────────────────────────────────

resource newStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = if (!useExisting) {
  name: storageAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

// ── Blob containers ───────────────────────────────────────────────────────────

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = if (!useExisting) {
  parent: newStorage
  name: 'default'
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (!useExisting) {
  parent: blobService
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

resource uploadsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = if (!useExisting) {
  parent: blobService
  name: 'uploads'
  properties: {
    publicAccess: 'None'
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────

// listKeys() cannot be called on a union type — call on each concrete resource separately
var _accountName = useExisting ? existingStorage.name : newStorage.name
var _accountId   = useExisting ? existingStorage.id   : newStorage.id
var _primaryKey  = useExisting ? existingStorage.listKeys().keys[0].value : newStorage.listKeys().keys[0].value

output storageAccountName string = _accountName
output storageAccountId string = _accountId
output storageConnectionString string = 'DefaultEndpointsProtocol=https;AccountName=${_accountName};AccountKey=${_primaryKey};EndpointSuffix=core.windows.net'
output primaryKey string = _primaryKey
