# =============================================================================
# scripts/scan-resources.ps1 — Mela AI Resource Scanner (Windows)
# Scans the repository for Azure service references and writes:
#   docs/resources-required.md
#   scripts/resource-inventory.json
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.
#
# Usage (from repo root):
#   .\scripts\scan-resources.ps1
# =============================================================================
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$DocsDir    = Join-Path $RepoRoot 'docs'
$OutputMd   = Join-Path $DocsDir  'resources-required.md'
$OutputJson = Join-Path $ScriptDir 'resource-inventory.json'

$null = New-Item -ItemType Directory -Force -Path $DocsDir

Write-Host "Scanning repository for Azure resource requirements..." -ForegroundColor Cyan

# ── Search helper ──────────────────────────────────────────────────────────────
function Test-PatternInRepo {
    param([string]$Pattern)
    $extensions = @('*.py','*.ts','*.tsx','*.js','*.env','*.env.*','*.sample','*.json')
    $searchPaths = @(
        (Join-Path $RepoRoot 'backend'),
        (Join-Path $RepoRoot 'frontend'),
        (Join-Path $RepoRoot 'env')
    )
    foreach ($sp in $searchPaths) {
        if (-not (Test-Path $sp)) { continue }
        foreach ($ext in $extensions) {
            $files = Get-ChildItem -Recurse -Path $sp -Filter $ext -ErrorAction SilentlyContinue
            foreach ($f in $files) {
                if (Select-String -Path $f.FullName -Pattern $Pattern -Quiet -ErrorAction SilentlyContinue) {
                    return $true
                }
            }
        }
    }
    return $false
}

# ── Detect services ────────────────────────────────────────────────────────────
Write-Host "  Scanning service references..."

$needs = [ordered]@{
    OpenAI          = Test-PatternInRepo 'AI_FOUNDRY_ENDPOINT|AZURE_OPENAI_ENDPOINT|AZURE_OPENAI_API_KEY'
    Speech          = Test-PatternInRepo 'AZURE_SPEECH_KEY|AZURE_SPEECH_ENDPOINT'
    Dalle           = Test-PatternInRepo 'AZURE_DALLE'
    Search          = Test-PatternInRepo 'AZURE_SEARCH_ENDPOINT|AZURE_SEARCH_API_KEY|AZURE_SEARCH_ADMIN_KEY'
    Storage         = Test-PatternInRepo 'AZURE_STORAGE_ACCOUNT|AZURE_STORAGE_CONNECTION|BLOB_CONTAINER'
    Sql             = Test-PatternInRepo 'AZURE_SQL_SERVER|DATABASE_URL'
    Cosmos          = Test-PatternInRepo 'AZURE_COSMOS_ENDPOINT|COSMOS_ENDPOINT'
    Redis           = Test-PatternInRepo 'REDIS_URL|AZURE_REDIS'
    Translator      = Test-PatternInRepo 'AZURE_TRANSLATOR_KEY|AZURE_TRANSLATOR_ENDPOINT'
    DocIntelligence = Test-PatternInRepo 'AZURE_DOCUMENT_INTELLIGENCE'
    AppInsights     = Test-PatternInRepo 'APPLICATIONINSIGHTS_CONNECTION_STRING|APP_INSIGHTS'
    KeyVault        = Test-PatternInRepo 'AZURE_KEY_VAULT|KEY_VAULT'
}

foreach ($svc in $needs.Keys) {
    $icon = if ($needs[$svc]) { '[FOUND]' } else { '[  --  ]' }
    Write-Host "    $icon $svc"
}

# ── Build Markdown table rows ──────────────────────────────────────────────────
$rows = [System.Collections.Generic.List[string]]::new()
$rows.Add('| App Service Plan (asp-armely-ai) | --  | YES | Bicep auto-provisioned |')
$rows.Add('| Backend Web App (armely-ai-api)  | --  | YES | Bicep auto-provisioned |')
$rows.Add('| Frontend Web App (armely-ai-web) | --  | YES | Bicep auto-provisioned |')
$rows.Add('| Key Vault (kv-armely-ai)         | --  | YES | Bicep auto-provisioned |')
$rows.Add('| App Insights (ai-armely-ai)      | NO  | YES | Bicep auto-provisioned |')

if ($needs.Sql)             { $rows.Add('| Azure SQL / DATABASE_URL      | SQLite | YES | env\.env.local -> KV secret: database-url        |') }
if ($needs.OpenAI)          { $rows.Add('| Azure OpenAI / AI Foundry     | YES    | YES | env\.env.local -> KV secret: ai-foundry-api-key  |') }
if ($needs.Speech)          { $rows.Add('| Azure Cognitive Speech        | NO     | YES | env\.env.local -> KV secret: azure-speech-key    |') }
if ($needs.Dalle)           { $rows.Add('| Azure DALL-E                  | NO     | YES | env\.env.local -> KV secret: azure-dalle-api-key |') }
if ($needs.Search)          { $rows.Add('| Azure AI Search               | NO     | YES | env\.env.local -> KV secret: azure-search-admin-key |') }
if ($needs.Storage)         { $rows.Add('| Azure Blob Storage            | NO     | YES | env\.env.local -> KV secret: azure-storage-account-key |') }
if ($needs.Cosmos)          { $rows.Add('| Azure Cosmos DB               | NO     | OPT | env\.env.local                                   |') }
if ($needs.Redis)           { $rows.Add('| Azure Cache for Redis         | NO     | OPT | env\.env.local                                   |') }
if ($needs.Translator)      { $rows.Add('| Azure Translator              | NO     | YES | env\.env.local                                   |') }
if ($needs.DocIntelligence) { $rows.Add('| Azure Document Intelligence   | NO     | YES | env\.env.local                                   |') }

$tableContent = $rows -join [System.Environment]::NewLine

# ── Markdown code fence ────────────────────────────────────────────────────────
# Using a single-quoted string for the fence token avoids PS escape issues.
$fence = '```'

# ── Build Markdown ─────────────────────────────────────────────────────────────
$md = @"
# Mela AI -- Azure Resources Required

> Auto-generated by ``scripts/scan-resources.ps1``. Re-run after adding new integrations.

## Architecture Overview

- **armely-ai-api** -- FastAPI backend (Python 3.12, Linux App Service)
- **armely-ai-web** -- Next.js 14 frontend (Node.js 20 LTS, Linux App Service)

Both share App Service Plan ``asp-armely-ai`` and Key Vault ``kv-armely-ai``.
Default region: **eastus2** (East US 2).

## Resources Detected in Codebase

| Resource | Required Local | Required Prod | Where Configured |
|----------|:--------------:|:-------------:|------------------|
$tableContent

## Environment Variable Reference

| Variable | Purpose | Local | Prod |
|----------|---------|:-----:|:----:|
| ``AZURE_TENANT_ID`` | Entra ID tenant | env\.env.local | App Setting |
| ``AZURE_CLIENT_ID`` | Entra app client ID | env\.env.local | App Setting |
| ``AZURE_CLIENT_SECRET`` | Entra app secret | env\.env.local | KV: azure-client-secret |
| ``AI_FOUNDRY_ENDPOINT`` | Azure AI Foundry URL | env\.env.local | App Setting |
| ``AI_FOUNDRY_API_KEY`` | Azure AI Foundry key | env\.env.local | KV: ai-foundry-api-key |
| ``AZURE_OPENAI_API_KEY`` | Azure OpenAI key | env\.env.local | KV: azure-openai-api-key |
| ``AZURE_SPEECH_KEY`` | Speech service key | env\.env.local | KV: azure-speech-key |
| ``AZURE_DALLE_API_KEY`` | DALL-E key | env\.env.local | KV: azure-dalle-api-key |
| ``AZURE_SEARCH_ADMIN_KEY`` | AI Search admin key | env\.env.local | KV: azure-search-admin-key |
| ``DATABASE_URL`` | Database connection string | SQLite (auto) | KV: database-url |
| ``JWT_SECRET_KEY`` | JWT signing key | auto-generated | KV: jwt-secret-key |

## External Dependencies (Not Auto-Provisioned)

These must be manually created or already exist:

- [ ] Azure AI Foundry deployments: gpt-4.1, Kimi-K2.5, Mistral-Large-3, text-embedding-3-small, dall-e-3
- [ ] Azure AI Search index (for enterprise RAG)
- [ ] Azure SQL Database (for production -- SQLite used locally)
- [ ] Azure Cognitive Speech resource
- [ ] Azure Document Intelligence resource
- [ ] Entra app registration: Meeting-Intelligence-Bot (tenant: 588cadf4-9902-4465-86c0-8bcf04f4f102)

## Key Vault Secrets Checklist

After first Bicep deployment, populate Key Vault secrets:

${fence}powershell
`$KV = 'kv-armely-ai'

az keyvault secret set --vault-name `$KV --name jwt-secret-key `
    --value (python -c "import secrets; print(secrets.token_hex(32))")

az keyvault secret set --vault-name `$KV --name azure-client-secret     --value `$env:AZURE_CLIENT_SECRET
az keyvault secret set --vault-name `$KV --name ai-foundry-api-key      --value `$env:AI_FOUNDRY_API_KEY
az keyvault secret set --vault-name `$KV --name azure-openai-api-key    --value `$env:AZURE_OPENAI_API_KEY
az keyvault secret set --vault-name `$KV --name azure-speech-key        --value `$env:AZURE_SPEECH_KEY
az keyvault secret set --vault-name `$KV --name azure-dalle-api-key     --value `$env:AZURE_DALLE_API_KEY
az keyvault secret set --vault-name `$KV --name azure-search-admin-key  --value `$env:AZURE_SEARCH_ADMIN_KEY
az keyvault secret set --vault-name `$KV --name database-url            --value `$env:DATABASE_URL
${fence}
"@

Set-Content -Path $OutputMd -Value $md -Encoding UTF8
Write-Host "Written: $OutputMd" -ForegroundColor Green

# ── Write JSON inventory ────────────────────────────────────────────────────────
$inventory = [ordered]@{
    generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    repoRoot    = $RepoRoot
    services    = $needs
}
$inventory | ConvertTo-Json -Depth 3 | Set-Content -Path $OutputJson -Encoding UTF8
Write-Host "Written: $OutputJson" -ForegroundColor Green
Write-Host "Done." -ForegroundColor Green
