/**
 * Mela AI - Chat Layout (Auth Guard)
 *
 * Wraps all /chat routes. Redirects unauthenticated users to the login page.
 * Handles both MSAL (Entra ID) and dev-mode auth.
 * Shows a spinner while MSAL is initialising so there is no flash of
 * unauthenticated content.
 *
 * Phase 5A: also owns the orchestration event-stream lifecycle — one
 * SSE connection per session (NOT per conversation).  Reconnects with
 * 5-second exponential backoff on non-user-initiated disconnect.
 */

'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { InteractionStatus } from '@azure/msal-browser';
import { Loader2 } from 'lucide-react';
import { api, type WorkerEventChunk } from '@/lib/api';
import { useChatStore } from '@/lib/store';

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const isDevAuth = api.isDevAuthenticated();

  // MSAL is still resolving cached accounts — do not redirect yet.
  const isInitialising =
    inProgress === InteractionStatus.Startup ||
    inProgress === InteractionStatus.HandleRedirect;

  const isAuthorized = isAuthenticated || isDevAuth;

  useEffect(() => {
    if (!isInitialising && !isAuthorized) {
      router.replace('/');
    }
  }, [isInitialising, isAuthorized, router]);

  // Phase 5A: orchestration event stream lifecycle.
  // - Single connection per session (mounts when authorised, unmounts on
  //   logout / nav away).
  // - Reconnects with exponential backoff (5s → 10s → 20s, capped at
  //   60s) on non-AbortError disconnect.  AbortError is user-initiated
  //   teardown — never reconnect.
  // - Heartbeats are filtered server-side already produce a chunk; we
  //   simply ignore type='heartbeat' here.
  useEffect(() => {
    if (!isAuthorized) return;
    const controller = new AbortController();
    let stopped = false;
    let attempt = 0;

    const pushWorkerEvent = useChatStore.getState().pushWorkerEvent;

    async function loop() {
      while (!stopped && !controller.signal.aborted) {
        try {
          for await (const chunk of api.streamWorkerEvents(controller.signal)) {
            if (chunk.type === 'heartbeat') continue;
            if (chunk.type === 'worker_event' && chunk.data) {
              pushWorkerEvent(chunk.data as WorkerEventChunk);
            }
          }
          // Stream closed cleanly without abort → server side drop.  Try again.
          attempt += 1;
        } catch (err: unknown) {
          if ((err as { name?: string })?.name === 'AbortError') return;
          attempt += 1;
        }
        if (stopped || controller.signal.aborted) return;
        const delay = Math.min(60_000, 5_000 * Math.pow(2, attempt - 1));
        await new Promise((r) => setTimeout(r, delay));
      }
    }
    void loop();

    return () => {
      stopped = true;
      controller.abort();
    };
  }, [isAuthorized]);

  if (isInitialising) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!isAuthorized) {
    // Redirecting — render nothing to avoid flash of protected content.
    return null;
  }

  return <>{children}</>;
}
