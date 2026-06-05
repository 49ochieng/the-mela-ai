/**
 * Mela AI - Budget Warning Banner
 *
 * Displays a warning banner when the user is approaching or has exceeded their budget.
 * Also can show a hard-stop modal if the budget is exceeded and hard_stop is enabled.
 */

'use client';

import { useEffect, useState, useCallback } from 'react';
import { AlertTriangle, X, ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

interface BudgetStatus {
  allowed: boolean;
  usage_pct: number;
  warning: boolean;
  hard_stop: boolean;
  message: string | null;
  budget_type: string | null;
}

interface BudgetBannerProps {
  onHardStop?: () => void; // Called when user is hard-stopped from sending messages
}

export function BudgetWarningBanner({ onHardStop }: BudgetBannerProps) {
  const [status, setStatus] = useState<BudgetStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [loading, setLoading] = useState(false);

  const checkBudget = useCallback(async () => {
    try {
      const res = await api.get<BudgetStatus>('/budgets/check');
      setStatus(res);
      if (!res.allowed && res.hard_stop) {
        onHardStop?.();
      }
    } catch {
      // Budget API not available or no budget set
      setStatus(null);
    }
  }, [onHardStop]);

  // Check budget on mount and every 60 seconds
  useEffect(() => {
    checkBudget();
    const interval = setInterval(checkBudget, 60000);
    return () => clearInterval(interval);
  }, [checkBudget]);

  // Don't show if no warning/stop, or if dismissed
  if (!status || (!status.warning && !status.hard_stop) || dismissed) {
    return null;
  }

  const isHardStop = status.hard_stop && !status.allowed;
  const bgClass = isHardStop
    ? 'bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-900'
    : 'bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-900';
  const textClass = isHardStop
    ? 'text-red-700 dark:text-red-400'
    : 'text-amber-700 dark:text-amber-400';
  const iconClass = isHardStop ? 'text-red-500' : 'text-amber-500';

  return (
    <div className={cn('flex items-center gap-3 px-4 py-2 border-b', bgClass)}>
      <AlertTriangle className={cn('h-4 w-4 shrink-0', iconClass)} />
      <p className={cn('text-sm flex-1', textClass)}>
        {status.message || (isHardStop ? 'Budget exceeded. Please contact your administrator.' : 'Approaching budget limit.')}
      </p>
      <div className="flex items-center gap-2 shrink-0">
        {status.usage_pct > 0 && (
          <span className={cn('text-xs font-medium', textClass)}>{status.usage_pct}% used</span>
        )}
        {!isHardStop && (
          <button
            onClick={() => setDismissed(true)}
            className="p-1 hover:bg-black/5 rounded"
            title="Dismiss"
          >
            <X className="h-3 w-3 text-muted-foreground" />
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Compact version for inline display (e.g., near the send button)
 */
export function BudgetUsageBadge() {
  const [summary, setSummary] = useState<{
    has_budget: boolean;
    token_used?: number;
    token_budget?: number;
    cost_used?: number;
    cost_budget?: number;
  } | null>(null);

  useEffect(() => {
    api.get<typeof summary>('/budgets/me').then(setSummary).catch(() => setSummary(null));
  }, []);

  if (!summary?.has_budget) return null;

  // Show token usage if token budget is set
  if (summary.token_budget && summary.token_used !== undefined) {
    const pct = Math.min(100, Math.round((summary.token_used / summary.token_budget) * 100));
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <div className="w-12 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full',
              pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-green-500'
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span>{pct}%</span>
      </div>
    );
  }

  // Show cost usage if cost budget is set
  if (summary.cost_budget && summary.cost_used !== undefined) {
    const pct = Math.min(100, Math.round((summary.cost_used / summary.cost_budget) * 100));
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <div className="w-12 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full',
              pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-green-500'
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span>${summary.cost_used?.toFixed(2)}</span>
      </div>
    );
  }

  return null;
}
