# Mela AI — Enterprise AI Assistant

**FastAPI + Next.js 14** — Azure AI, SharePoint, OneDrive, Speech, DALL-E, and more.

Live: [armely-ai-web.azurewebsites.net](https://armely-ai-web.azurewebsites.net) | API health: [armely-ai-api.azurewebsites.net/health](https://armely-ai-api.azurewebsites.net/health)

---

## Local development

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Node.js | 20 LTS |

### 1. Configure env files

```bash
# Backend — copy sample and fill in values
cp env/.env.dev.sample env/.env.local

# Frontend — copy sample and fill in values
cp frontend/.env.example frontend/.env.local
```

Minimum required for local dev (SQLite, dev login):

```env
# backend env/.env.local
JWT_SECRET_KEY=any-random-string-here
AI_FOUNDRY_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
AI_FOUNDRY_API_KEY=<your-key>
DEV_USERNAME=dev
DEV_PASSWORD=dev

# frontend frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_ENABLE_DEV_LOGIN=true
NEXT_PUBLIC_DEV_USERNAME=dev
NEXT_PUBLIC_DEV_PASSWORD=dev
```

### 2. Start the backend

```bash
cd backend
python -m venv venv
source venv/Scripts/activate        # Windows
# source venv/bin/activate          # Linux / macOS
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend health: <http://localhost:8000/health>

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:3005
```

---

## Required environment variables

### Backend

| Variable | Required | Secret | Description |
|----------|----------|--------|-------------|
| `JWT_SECRET_KEY` | Yes | Yes | Token signing secret |
| `AI_FOUNDRY_ENDPOINT` | Yes | No | Azure AI / OpenAI endpoint |
| `AI_FOUNDRY_API_KEY` | Yes | Yes | Azure AI API key |
| `AZURE_TENANT_ID` | For SSO | No | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | For SSO | No | App registration client ID |
| `AZURE_CLIENT_SECRET` | For SSO | Yes | App registration secret |
| `AZURE_SEARCH_ENDPOINT` | For RAG | No | Azure AI Search URL |
| `AZURE_SEARCH_ADMIN_KEY` | For RAG | Yes | AI Search admin key |
| `AZURE_SPEECH_KEY` | For voice | Yes | Speech service key |
| `AZURE_SPEECH_REGION` | For voice | No | Speech region (default: `eastus`) |
| `AZURE_DALLE_ENDPOINT` | For images | No | DALL-E endpoint |
| `AZURE_DALLE_API_KEY` | For images | Yes | DALL-E API key |
| `SHAREPOINT_SITES` | For RAG | No | Comma-separated SharePoint site URLs |
| `ONEDRIVE_ROOT` | For RAG | No | OneDrive root URL |
| `ORG_WEBSITE_ALLOWLIST` | For RAG | No | Domains to crawl (e.g. `armely.com`) |
| `DATABASE_URL` | Prod only | Yes | Azure SQL connection string |
| `DEV_USERNAME` / `DEV_PASSWORD` | Dev only | No/Yes | Dev login credentials |

### Frontend

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Yes | Backend URL (e.g. `http://localhost:8000`) |
| `NEXT_PUBLIC_AZURE_AD_CLIENT_ID` | For SSO | Azure AD client ID |
| `NEXT_PUBLIC_AZURE_AD_TENANT_ID` | For SSO | Azure AD tenant ID |
| `NEXT_PUBLIC_API_SCOPE` | For SSO | `api://<client-id>/access_as_user` |

---

## Azure App Registration — required for Microsoft login

Microsoft Entra ID (Azure AD) requires every redirect URI to be explicitly registered. If a URI is missing, login will fail with an `AADSTS50011` error.

### Redirect URIs to register

Go to: **Azure portal → App registrations → [your app] → Authentication → Web → Redirect URIs**

| Environment | URI to add |
|-------------|-----------|
| Local dev | `http://localhost:3005` |
| Production (current) | `https://armely-ai-web.azurewebsites.net` |
| Production (future) | `https://mela.armely.com` |

Add all three so switching domains does not break existing sessions.

Also set **Front-channel logout URL** to `https://armely-ai-web.azurewebsites.net` (or your active production domain).

### API scope (for backend token validation)

Go to: **App registrations → [your app] → Expose an API**

1. Set **Application ID URI** to `api://<client-id>`
2. Add a scope named `access_as_user` — Admins and users: **enabled**

The full scope string used in MSAL and the backend is: `api://<client-id>/access_as_user`

### Required GitHub Variable for domain switch

When switching to `https://mela.armely.com`:

1. Add the new URI to the Azure App Registration (see table above)
2. Set the GitHub Variable `FRONTEND_URL=https://mela.armely.com` in your repo
3. Re-run the CD workflow — the new URL is baked into the Next.js build automatically

> **No code changes needed.** The CD pipeline reads `vars.FRONTEND_URL` and falls back to the `.azurewebsites.net` URL until the variable is set.

---

## Knowledge sources (production)

| Source type | URL / Domain |
|-------------|--------------|
| SharePoint | `https://armely.sharepoint.com/sites/Test-team` |
| SharePoint | `https://armely.sharepoint.com/ZapManufacturing` |
| SharePoint | `https://armely.sharepoint.com/sites/LearningResources` |
| SharePoint | `https://armely.sharepoint.com/sites/ArmelyLLC` |
| OneDrive | `https://armely-my.sharepoint.com/` |
| Website | `armely.com` (crawl depth 3) |

All answers that depend on retrieved documents include citations. If retrieval returns nothing relevant, the assistant says so — it never fabricates sources.

---

## Run tests

### Backend

```bash
cd backend
source venv/Scripts/activate
pytest tests/ -v --tb=short
```

### Frontend

```bash
cd frontend
npm test               # unit tests (Jest)
npm run test:coverage  # with coverage
```

---

## Lint and formatting

### Backend

```bash
cd backend
pip install ruff black
ruff check app/          # lint — same rules as CI (E, F, W; ignores E501)
black app/               # format
```

### Frontend

```bash
cd frontend
npm run lint             # ESLint — same as CI (must pass)
npx tsc --noEmit         # TypeScript type check — same as CI (must pass)
npx prettier --write .   # format
```

CI fails if lint, type-check, or tests fail.

---

## Deployment

### CI (every PR and push except main)

`.github/workflows/ci.yml` runs:

1. Python lint (`ruff`) — **blocking**
2. Python tests (`pytest`) — **blocking**
3. Next.js lint + type check — **blocking**
4. Next.js build
5. Security scan (Gitleaks)

### CD (push to `main` or manual dispatch)

`.github/workflows/cd.yml` runs:

1. Build backend ZIP + frontend standalone ZIP
2. Provision infrastructure via Bicep (`infra/main.bicep`)
3. Write secrets to Azure Key Vault
4. Deploy backend via Kudu ZipDeploy (with SCM retry)
5. Deploy frontend via Azure OneDeploy
6. Health check gate — fails workflow if backend does not return HTTP 200

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `JWT_SECRET_KEY` | JWT signing secret |
| `AZURE_CLIENT_SECRET` | Azure AD client secret |
| `AI_FOUNDRY_API_KEY` | Azure AI API key |
| `AZURE_SPEECH_KEY` | Speech service key |
| `AZURE_DALLE_API_KEY` | DALL-E API key |
| `AZURE_SEARCH_ADMIN_KEY` | AI Search admin key |
| `AZURE_STORAGE_ACCOUNT_KEY` | Storage key |
| `DATABASE_URL` | Azure SQL connection string |
| `DEV_PASSWORD` | Dev login password |

### Required GitHub Variables

| Variable | Description |
|----------|-------------|
| `AZURE_CLIENT_ID` | Azure AD client ID (OIDC federated) |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID |
| `AZURE_AD_CLIENT_ID` | App registration client ID |
| `AI_FOUNDRY_ENDPOINT` | Azure AI Foundry endpoint |
| `FRONTEND_URL` | *(optional)* Override frontend URL — e.g. `https://mela.armely.com`. Defaults to `https://armely-ai-web.azurewebsites.net` |
| `NEXT_PUBLIC_API_SCOPE` | `api://<client-id>/access_as_user` |
| `DEV_USERNAME` | Dev login username (default: `dev`) |

---

## Secrets management

```
Local dev    → env/.env.local         (gitignored, never committed)
GitHub CI    → Repository Secrets     (encrypted)
                  ↓ cd.yml writes to Key Vault post-Bicep
Azure KV     → kv-mela-mcpp           (Azure-managed)
                  ↓ App Setting KV reference
App Service  → injected as env var    (resolved at runtime)
```

---

## Architecture

```
frontend (Next.js 14, port 3005)
  │  SSE streaming / REST API
  ▼
backend (FastAPI, port 8000)
  ├── chat_service.py          — orchestration, tool dispatch, RAG
  ├── openai_service.py        — Azure OpenAI / AI Foundry, retry + model fallback
  ├── search/
  │   ├── query_pipeline.py    — hybrid search, ACL filter, SourceRecord citation schema
  │   └── index_manager.py     — Azure AI Search index lifecycle
  ├── connectors/
  │   ├── sharepoint.py        — Graph delta sync (4 SharePoint sites)
  │   ├── onedrive.py          — user OneDrive (delegated token)
  │   └── org_website.py       — armely.com crawler
  ├── speech_service.py        — Azure Speech STT + TTS (with citation text cleanup)
  ├── dalle_service.py         — DALL-E 3 image generation
  └── code_interpreter_service.py — Python sandbox, returns xlsx/docx/pdf/csv
```

---

## View logs

```bash
# Backend logs (Azure)
az webapp log tail -n armely-ai-api -g EdgarO_RG_MCPP_WU2

# Frontend logs (Azure)
az webapp log tail -n armely-ai-web -g EdgarO_RG_MCPP_WU2

# Local backend log
tail -f /tmp/backend.log
```
