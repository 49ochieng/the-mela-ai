/**
 * Mela AI - Admin Layout (Auth Guard)
 *
 * Wraps all /admin routes. Redirects unauthenticated users to the login page.
 * Handles both MSAL (Entra ID) and dev-mode auth.
 * Shows a spinner while MSAL is initialising so there is no flash of
 * unauthenticated content or premature redirects.
 */

'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { InteractionStatus } from '@azure/msal-browser';
import { Loader2 } from 'lucide-react';
import { api } from '@/lib/api';

export default function AdminLayout({ children }: { children: React.ReactNode }) {
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
