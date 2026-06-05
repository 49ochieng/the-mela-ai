/**
 * ModelInsightsPanel — compact model comparison on the chat welcome screen.
 * Pulls enabled models + live pricing from admin governance (1-min TTL).
 * Responsive 2→3-col grid; no horizontal scroll; all models visible at once.
 */

'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, ModelInsight } from '@/lib/api';
import { useChatStore } from '@/lib/store';
import { Zap, TrendingUp, Sparkles, Check } from 'lucide-react';
import { cn } from '@/lib/utils';

// ── Provider label ────────────────────────────────────────────────────────────

const PROVIDER_LABEL: Record<string, string> = {
  azure_openai:     'Azure OpenAI',
  azure_ai_foundry: 'AI Foundry',
  anthropic:        'Anthropic',
  google:           'Google AI',
};

// ── Per-model left-accent colour (Tailwind bg class) ─────────────────────────

const MODEL_COLOR: Record<string, string> = {
  'gpt-5.2-chat':      'bg-blue-500',
  'gpt-4.1':           'bg-cyan-500',
  'gpt-4o':            'bg-teal-500',
  'kimi-k2.5':         'bg-violet-500',
  'mistral-large-3':   'bg-orange-500',
  'grok-3-mini':       'bg-slate-500',
  'llama-4-maverick':  'bg-rose-500',
  'gemini-2.0-flash':  'bg-indigo-400',
  'claude-opus-4-6':   'bg-amber-500',
  'claude-sonnet-4-6': 'bg-yellow-500',
  'claude-haiku-4-5':  'bg-lime-500',
  'dall-e-3':          'bg-fuchsia-500',
};

// ── Badge ─────────────────────────────────────────────────────────────────────

const BADGE: Record<string, { label: string; icon: React.ReactNode; cls: string }> = {
  Popular:      { label: 'Popular',    icon: <TrendingUp className="h-2 w-2" />, cls: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  Fastest:      { label: 'Fastest',    icon: <Zap         className="h-2 w-2" />, cls: 'text-amber-700   bg-amber-50   border-amber-200'   },
  'Best Value': { label: 'Best Value', icon: <Sparkles    className="h-2 w-2" />, cls: 'text-sky-700     bg-sky-50     border-sky-200'     },
};

// ── Price formatter ───────────────────────────────────────────────────────────

function price(cost: number): string {
  if (cost === 0) return 'Free';
  if (cost < 0.001) return `$${(cost * 1000).toFixed(2)}/M`;
  return `$${cost.toFixed(4)}/1K`;
}

// ── ModelRow ──────────────────────────────────────────────────────────────────

function ModelRow({ insight, selected, onSelect }: {
  insight: ModelInsight;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const accent = MODEL_COLOR[insight.id] ?? 'bg-gray-400';
  const badge  = insight.badge ? BADGE[insight.badge] : null;
  const pLabel = PROVIDER_LABEL[insight.provider] ?? insight.provider;
  const isFree = insight.cost_per_1k_tokens === 0;

  return (
    <button
      onClick={() => onSelect(insight.id)}
      aria-pressed={selected}
      className={cn(
        'group relative flex items-center gap-2.5 rounded-lg border px-3 py-2.5',
        'text-left transition-all duration-150 w-full',
        selected
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border bg-card hover:border-primary/25 hover:bg-accent/30',
      )}
    >
      {/* Left accent stripe */}
      <span className={cn('shrink-0 w-0.5 h-6 rounded-full', accent)} />

      {/* Name + use-case */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[13px] font-semibold leading-none truncate">
            {insight.name}
          </span>
          {badge && (
            <span className={cn(
              'inline-flex items-center gap-0.5 px-1 py-0.5 rounded border',
              'text-[9px] font-semibold leading-none uppercase tracking-wide',
              badge.cls,
            )}>
              {badge.icon}{badge.label}
            </span>
          )}
          {!badge && insight.preview && (
            <span className="inline-flex items-center px-1 py-0.5 rounded border text-[9px] font-semibold leading-none uppercase tracking-wide text-amber-700 bg-amber-50 border-amber-200">
              Preview
            </span>
          )}
        </div>
        <p className="text-[10px] text-muted-foreground mt-0.5 leading-none truncate">
          {insight.performance_label}
        </p>
      </div>

      {/* Provider + price — right side */}
      <div className="shrink-0 text-right">
        <p className="text-[10px] text-muted-foreground/70 leading-none mb-0.5">
          {pLabel}
        </p>
        <p className={cn(
          'text-[11px] font-semibold leading-none tabular-nums',
          isFree ? 'text-emerald-600' : 'text-foreground',
        )}>
          {price(insight.cost_per_1k_tokens)}
        </p>
      </div>

      {/* Selected check */}
      {selected && (
        <span className="shrink-0 ml-0.5 text-primary">
          <Check className="h-3 w-3" />
        </span>
      )}
    </button>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function Skeleton() {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-3 gap-1.5">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-[52px] rounded-lg border border-border bg-card animate-pulse" />
      ))}
    </div>
  );
}

// ── ModelInsightsPanel ────────────────────────────────────────────────────────

export function ModelInsightsPanel() {
  const { selectedModel, setSelectedModel } = useChatStore();
  const [insights, setInsights] = useState<ModelInsight[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error,   setError]     = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.getModelInsights();
      setInsights(data);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  if (loading) return <Skeleton />;
  if (error || insights.length === 0) return null;

  return (
    <div className="w-full space-y-2">
      {/* Header */}
      <div className="flex items-center justify-between px-0.5">
        <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-widest">
          Choose a model
        </span>
        <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          Live pricing
        </span>
      </div>

      {/* Compact responsive grid */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-1.5">
        {insights.map((insight) => (
          <ModelRow
            key={insight.id}
            insight={insight}
            selected={selectedModel === insight.id}
            onSelect={setSelectedModel}
          />
        ))}
      </div>

      {/* Footer */}
      <p className="text-[10px] text-muted-foreground/50 text-center pt-0.5">
        Pricing and availability from admin governance settings
      </p>
    </div>
  );
}
