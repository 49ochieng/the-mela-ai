/**
 * Mela AI — Microsoft Graph delegated utilities
 *
 * Two responsibilities:
 *  1. Fetch the signed-in user's profile from /me (used on login).
 *  2. Acquire delegated Graph tokens for productivity scopes so the
 *     backend can use them via the OBO flow (mail, calendar, tasks).
 *
 * Token acquisition strategy for all functions:
 *   1. Silent (MSAL cache) — preferred, no user interaction.
 *   2. Popup — fallback when MFA or incremental consent is required.
 *   3. Returns null on any unrecoverable error so callers degrade gracefully.
 *
 * Tokens are used in-memory only; never written to localStorage.
 * NEVER log access tokens — only log display names or boolean success.
 */

import {
  IPublicClientApplication,
  InteractionRequiredAuthError,
} from '@azure/msal-browser';

const GRAPH_ME_ENDPOINT = 'https://graph.microsoft.com/v1.0/me';

/** Scopes for reading the signed-in user's profile. */
const GRAPH_ME_SCOPES = ['User.Read'];

/**
 * Delegated scopes required for productivity features
 * (email, calendar, Planner / To Do).
 *
 * These must be added as delegated permissions on the auth app
 * registration (ENTRA_AUTH_CLIENT_ID) in the Azure portal, and
 * admin consent must be granted before silent acquisition works.
 */
export const GRAPH_PRODUCTIVITY_SCOPES = [
  'Mail.Read',
  'Mail.Send',
  'Calendars.ReadWrite',
  'Tasks.ReadWrite',
];

// ── Types ─────────────────────────────────────────────────────────────────────

export interface GraphUserProfile {
  /** Entra Object ID — stable, immutable user identifier. */
  id: string;
  displayName: string | null;
  /**
   * Primary SMTP address. May be null for some synced accounts.
   * Use userPrincipalName as the safe fallback.
   */
  mail: string | null;
  userPrincipalName: string;
  givenName: string | null;
  surname: string | null;
  jobTitle: string | null;
  department: string | null;
  photoUrl: string | null;
}

// ── Core token helper ─────────────────────────────────────────────────────────

/**
 * Acquire a delegated Graph token for the given scopes.
 * Tries silent first, falls back to popup on InteractionRequiredAuthError.
 * Returns null on any failure.
 */
async function acquireGraphToken(
  msalInstance: IPublicClientApplication,
  scopes: string[],
): Promise<string | null> {
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length === 0) return null;

  try {
    const response = await msalInstance.acquireTokenSilent({
      scopes,
      account: accounts[0],
    });
    return response.accessToken;
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      try {
        const response = await msalInstance.acquireTokenPopup({
          scopes,
          account: accounts[0],
        });
        return response.accessToken;
      } catch (popupErr) {
        if (process.env.NODE_ENV === 'development') {
          console.warn('[graph] Popup token acquisition failed:', popupErr);
        }
        return null;
      }
    }
    if (process.env.NODE_ENV === 'development') {
      console.warn('[graph] Silent token acquisition failed:', err);
    }
    return null;
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Fetch the signed-in user's profile from Graph /me.
 * Used on login to populate the user object with real profile data.
 */
export async function fetchGraphMe(
  msalInstance: IPublicClientApplication,
): Promise<GraphUserProfile | null> {
  const accessToken = await acquireGraphToken(msalInstance, GRAPH_ME_SCOPES);
  if (!accessToken) return null;

  try {
    const response = await fetch(GRAPH_ME_ENDPOINT, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (!response.ok) {
      if (process.env.NODE_ENV === 'development') {
        console.error('[graph] /me returned', response.status, response.statusText);
      }
      return null;
    }

    const data = await response.json();

    if (process.env.NODE_ENV === 'development') {
      // Log display name only — never log the raw token or full profile.
      console.info('[graph] /me success — displayName:', data.displayName);
    }

    return {
      id: data.id ?? '',
      displayName: data.displayName ?? null,
      mail: data.mail ?? null,
      userPrincipalName: data.userPrincipalName ?? '',
      givenName: data.givenName ?? null,
      surname: data.surname ?? null,
      jobTitle: data.jobTitle ?? null,
      department: data.department ?? null,
      photoUrl: null,
    };
  } catch (err) {
    if (process.env.NODE_ENV === 'development') {
      console.error('[graph] /me fetch error:', err);
    }
    return null;
  }
}

/**
 * Acquire a delegated Graph token for mail + calendar + tasks scopes.
 *
 * The backend uses this token via OBO to call /me/sendMail,
 * /me/events, /me/planner/tasks, etc. on behalf of the signed-in user.
 *
 * Returns null if the user has not yet consented to these scopes
 * (caller should surface a consent prompt in that case).
 */
export async function acquireProductivityToken(
  msalInstance: IPublicClientApplication,
): Promise<string | null> {
  const token = await acquireGraphToken(msalInstance, GRAPH_PRODUCTIVITY_SCOPES);
  if (process.env.NODE_ENV === 'development') {
    console.info('[graph] productivity token acquired:', token !== null);
  }
  return token;
}

/**
 * Returns the best available email address for a Graph profile.
 * Prefers `mail`; falls back to `userPrincipalName` which is always present.
 */
export function resolveEmail(profile: GraphUserProfile): string {
  return profile.mail ?? profile.userPrincipalName;
}
