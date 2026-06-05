'use client';

/**
 * Mela AI - Worker Event Bar
 *
 * Phase 5A: stacked banners showing live worker activity from the
 * orchestration brain.  Reads from the Zustand store's `workerEvents`
 * slice (populated by the SSE stream wired in chat/layout.tsx).
 *
 * UX rules (per spec):
 *  - Up to 3 banners visible at once; oldest drops off when a fourth
 *    arrives (the store enforces the cap, not this component).
 *  - Each banner auto-dismisses after 8 seconds.
 *  - Banner is purely informational — never blocks the chat input,
 *    never intercepts a message.
 *  - "View details" link opens MonitoringTab (admin) with the trace
 *    id in the URL hash; non-admins just dismiss.
 */

import { useEffect } from 'react';
import { useChatStore } from '@/lib/store';
import { Activity, X } from 'lucide-react';

const AUTO_DISMISS_MS = 8000;

export function WorkerEventBar() {
  const events = useChatStore((s) => s.workerEvents);
  const dismiss = useChatStore((s) => s.dismissWorkerEvent);

  // Schedule auto-dismiss timers per banner.  Each effect cleans its
  // own timer on unmount / when the banner is dismissed early.
  useEffect(() => {
    if (events.length === 0) return;
    const timers = events.map((e) =>
      window.setTimeout(() => dismiss(e.banner_id), AUTO_DISMISS_MS),
    );
    return () => {
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, [events, dismiss]);

  if (events.length === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none absolute bottom-full left-0 right-0 z-10 px-3 pb-2 space-y-1"
    >
      {events.map((e) => (
        <div
          key={e.banner_id}
          className="pointer-events-auto flex items-start gap-2 rounded-md border bg-card/95 backdrop-blur-sm px-3 py-2 shadow-sm"
        >
          <div className="shrink-0 mt-0.5 text-primary">
            <Activity className="h-3.5 w-3.5" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium truncate">{e.title}</p>
            <p className="text-[11px] text-muted-foreground line-clamp-2">
              {e.summary}
            </p>
            {e.trace_id && (
              <a
                href={`/admin#trace=${encodeURIComponent(e.trace_id)}`}
                className="text-[11px] text-primary hover:underline mt-0.5 inline-block"
              >
                View details
              </a>
            )}
          </div>
          <button
            onClick={() => dismiss(e.banner_id)}
            aria-label="Dismiss"
            className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
