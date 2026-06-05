/**
 * Mela AI - MSAL Configuration
 * Microsoft Authentication Library configuration for Entra ID
 */

import { Configuration, LogLevel, BrowserCacheLocation } from '@azure/msal-browser';

// ── Auth / login app registration (new, dedicated to user sign-in) ───────────
// NEXT_PUBLIC_ENTRA_AUTH_CLIENT_ID = client ID of the login-only app registration.
// Falls back to NEXT_PUBLIC_AZURE_AD_CLIENT_ID so existing single-registration
// deployments keep working without any configuration change.
const clientId =
  process.env.NEXT_PUBLIC_ENTRA_AUTH_CLIENT_ID ||
  process.env.NEXT_PUBLIC_AZURE_AD_CLIENT_ID ||
  '';
const tenantId = process.env.NEXT_PUBLIC_AZURE_AD_TENANT_ID || '';
// Dev server runs on port 3005 (see frontend/package.json).
// NEXT_PUBLIC_REDIRECT_URI must be set to the deployed URL in production
// AND must match the redirect URI registered in the Azure portal exactly.
// For the SPA platform, register the origin only (no /auth/callback path).
const redirectUri = process.env.NEXT_PUBLIC_REDIRECT_URI || 'http://localhost:3005';

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri,
    postLogoutRedirectUri: redirectUri,
    // false: after processing the redirect, stay on the redirectUri page.
    // The landing page's useEffect detects isAuthenticated and pushes to /chat,
    // which is cleaner than MSAL doing its own history.replaceState navigation
    // that can race with the Next.js App Router.
    navigateToLoginRequestUrl: false,
  },
  cache: {
    // SessionStorage: tokens are cleared when the browser tab closes, which
    // limits exposure if a user forgets to log out on a shared machine.
    // (Backend session lifecycle still enforces idle/absolute timeouts.)
    cacheLocation: BrowserCacheLocation.SessionStorage,
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        switch (level) {
          case LogLevel.Error:
            console.error(message);
            break;
          case LogLevel.Warning:
            console.warn(message);
            break;
          case LogLevel.Info:
            if (process.env.NODE_ENV === 'development') {
              console.info(message);
            }
            break;
          case LogLevel.Verbose:
            if (process.env.NODE_ENV === 'development') {
              console.debug(message);
            }
            break;
        }
      },
      logLevel: process.env.NODE_ENV === 'development' ? LogLevel.Verbose : LogLevel.Error,
    },
    windowHashTimeout: 60000,
    iframeHashTimeout: 6000,
    loadFrameTimeout: 0,
  },
};

// Backend API scope — must match what's exposed on the LOGIN app registration in Azure portal:
// App registrations → <login-app-name> → Expose an API → add scope → access_as_user
// The scope URI uses the auth-app client ID: api://<ENTRA_AUTH_CLIENT_ID>/access_as_user
export const apiScope =
  process.env.NEXT_PUBLIC_API_SCOPE ||
  `api://${clientId}/access_as_user`;

// Request used to acquire the access token sent to our backend
export const backendTokenRequest = {
  scopes: [apiScope],
};

// Scopes for the initial login (ID token + user profile).
//
// IMPORTANT: Do NOT include apiScope here.
// Microsoft's token endpoint only accepts scopes from ONE resource per request.
// 'User.Read' belongs to Microsoft Graph (resource: https://graph.microsoft.com).
// apiScope belongs to this app (resource: api://<clientId>).
// Mixing them causes MSAL to attempt a multi-resource token exchange which fails
// with msal:loginFailure / AADSTS70011 (invalid scope).
//
// The backend API token is acquired separately by api.ts#getAccessToken() via
// acquireTokenSilent(backendTokenRequest) before each authenticated request.
// If silent acquisition requires interaction, it falls back to acquireTokenPopup.
export const loginRequest = {
  scopes: [
    'openid',
    'profile',
    'email',
    'User.Read',
  ],
};

// Graph API scopes (acquired separately when needed)
export const graphScopes = {
  mail: ['Mail.Send', 'Mail.ReadWrite'],
  calendar: ['Calendars.ReadWrite'],
  tasks: ['Tasks.ReadWrite'],
  sharepoint: ['Sites.Read.All'],
  teams: ['OnlineMeetings.ReadWrite'],
};
