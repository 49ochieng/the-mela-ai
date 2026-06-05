/**
 * Mela AI - Projects Layout (Auth Guard)
 *
 * Wraps all /projects routes. Redirects unauthenticated users to the login page.
 * Handles both MSAL (Entra ID) and dev-mode auth.
 */

'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { InteractionStatus } from '@azure/msal-browser';
import { Loader2 } from 'lucide-react';
import { api } from '@/lib/api';

export default function ProjectsLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const isDevAuth = api.isDevAuthenticated();

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
    return null;
  }

  return <>{children}</>;
}
