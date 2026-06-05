'use client';

/**
 * Mela AI - Embed Page (Phase 6B)
 *
 * Minimal full-screen Mela chat designed to live inside an iframe.
 * Authentication is via the embed token in the URL — NOT MSAL.  The
 * embedding app authenticated the user; Mela trusts the token.
 *
 * Excludes everything that would conflict with being framed: no
 * MSAL provider, no sidebar, no profile switcher, no admin links.
 */

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import { EmbedChatInterface } from '@/components/embed/EmbedChatInterface';

function EmbedInner() {
  const params = useSearchParams();
  const token = params.get('token') || '';

  if (!token) {
    return (
      <div className="flex items-center justify-center h-screen text-sm text-red-500">
        Missing embed token.
      </div>
    );
  }
  return (
    <div className="h-screen w-screen">
      <EmbedChatInterface token={token} />
    </div>
  );
}

export default function EmbedPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-screen">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
        </div>
      }
    >
      <EmbedInner />
    </Suspense>
  );
}
