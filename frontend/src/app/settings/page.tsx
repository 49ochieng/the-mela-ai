'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, ModelRanking, ModelRankingUpdate, ClaudeUsageInfo } from '@/lib/api';

// ── Simple drag-to-reorder hook ────────────────────────────────────────────

function useRankings() {
  const [rankings, setRankings] = useState<ModelRanking[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.getModelRankings();
      setRankings(data);
      setDirty(false);
    } catch (e: any) {
      setError(e.message ?? 'Failed to load model rankings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = (modelId: string) => {
    setRankings(prev => prev.map(r =>
      r.model_id === modelId ? { ...r, is_enabled: !r.is_enabled } : r
    ));
    setDirty(true);
  };

  const moveUp = (index: number) => {
    if (index === 0) return;
    setRankings(prev => {
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      return next.map((r, i) => ({ ...r, rank: i + 1 }));
    });
    setDirty(true);
  };

  const moveDown = (index: number) => {
    setRankings(prev => {
      if (index >= prev.length - 1) return prev;
      const next = [...prev];
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      return next.map((r, i) => ({ ...r, rank: i + 1 }));
    });
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updates: ModelRankingUpdate[] = rankings.map(r => ({
        model_id: r.model_id,
        rank: r.rank,
        is_enabled: r.is_enabled,
      }));
      const updated = await api.updateModelRankings(updates);
      setRankings(updated);
      setDirty(false);
    } catch (e: any) {
      setError(e.message ?? 'Failed to save rankings');
    } finally {
      setSaving(false);
    }
  };

  return { rankings, loading, saving, error, dirty, toggle, moveUp, moveDown, save, reload: load };
}

// ── Claude Usage Card ──────────────────────────────────────────────────────

function ClaudeUsageCard() {
  const [usage, setUsage] = useState<ClaudeUsageInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getClaudeUsage()
      .then(setUsage)
      .catch(() => null)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-zinc-400">Loading usage…</div>;
  if (!usage) return null;

  const pct = usage.limit > 0 ? Math.min(100, Math.round((usage.question_count / usage.limit) * 100)) : 0;
  const barColor = pct >= 100 ? 'bg-red-500' : pct >= 60 ? 'bg-amber-500' : 'bg-emerald-500';

  return (
    <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-5 space-y-3">
      <h2 className="font-semibold text-sm text-zinc-700 dark:text-zinc-300">Claude Daily Usage</h2>
      <p className="text-xs text-zinc-500 dark:text-zinc-400">{usage.date}</p>
      <div className="flex items-center justify-between text-sm">
        <span className="text-zinc-600 dark:text-zinc-300">{usage.question_count} / {usage.limit} questions</span>
        <span className={`font-semibold ${pct >= 100 ? 'text-red-500' : 'text-zinc-500'}`}>
          {usage.remaining > 0 ? `${usage.remaining} remaining` : 'Limit reached'}
        </span>
      </div>
      <div className="h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
        <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      {usage.token_count > 0 && (
        <p className="text-xs text-zinc-400">{usage.token_count.toLocaleString()} tokens used today</p>
      )}
    </div>
  );
}

// ── Provider badge ─────────────────────────────────────────────────────────

function ProviderBadge({ provider }: { provider: string }) {
  const label = provider === 'anthropic' ? 'Anthropic' : 'Azure OpenAI';
  const cls = provider === 'anthropic'
    ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300'
    : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300';
  return <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>{label}</span>;
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { rankings, loading, saving, error, dirty, toggle, moveUp, moveDown, save } = useRankings();

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 py-10 px-4">
      <div className="max-w-2xl mx-auto space-y-8">
        <div>
          <h1 className="text-2xl font-bold text-zinc-900 dark:text-white">Settings</h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            Manage model routing priority and usage limits.
          </p>
        </div>

        {/* Claude usage */}
        <ClaudeUsageCard />

        {/* Model ranking */}
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 dark:border-zinc-800">
            <h2 className="font-semibold text-sm text-zinc-700 dark:text-zinc-300">Model Routing Priority</h2>
            {dirty && (
              <button
                onClick={save}
                disabled={saving}
                className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            )}
          </div>

          {error && (
            <div className="px-5 py-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20">
              {error}
            </div>
          )}

          {loading ? (
            <div className="px-5 py-8 text-center text-sm text-zinc-400">Loading…</div>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {rankings.map((model, index) => (
                <li
                  key={model.model_id}
                  className={`flex items-center gap-3 px-5 py-3 ${!model.is_enabled ? 'opacity-50' : ''}`}
                >
                  {/* Rank badge */}
                  <span className="w-6 text-center text-xs font-mono text-zinc-400">{model.rank}</span>

                  {/* Move buttons */}
                  <div className="flex flex-col gap-0.5">
                    <button
                      onClick={() => moveUp(index)}
                      disabled={index === 0}
                      className="text-zinc-300 hover:text-zinc-600 dark:hover:text-zinc-300 disabled:opacity-20 text-xs leading-none"
                      title="Move up"
                    >▲</button>
                    <button
                      onClick={() => moveDown(index)}
                      disabled={index === rankings.length - 1}
                      className="text-zinc-300 hover:text-zinc-600 dark:hover:text-zinc-300 disabled:opacity-20 text-xs leading-none"
                      title="Move down"
                    >▼</button>
                  </div>

                  {/* Model info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200 truncate">
                        {model.display_name}
                      </span>
                      <ProviderBadge provider={model.provider} />
                      {model.is_default && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300 font-medium">
                          Default
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-zinc-400 mt-0.5">{model.model_id}</p>
                  </div>

                  {/* Enable toggle */}
                  <button
                    onClick={() => toggle(model.model_id)}
                    className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                      model.is_enabled ? 'bg-blue-600' : 'bg-zinc-300 dark:bg-zinc-600'
                    }`}
                    title={model.is_enabled ? 'Disable model' : 'Enable model'}
                    role="switch"
                    aria-checked={model.is_enabled}
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

          <div className="px-5 py-3 text-xs text-zinc-400 bg-zinc-50 dark:bg-zinc-800/50 border-t border-zinc-100 dark:border-zinc-800">
            Models are tried in rank order. Disabled models are skipped during routing.
          </div>
        </div>
      </div>
    </div>
  );
}
