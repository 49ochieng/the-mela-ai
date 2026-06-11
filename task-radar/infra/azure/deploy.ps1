<#
.SYNOPSIS
  First-time production deployment for Mela Task Radar on Azure.

.DESCRIPTION
  Runs end-to-end:
    1.  Validates the Bicep template
    2.  Deploys all Azure infrastructure to Mela-AI_CLIENT_APPS
    3.  Grants the deployer Key Vault Secrets Officer so it can write secrets
    4.  Generates and stores all secrets in Key Vault
    5.  Stores connection strings (Service Bus, Storage, Database) in Key Vault
    6.  Configures Key Vault references in every App Service
    7.  Sets all non-secret App Settings
    8.  Logs in to ACR, builds and pushes Docker images
    9.  Switches App Services to Docker container mode
    10. Adds current IP to SQL firewall, runs Alembic migrations, removes IP
    11. Restarts all apps and prints the live URLs

.PREREQUISITES
  - az CLI logged in with Owner rights on ARMELY ISV subscription
  - Docker Desktop running and logged in
  - Python 3.12 + alembic in PATH  (or a .venv at apps/api/.venv)
  - Git repo root as CWD

.EXAMPLE
  cd "C:\copilot\Mela Task Radar"
  $env:AZURE_CLIENT_SECRET = "Vby8Q~..."   # Entra app client secret
  $env:AZURE_OPENAI_API_KEY = "7NPNHc..."  # Azure OpenAI key
  .\infra\azure\deploy.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Constants ────────────────────────────────────────────────
$SUBSCRIPTION_ID = "582b74e6-6cea-4329-8cb4-473b9653ae03"
$RESOURCE_GROUP  = "EdgarO_RG_MCPP_WU2"
$NAME_PREFIX     = "melatr"

# Existing shared SQL — not provisioned by Bicep
$SQL_SERVER   = "armely.database.windows.net"
$SQL_DATABASE = "MelaBilling"
$SQL_USERNAME = "armelysql"
$SQL_PASSWORD = "rX70023f1iwD"

$API_NAME    = "$NAME_PREFIX-api"
$WORKER_NAME = "$NAME_PREFIX-worker"
$SCHED_NAME  = "$NAME_PREFIX-sched"
$MCP_NAME    = "$NAME_PREFIX-mcp"
$WEB_NAME    = "$NAME_PREFIX-web"
$KV_NAME     = "$NAME_PREFIX-kv"
$ACR_NAME    = "${NAME_PREFIX}acr"
$ACR_SERVER  = "$ACR_NAME.azurecr.io"

$API_URL  = "https://$API_NAME.azurewebsites.net"
$MCP_URL  = "https://$MCP_NAME.azurewebsites.net"
$WEB_URL  = "https://$WEB_NAME.azurewebsites.net"

# ── Secrets (read from env — never hardcode in git) ──────────
$AZURE_CLIENT_SECRET = if ($env:AZURE_CLIENT_SECRET) { $env:AZURE_CLIENT_SECRET }
                       else { Read-Host "Enter AZURE_CLIENT_SECRET (Entra app secret)" -AsSecureString | ConvertFrom-SecureString -AsPlainText }
$AZURE_OPENAI_API_KEY = if ($env:AZURE_OPENAI_API_KEY) { $env:AZURE_OPENAI_API_KEY }
                        else { Read-Host "Enter AZURE_OPENAI_API_KEY" -AsSecureString | ConvertFrom-SecureString -AsPlainText }

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }
function OK($msg)        { Write-Host "    OK: $msg" -ForegroundColor Green }
function Info($msg)      { Write-Host "    $msg" -ForegroundColor Gray }

# ── 0. Select subscription ───────────────────────────────────
Step 0 "Selecting ARMELY ISV subscription"
az account set --subscription $SUBSCRIPTION_ID
OK "Subscription set"

# ── 1. Deploy Bicep ──────────────────────────────────────────
Step 1 "Deploying Bicep infrastructure (this takes ~10 min first time)..."
$deployName = "melatr-infra-$(Get-Date -Format 'yyyyMMddHHmmss')"
$deployOutput = az deployment group create `
  --resource-group $RESOURCE_GROUP `
  --name $deployName `
  --template-file "$PSScriptRoot\main.bicep" `
  --parameters namePrefix=$NAME_PREFIX `
  --output json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) { throw "Bicep deployment failed" }
$outputs = $deployOutput.properties.outputs

$KV_URI    = $outputs.keyVaultUri.value
$KV_NAME   = $outputs.keyVaultName.value
$SB_NS     = $outputs.serviceBusNs.value
$STG_NAME  = $outputs.storageAccount.value
$AI_CS     = $outputs.appInsightsCs.value

OK "Infrastructure deployed"
Info "SQL: $SQL_SERVER | DB: $SQL_DATABASE (existing)"
Info "KV : $KV_URI"
Info "ACR: $ACR_SERVER"

# ── 2. Grant deployer Key Vault Secrets Officer ───────────────
Step 2 "Granting deployer Key Vault Secrets Officer on $KV_NAME"
$deployerOid = (az ad signed-in-user show --query id -o tsv)
az role assignment create `
  --role "Key Vault Secrets Officer" `
  --assignee-object-id $deployerOid `
  --assignee-principal-type User `
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.KeyVault/vaults/$KV_NAME" `
  --output none 2>$null
OK "Role assigned (or already present)"

# Give RBAC time to propagate
Start-Sleep -Seconds 15

# ── 3. Generate cryptographic secrets ────────────────────────
Step 3 "Generating cryptographic secrets"
$SECRET_KEY = -join ((1..64) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
$JWT_SECRET = -join ((1..64) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
# Fernet key = URL-safe base64 of 32 random bytes
$fernetBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($fernetBytes)
$TOKEN_ENC_KEY = [System.Convert]::ToBase64String($fernetBytes) `
  -replace '\+', '-' -replace '/', '_' -replace '=', ''
# Pad back to multiple of 4
while ($TOKEN_ENC_KEY.Length % 4 -ne 0) { $TOKEN_ENC_KEY += '=' }
OK "Secrets generated"

# ── 4. Get connection strings from Azure ─────────────────────
Step 4 "Retrieving connection strings from Azure resources"

$SB_CS = az servicebus namespace authorization-rule keys list `
  --resource-group $RESOURCE_GROUP `
  --namespace-name "$NAME_PREFIX-bus" `
  --name RootManageSharedAccessKey `
  --query primaryConnectionString -o tsv

$STG_CS = az storage account show-connection-string `
  --resource-group $RESOURCE_GROUP `
  --name $STG_NAME `
  --query connectionString -o tsv

# Use odbc_connect to pass raw ODBC string — avoids aioodbc URL-parser failures on Windows
$_odbcCs  = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=$SQL_SERVER;DATABASE=$SQL_DATABASE;UID=$SQL_USERNAME;PWD=$SQL_PASSWORD;Encrypt=yes;TrustServerCertificate=no"
$_odbcEnc = [System.Uri]::EscapeDataString($_odbcCs)
$DB_URL   = "mssql+aioodbc:///?odbc_connect=$_odbcEnc"

OK "Connection strings retrieved"

# ── 5. Populate Key Vault ─────────────────────────────────────
Step 5 "Storing secrets in Key Vault $KV_NAME"

$kvSecrets = @{
  "SECRET-KEY"                    = $SECRET_KEY
  "JWT-SECRET"                    = $JWT_SECRET
  "TOKEN-ENCRYPTION-KEY"          = $TOKEN_ENC_KEY
  "AZURE-CLIENT-SECRET"           = $AZURE_CLIENT_SECRET
  "AZURE-OPENAI-API-KEY"          = $AZURE_OPENAI_API_KEY
  "DATABASE-URL"                  = $DB_URL
  "AZURE-BLOB-CONNECTION-STRING"  = $STG_CS
  "SERVICE-BUS-CONNECTION-STRING" = $SB_CS
  "SQL-PASSWORD"                  = $SQL_PASSWORD
}

foreach ($kv in $kvSecrets.GetEnumerator()) {
  # Write to temp file so special chars (&, (, ), etc.) aren't mangled by the shell
  $tmpFile = [System.IO.Path]::GetTempFileName()
  try {
    Set-Content -Path $tmpFile -Value $kv.Value -NoNewline -Encoding UTF8
    az keyvault secret set --vault-name $KV_NAME --name $kv.Key --file $tmpFile --output none
  } finally {
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
  }
  Info "  Stored: $($kv.Key)"
}
OK "All secrets stored in Key Vault"

# ── 6. Configure App Service settings with KV references ─────
Step 6 "Configuring App Service settings (backend apps)"

$kvRef = { param($n) "@Microsoft.KeyVault(SecretUri=${KV_URI}secrets/$n/)" }

# Build settings hashtable; use JSON @file syntax so @Microsoft.KeyVault(...)
# values aren't mangled by PowerShell splatting / Windows CMD parsing
function Build-AppSettingsJson($extraSettings) {
  $all = [ordered]@{
    "APP_ENV"                               = "production"
    "DEBUG"                                 = "false"
    "LOG_LEVEL"                             = "WARNING"
    "LOG_FORMAT"                            = "json"
    "JWT_ALGORITHM"                         = "HS256"
    "ACCESS_TOKEN_EXPIRE_MINUTES"           = "480"
    "AZURE_TENANT_ID"                       = "588cadf4-9902-4465-86c0-8bcf04f4f102"
    "AZURE_CLIENT_ID"                       = "7ed650f2-28d9-4c03-b660-2fe0bbb98434"
    "AZURE_PUBLIC_CLIENT"                   = "false"
    "GRAPH_SCOPES"                          = "openid profile offline_access User.Read Mail.Read Files.ReadWrite Tasks.ReadWrite Group.Read.All Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Read.All"
    "AZURE_OPENAI_ENDPOINT"                 = "https://AI-FOUNDRY-MAIN-001.cognitiveservices.azure.com"
    "AZURE_OPENAI_DEPLOYMENT_GPT52"         = "gpt-5.2-chat"
    "AZURE_OPENAI_API_VERSION"              = "2024-05-01-preview"
    "AZURE_BLOB_CONTAINER"                  = "taskradar-attachments"
    "QUEUE_PROVIDER"                        = "servicebus"
    "AZURE_SERVICE_BUS_QUEUE"               = "scan-jobs"
    "KEY_VAULT_URL"                         = $KV_URI
    "ENABLE_TEAMS_SCAN"                     = "false"
    "ENABLE_EXCEL_SYNC"                     = "true"
    "ENABLE_PLANNER_SYNC"                   = "true"
    "ENABLE_MCP_SERVER"                     = "true"
    "ENABLE_REALTIME_WEBHOOKS"              = "false"
    "COOKIE_SECURE"                         = "true"
    "COOKIE_SAMESITE"                       = "lax"
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = $AI_CS
    "SECRET_KEY"                            = (&$kvRef "SECRET-KEY")
    "JWT_SECRET"                            = (&$kvRef "JWT-SECRET")
    "TOKEN_ENCRYPTION_KEY"                  = (&$kvRef "TOKEN-ENCRYPTION-KEY")
    "AZURE_CLIENT_SECRET"                   = (&$kvRef "AZURE-CLIENT-SECRET")
    "AZURE_OPENAI_API_KEY"                  = (&$kvRef "AZURE-OPENAI-API-KEY")
    "DATABASE_URL"                          = (&$kvRef "DATABASE-URL")
    "AZURE_BLOB_CONNECTION_STRING"          = (&$kvRef "AZURE-BLOB-CONNECTION-STRING")
    "AZURE_SERVICE_BUS_CONNECTION_STRING"   = (&$kvRef "SERVICE-BUS-CONNECTION-STRING")
  }
  foreach ($kv in $extraSettings.GetEnumerator()) { $all[$kv.Key] = $kv.Value }
  $jsonArray = $all.GetEnumerator() | ForEach-Object {
    [ordered]@{ name = $_.Key; value = $_.Value; slotSetting = $false }
  }
  $tmpJson = [System.IO.Path]::GetTempFileName() + ".json"
  $jsonArray | ConvertTo-Json -Depth 3 | Set-Content $tmpJson -Encoding UTF8
  return $tmpJson
}

$apiJsonFile = Build-AppSettingsJson @{
  "FRONTEND_URL"           = $WEB_URL
  "BACKEND_URL"            = $API_URL
  "MCP_SERVER_URL"         = $MCP_URL
  "MICROSOFT_REDIRECT_URI" = "$API_URL/api/auth/microsoft/callback"
  "WEBSITES_PORT"          = "8000"
}
$beJsonFile = Build-AppSettingsJson @{
  "BACKEND_URL"            = $API_URL
  "FRONTEND_URL"           = $WEB_URL
  "MICROSOFT_REDIRECT_URI" = "$API_URL/api/auth/microsoft/callback"
}

foreach ($app in @($API_NAME, $WORKER_NAME, $SCHED_NAME, $MCP_NAME)) {
  $jsonFile = if ($app -eq $API_NAME) { $apiJsonFile } else { $beJsonFile }
  az webapp config appsettings set `
    --name $app `
    --resource-group $RESOURCE_GROUP `
    --settings "@$jsonFile" `
    --output none
  Info "  Configured: $app"
}
Remove-Item $apiJsonFile,$beJsonFile -Force -ErrorAction SilentlyContinue
OK "Backend app settings configured"

# ── 7. Build images via ACR Tasks (no local Docker required) ─
Step 7 "Building and pushing Docker images via ACR Tasks (cloud build)"

# Set Windows console code page to UTF-8 so colorama doesn't crash on non-cp1252 chars
$null = chcp 65001
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Grant the deployer AcrPush so az acr build can push
$acrId = "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME"
az role assignment create `
  --role "AcrPush" `
  --assignee-object-id $deployerOid `
  --assignee-principal-type User `
  --scope $acrId `
  --output none 2>$null
Info "AcrPush role ensured for deployer"
Start-Sleep -Seconds 10

# Helper: submit ACR build with --no-wait, fetch runId via list-runs, then poll
function Invoke-AcrBuild($Image, $ContextPath, $BuildArgs = @()) {
  $extraArgs = $BuildArgs | ForEach-Object { "--build-arg", $_ }
  # Note: --no-wait returns no useful output on this CLI version; ignore it
  az acr build `
    --registry $ACR_NAME `
    --subscription $SUBSCRIPTION_ID `
    --image $Image `
    --platform linux/amd64 `
    --no-wait `
    @extraArgs `
    $ContextPath *>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Failed to queue ACR build for $Image" }
  # Give ACR a moment to register the run, then grab the newest runId
  Start-Sleep -Seconds 5
  $runId = az acr task list-runs --registry $ACR_NAME `
    --query "[0].runId" -o tsv 2>$null
  if (-not $runId) { throw "Could not get runId for $Image build" }
  Info "  Build queued: $runId — polling for completion..."
  $deadline = (Get-Date).AddMinutes(30)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 20
    $status = az acr task list-runs --registry $ACR_NAME `
      --query "[?runId=='$runId'].status | [0]" -o tsv 2>$null
    Info "  $runId status: $status"
    if ($status -eq "Succeeded") { return }
    if ($status -in @("Failed","Error","Canceled")) { throw "ACR build $runId $status for $Image" }
  }
  throw "ACR build $runId timed out for $Image"
}

# API image (also used for worker, scheduler, mcp) — skip if already present
$apiTagExists = az acr repository show-tags --name $ACR_NAME --repository "melatr-api" --query "[?@=='latest']" -o tsv 2>$null
if ($apiTagExists -eq "latest") {
  Info "melatr-api:latest already in ACR — skipping build"
} else {
  Invoke-AcrBuild "melatr-api:latest" "./apps/api"
  OK "API image pushed"
}

# Web image (bakes in NEXT_PUBLIC_* at build time)
$webTagExists = az acr repository show-tags --name $ACR_NAME --repository "melatr-web" --query "[?@=='latest']" -o tsv 2>$null
if ($webTagExists -eq "latest") {
  Info "melatr-web:latest already in ACR — skipping build"
} else {
  Invoke-AcrBuild "melatr-web:latest" "./apps/web" @(
    "NEXT_PUBLIC_API_URL=$API_URL",
    "NEXT_PUBLIC_MCP_URL=$MCP_URL"
  )
  OK "Web image pushed"
}

# ── 8. Switch App Services to Docker container mode ───────────
Step 8 "Switching App Services to Docker container mode"

$backendImage = "${ACR_SERVER}/melatr-api:latest"
$webImage     = "${ACR_SERVER}/melatr-web:latest"

foreach ($pair in @(
  @{ Name=$API_NAME;    Image=$backendImage; Cmd="gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --workers 2 --timeout 120" }
  @{ Name=$WORKER_NAME; Image=$backendImage; Cmd="python -m app.workers.worker" }
  @{ Name=$SCHED_NAME;  Image=$backendImage; Cmd="python -m app.scheduler.scheduler" }
  @{ Name=$MCP_NAME;    Image=$backendImage; Cmd="python -m app.mcp.server" }
  @{ Name=$WEB_NAME;    Image=$webImage;     Cmd="" }
)) {
  az webapp config container set `
    --name $pair.Name `
    --resource-group $RESOURCE_GROUP `
    --docker-custom-image-name $pair.Image `
    --docker-registry-server-url "https://$ACR_SERVER" `
    --output none

  if ($pair.Cmd) {
    az webapp config set `
      --name $pair.Name `
      --resource-group $RESOURCE_GROUP `
      --startup-file $pair.Cmd `
      --output none
  }
  Info "  Container set: $($pair.Name)"
}
OK "All apps switched to Docker mode"

# ── 8b. Enable Managed Identity ACR pull on all apps ─────────
Step "8b" "Enabling acrUseManagedIdentityCreds on all App Services"
$acrId = "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.ContainerRegistry/registries/$ACR_NAME"
foreach ($app in @($API_NAME, $WORKER_NAME, $SCHED_NAME, $MCP_NAME, $WEB_NAME)) {
  $principalId = az webapp identity show --name $app --resource-group $RESOURCE_GROUP --query principalId -o tsv 2>$null
  if (-not $principalId -or $principalId -eq "None") {
    $principalId = az webapp identity assign --name $app --resource-group $RESOURCE_GROUP --query principalId -o tsv
    Start-Sleep -Seconds 5
  }
  az role assignment create --role "AcrPull" --assignee-object-id $principalId `
    --assignee-principal-type ServicePrincipal --scope $acrId --output none 2>$null
  $url = "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Web/sites/$app/config/web?api-version=2022-09-01"
  az rest --method PATCH --url $url --headers "Content-Type=application/json" `
    --body '{"properties":{"acrUseManagedIdentityCreds":true}}' --output none
  Info "  MI ACR pull enabled: $app"
}
OK "Managed Identity ACR pull configured"

# ── 9. Run database migrations ───────────────────────────────
Step 9 "Running Alembic migrations against $SQL_SERVER / $SQL_DATABASE"

$venvPython = if (Test-Path "apps/api/.venv/Scripts/python.exe") {
  (Resolve-Path "apps/api/.venv/Scripts/python.exe").Path  # absolute so Push-Location doesn't break it
} else { "python" }

# Add current IP to the armely SQL server firewall, run migrations, then remove
$myIP = (Invoke-RestMethod -Uri "https://api.ipify.org")
$firewallRule = "deploy-$(Get-Date -Format 'yyyyMMddHHmm')"
Info "Adding IP $myIP to armely SQL server firewall..."
az sql server firewall-rule create `
  --resource-group "Server" `
  --server "armely" `
  --name $firewallRule `
  --start-ip-address $myIP `
  --end-ip-address $myIP `
  --output none
Info "Waiting 45s for firewall rule to propagate..."
Start-Sleep -Seconds 45

try {
  $env:DATABASE_URL = $DB_URL
  # Ensure alembic_version table has a wide-enough version_num column (SQL Server
  # default is VARCHAR(32) but migration IDs can exceed 32 chars)
  & $venvPython -c @"
import pyodbc, os, urllib.parse
raw_url = os.environ['DATABASE_URL']
# Extract odbc_connect param
odbc_cs = urllib.parse.unquote(raw_url.split('odbc_connect=')[1])
conn = pyodbc.connect(odbc_cs, timeout=15, autocommit=True)
cur = conn.cursor()
cur.execute('''
IF OBJECT_ID(N'dbo.alembic_version') IS NULL
    CREATE TABLE dbo.alembic_version (version_num NVARCHAR(256) NOT NULL, CONSTRAINT pk_alembic_version PRIMARY KEY (version_num))
ELSE BEGIN
    IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.alembic_version') AND name='version_num' AND max_length < 256)
        ALTER TABLE dbo.alembic_version ALTER COLUMN version_num NVARCHAR(256) NOT NULL
END
''')
conn.close()
print('alembic_version table ready')
"@
  Push-Location "apps/api"
  & $venvPython -m alembic upgrade head
  if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Alembic migrations failed" }
  Pop-Location
  OK "Migrations applied"
} finally {
  az sql server firewall-rule delete `
    --resource-group "Server" `
    --server "armely" `
    --name $firewallRule `
    --output none 2>$null
  Info "Removed deploy IP from SQL firewall"
}

# ── 10. Restart all apps ──────────────────────────────────────
Step 10 "Restarting all App Services"
foreach ($app in @($API_NAME, $WORKER_NAME, $SCHED_NAME, $MCP_NAME, $WEB_NAME)) {
  az webapp restart --name $app --resource-group $RESOURCE_GROUP --output none
  Info "  Restarted: $app"
}
OK "All apps restarted"

# ── Done ──────────────────────────────────────────────────────
Write-Host "`n╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host   "║  Mela Task Radar — Production Deployment Complete    ║" -ForegroundColor Green
Write-Host   "╠══════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host   "║  API:  $API_URL" -ForegroundColor Green
Write-Host   "║  MCP:  $MCP_URL" -ForegroundColor Green
Write-Host   "║  Web:  $WEB_URL" -ForegroundColor Green
Write-Host   "╠══════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host   "║  NEXT: Add redirect URI in Azure AD app registration:" -ForegroundColor Yellow
Write-Host   "║  $API_URL/api/auth/microsoft/callback" -ForegroundColor Yellow
Write-Host   "╚══════════════════════════════════════════════════════╝" -ForegroundColor Green
