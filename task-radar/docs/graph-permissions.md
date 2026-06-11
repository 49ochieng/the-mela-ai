# Microsoft Graph permissions

All permissions are **delegated** in MVP.

## Base
- `openid`, `profile`, `offline_access`
- `User.Read`

## Outlook
- `Mail.Read`

## OneDrive / Excel
- `Files.ReadWrite`

## Planner
- `Tasks.ReadWrite`
- `Group.Read.All` (admin consent)

## Teams (admin consent recommended)
- `Team.ReadBasic.All`
- `Channel.ReadBasic.All`
- `ChannelMessage.Read.All`

## Entra app registration

1. Azure Portal → Entra ID → App registrations → **New registration**
   - Name: `Mela Task Radar`
   - Redirect URI: `http://localhost:8000/api/auth/microsoft/callback` (Web)
2. Certificates & secrets → **New client secret** → copy value into `AZURE_CLIENT_SECRET`.
3. API permissions → add the scopes above. Click **Grant admin consent** for Teams + Group scopes.
4. Expose an API → set Application ID URI `api://<client-id>` and add scope `access_as_user`.

Production redirect URI:
`https://<your-app>.azurewebsites.net/api/auth/microsoft/callback`
