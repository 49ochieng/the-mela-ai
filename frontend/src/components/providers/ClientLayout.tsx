/**
 * ClientLayout — client-side provider shell for the App Router root layout.
 *
 * Why this file exists:
 *   Next.js 14 App Router SSR's every 'use client' component for the initial
 *   HTML, including our MsalProvider.  MSAL Browser v3 calls initialize()
 *   which accesses __PRIVATE_NEXTJS_INTERNALS_TREE — a navigation-related
 *   Next.js internal that is null during SSR hydration.  This crashes with:
 *   "Cannot read properties of null (reading '__PRIVATE_NEXTJS_INTERNALS_TREE')"
 *
 *   The only reliable fix is next/dynamic with { ssr: false }, which must be
 *   called from a 'use client' component (not a Server Component).  This file
 *   is that client-side boundary.  MsalProvider is never rendered on the server.
 */

'use client';

import dynamic from 'next/dynamic';
import { ReactNode } from 'react';
import { ThemeProvider } from './ThemeProvider';
import { Toaster } from '@/components/ui/Toaster';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';

// MsalProvider is excluded from SSR entirely. The loading fallback matches the
// spinner shown inside MsalProvider itself while MSAL initialises in the browser.
const MsalProvider = dynamic(
  () => import('./MsalProvider').then((m) => ({ default: m.MsalProvider })),
  {
    ssr: false,
    loading: () => (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-primary border-t-transparent" />
      </div>
    ),
  },
);

const AppCrashFallback = () => (
  <div className="min-h-screen flex items-center justify-center bg-gray-50">
    <div className="text-center max-w-sm px-4">
      <p className="text-xl font-semibold text-gray-800 mb-2">Something went wrong</p>
      <p className="text-sm text-gray-500 mb-4">Reload the page to continue.</p>
      <button
        onClick={() => window.location.reload()}
        className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700"
      >
        Reload
      </button>
    </div>
  </div>
);

export function ClientLayout({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary fallback={<AppCrashFallback />}>
      <MsalProvider>
        <ThemeProvider
          attribute="class"
          defaultTheme="light"
          enableSystem
          disableTransitionOnChange
        >
          {children}
          <Toaster />
        </ThemeProvider>
      </MsalProvider>
    </ErrorBoundary>
  );
}
