/**
 * Mela AI - MSAL Provider
 *
 * KEY INVARIANT: MsalProviderBase must never render until initialize() has
 * fully resolved.  If handleRedirectPromise() is called on a partially-
 * initialized instance (which can happen in React 18 StrictMode when the
 * second effect-invocation races the first's async initialize()), MSAL's
 * internal state machine is inconsistent and the redirect fails with
 * msal:loginFailure even though the auth code came back from Microsoft fine.
 *
 * How the singleton works:
 *   - _msalInstance  created synchronously on first effect invocation.
 *   - _initPromise   tracks the initialize() + post-init promise.
 *   - StrictMode second invocation finds _initPromise already set and
 *     attaches a .then() to the SAME promise — does NOT create a new instance.
 *   - MsalProviderBase only renders after isReady = true, which only fires
 *     inside the .then() of _initPromise (i.e., after initialize() resolves).
 *
 * Next.js 14 SSR notes:
 *   - This file is loaded by ClientLayout.tsx via next/dynamic { ssr: false }.
 *   - It is therefore never evaluated on the server; window/localStorage are safe.
 *   - We must NOT call handleRedirectPromise() ourselves — MsalProviderBase does
 *     it through its own useEffect at the correct lifecycle point.
 */

'use client';

import { ReactNode, useEffect, useState } from 'react';
import { MsalProvider as MsalProviderBase } from '@azure/msal-react';
import {
  PublicClientApplication,
  EventType,
  EventMessage,
  AuthenticationResult,
  AuthError,
  IPublicClientApplication,
} from '@azure/msal-browser';
import { msalConfig } from '@/lib/msal/config';

// ── Module-level singleton ────────────────────────────────────────────────────
// Only ONE PublicClientApplication is created for the lifetime of the tab.
// _initPromise guards against concurrent initialization (StrictMode, HMR).

let _msalInstance: PublicClientApplication | null = null;
let _initPromise: Promise<void> | null = null;

/** Access the MSAL instance from outside React (e.g., api.ts). */
export function getMsalInstance(): IPublicClientApplication | null {
  return _msalInstance;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function MsalProvider({ children }: { children: ReactNode }) {
  // Start as "ready" only if a previous render already fully initialized MSAL.
  // This handles HMR reloads where _initPromise is truthy and already resolved.
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    // ── Already initializing (StrictMode second invocation, HMR) ──────────
    // Attach to the same in-flight promise so both invocations resolve together.
    if (_initPromise) {
      _initPromise.then(() => setIsReady(true)).catch(() => setIsReady(true));
      return;
    }

    // ── First invocation: create + initialize ─────────────────────────────
    const instance = new PublicClientApplication(msalConfig);
    _msalInstance = instance;

    _initPromise = instance
      .initialize()
      .then(() => {
        // Restore previously signed-in account so useIsAuthenticated() returns
        // true immediately on page load without waiting for a token refresh.
        const accounts = instance.getAllAccounts();
        if (accounts.length > 0 && !instance.getActiveAccount()) {
          instance.setActiveAccount(accounts[0]);
        }

        // Sync active account on every successful login (including redirects).
        instance.addEventCallback((event: EventMessage) => {
          if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
            const payload = event.payload as AuthenticationResult;
            instance.setActiveAccount(payload.account);
          }

          // Surface the exact MSAL error in the console — far easier to
          // diagnose than scrolling through hundreds of verbose log lines.
          if (event.eventType === EventType.LOGIN_FAILURE && event.error) {
            const err = event.error as AuthError;
            console.error(
              '[MsalProvider] ❌ msal:loginFailure',
              '\n  errorCode   :', err.errorCode,
              '\n  errorMessage:', err.errorMessage,
              '\n  subError    :', (err as any).subError,
              '\n  full error  :', err,
            );
          }
        });
      })
      .catch((err) => {
        // Non-fatal — show the login page anyway so the user can retry.
        console.error('[MsalProvider] initialize() threw:', err);
      });

    // Resolve isReady after initialize() settles (resolve or reject).
    _initPromise.then(() => setIsReady(true)).catch(() => setIsReady(true));

    // No cleanup: the singleton must persist across StrictMode unmount/remount.
  }, []); // Empty deps — runs once per browser session (after first paint).

  if (!isReady || !_msalInstance) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <MsalProviderBase instance={_msalInstance}>
      {children}
    </MsalProviderBase>
  );
}
