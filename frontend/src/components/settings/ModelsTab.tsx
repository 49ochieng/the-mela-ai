'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, ModelRanking, ModelRankingUpdate, ClaudeUsageInfo } from '@/lib/api';
import { useChatStore } from '@/lib/store';

// ── Provider status card ───────────────────────────────────────────────────

function ProviderStatusCard() {
  const [providers, setProviders] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    api.getProviderStatus()
      .then(setProviders)
      .catch(() => null);
  }, []);

  if (!providers) return null;

  return (
    <div className="rounded-lg border bg-card p-4 space-y-2">
      <h3 className="text-sm font-medium">Provider Status</h3>
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(providers).map(([key, info]) => (
          <div key={key} className="flex items-center gap-2 text-xs">
            <div className={`h-2 w-2 rounded-full flex-shrink-0 ${
              info.status === 'ok' ? 'bg-emerald-500' : 'bg-red-400'
            }`} />
            <span className="text-muted-foreground truncate">{info.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Provider badge ─────────────────────────────────────────────────────────

function ProviderBadge({ provider }: { provider: string }) {
  const label = provider === 'anthropic' ? 'Anthropic' : 'Azure OpenAI';
  const cls =
    provider === 'anthropic'
      ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300'
      : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300';
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${cls}`}>
      {label}
    </span>
  );
}

// ── Claude usage bar ───────────────────────────────────────────────────────

function ClaudeUsageBar() {
  const [usage, setUsage] = useState<ClaudeUsageInfo | null>(null);

  useEffect(() => {
    api.getClaudeUsage().then(setUsage).catch(() => null);
  }, []);

  if (!usage || usage.limit <= 0) return null;

  const pct = Math.min(100, Math.round((usage.question_count / usage.limit) * 100));
  const barColor =
    pct >= 100
      ? 'bg-red-500'
      : pct >= 60
      ? 'bg-amber-500'
      : 'bg-emerald-500';

  return (
    <div className="rounded-lg border bg-card p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Claude Daily Usage</h3>
        <span className="text-xs text-muted-foreground">{usage.date}</span>
      </div>
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">
          {usage.question_count} / {usage.limit} questions
        </span>
        <span
          className={
            pct >= 100
              ? 'text-red-500 font-semibold'
              : 'text-muted-foreground'
          }
        >
          {usage.remaining > 0
            ? `${usage.remaining} remaining`
            : 'Limit reached'}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── Main tab ───────────────────────────────────────────────────────────────

export function ModelsTab() {
  const [rankings, setRankings] = useState<ModelRanking[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const refreshAttribution = useChatStore((s) => s.loadModelAttribution);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.getModelRankings();
      setRankings(data);
      setDirty(false);
    } catch (e: any) {
      setError(e.message ?? 'Failed to load model rankings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (modelId: string) => {
    setRankings((prev) =>
      prev.map((r) =>
        r.model_id === modelId ? { ...r, is_enabled: !r.is_enabled } : r
      )
    );
    setDirty(true);
  };

  const move = (index: number, dir: -1 | 1) => {
    setRankings((prev) => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next.map((r, i) => ({ ...r, rank: i + 1 }));
    });
    setDirty(true);
  };

  const setMultiplier = (modelId: string, value: number) => {
    setRankings((prev) =>
      prev.map((r) =>
        r.model_id === modelId
          ? { ...r, cost_multiplier: Number.isFinite(value) && value >= 0 ? value : 0 }
          : r,
      ),
    );
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updates: ModelRankingUpdate[] = rankings.map((r) => ({
        model_id: r.model_id,
        rank: r.rank,
        is_enabled: r.is_enabled,
        cost_multiplier: r.cost_multiplier,
      }));
      const updated = await api.updateModelRankings(updates);
      setRankings(updated);
      setDirty(false);
      // Push the new multipliers into the global store so the attribution
      // subscript under each chat message updates immediately.
      refreshAttribution().catch(() => {});
    } catch (e: any) {
      setError(e.message ?? 'Failed to save rankings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <ProviderStatusCard />
      <ClaudeUsageBar />

      <div className="rounded-lg border bg-card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <div>
            <h3 className="text-sm font-medium">Model Routing Priority</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Models are tried in order — disabled models are skipped.
            </p>
          </div>
          {dirty && (
            <button
              onClick={save}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          )}
        </div>

        {error && (
          <div className="px-4 py-2 text-sm text-destructive bg-destructive/10">
            {error}
          </div>
        )}

        {loading ? (
          <div className="px-4 py-6 text-center text-sm text-muted-foreground">
            Loading…
          </div>
        ) : (
          <ul className="divide-y">
            {rankings.map((model, index) => (
              <li
                key={model.model_id}
                className={`flex items-center gap-3 px-4 py-2.5 ${
                  !model.is_enabled ? 'opacity-50' : ''
                }`}
              >
                {/* Rank */}
                <span className="w-5 text-center text-xs font-mono text-muted-foreground">
                  {model.rank}
                </span>

                {/* Move buttons */}
                <div className="flex flex-col gap-0">
                  <button
                    onClick={() => move(index, -1)}
                    disabled={index === 0}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-20 text-[10px] leading-tight"
                    title="Move up"
                  >
                    ▲
                  </button>
                  <button
                    onClick={() => move(index, 1)}
                    disabled={index === rankings.length - 1}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-20 text-[10px] leading-tight"
                    title="Move down"
                  >
                    ▼
                  </button>
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-sm font-medium truncate">
                      {model.display_name}
                    </span>
                    <ProviderBadge provider={model.provider} />
                    {model.is_default && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 font-medium">
                        Default
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground">{model.model_id}</p>
                </div>

                {/* Cost multiplier */}
                <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span>cost</span>
                  <input
                    type="number"
                    min={0}
                    step={0.1}
                    value={model.cost_multiplier ?? 1}
                    onChange={(e) =>
                      setMultiplier(model.model_id, parseFloat(e.target.value))
                    }
                    className="w-14 px-1.5 py-0.5 text-xs text-center rounded border bg-background"
                    title="Cost multiplier shown to users (e.g. 5 → 5x)"
                  />
                  <span>x</span>
                </label>

                {/* Toggle */}
                <button
                  onClick={() => toggle(model.model_id)}
                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                    model.is_enabled
                      ? 'bg-primary'
                      : 'bg-muted-foreground/30'
                  }`}
                  role="switch"
                  aria-checked={model.is_enabled}
                  title={model.is_enabled ? 'Disable' : 'Enable'}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      model.is_enabled ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
