'use client';

import { useEffect, useState, useMemo } from 'react';
import { useChatStore } from '@/lib/store';
import {
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from 'recharts';
import {
  Loader2,
  Activity,
  Hash,
  DollarSign,
  TrendingUp,
  MessageSquare,
  MessagesSquare,
  BarChart3,
  AlertTriangle,
  Clock,
  Gauge,
  RefreshCw,
} from 'lucide-react';

type Range = '1d' | '7d' | '30d' | '90d';

const RANGE_DAYS: Record<Range, number> = { '1d': 1, '7d': 7, '30d': 30, '90d': 90 };
const RANGE_LABELS: Record<Range, string> = { '1d': 'Today', '7d': '7D', '30d': '30D', '90d': '90D' };

const PIE_COLORS = [
  'hsl(217, 91%, 60%)',
  'hsl(142, 71%, 45%)',
  'hsl(38, 92%, 50%)',
  'hsl(280, 67%, 55%)',
  'hsl(0, 84%, 60%)',
  'hsl(199, 89%, 48%)',
];

function fmtCost(v: number): string {
  if (v === 0) return '$0.00';
  if (v < 0.001) return `$${v.toFixed(6)}`;
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(3)}`;
}

function fmtNum(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return v.toLocaleString();
}

// Parse "2026-02-20" as a LOCAL date (not UTC midnight which shifts day in UTC+ zones)
function parseDateLabel(dateStr: string): string {
  const [y, mo, day] = dateStr.split('-').map(Number);
  return new Date(y, mo - 1, day).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function UsageTab() {
  const { userUsage, isLoadingSettings, fetchUsage } = useChatStore();
  const [range, setRange] = useState<Range>('30d');
  const [isFetching, setIsFetching] = useState(false);

  // ── All hooks MUST come before any conditional return ──────────────────────

  // Human-readable period header
  const periodLabel = useMemo(() => {
    const now = new Date();
    const start = new Date(now);
    start.setDate(now.getDate() - RANGE_DAYS[range] + 1);
    const fmt = (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    return range === '1d' ? `Today — ${fmt(now)}` : `${fmt(start)} – ${fmt(now)}`;
  }, [range]);

  // Daily chart data
  const dailyData = useMemo(() => {
    if (!userUsage?.daily_usage) return [];
    return userUsage.daily_usage.map((d) => ({
      date: parseDateLabel(d.date as string),
      tokens: d.tokens,
      prompt_tokens: d.prompt_tokens,
      completion_tokens: d.completion_tokens,
      estimated_cost: d.estimated_cost,
      conversations: d.conversations,
      messages: d.messages,
    }));
  }, [userUsage?.daily_usage]);

  const modelCostData = useMemo(() => {
    if (!userUsage?.cost_by_model) return [];
    return userUsage.cost_by_model
      .filter((c) => c.cost > 0)
      .map((c) => ({ name: c.category, cost: c.cost, tokens: c.tokens, requests: c.requests }));
  }, [userUsage?.cost_by_model]);

  const pieData = useMemo(() => {
    if (!userUsage?.model_breakdown) return [];
    return userUsage.model_breakdown
      .filter((m) => m.total_tokens > 0)
      .map((m) => ({ name: m.model, value: m.total_tokens }));
  }, [userUsage?.model_breakdown]);

  const tokenPercent = useMemo(() => {
    if (!userUsage) return 0;
    return Math.min(100, Math.round((userUsage.tokens_used_today / Math.max(userUsage.daily_token_limit, 1)) * 100));
  }, [userUsage]);

  const alerts = useMemo(() => {
    const items: { type: 'warning' | 'danger'; message: string }[] = [];
    if (!userUsage) return items;

    if (tokenPercent > 80) {
      items.push({
        type: tokenPercent > 95 ? 'danger' : 'warning',
        message: `Daily token limit is ${tokenPercent}% used (${fmtNum(userUsage.tokens_used_today)} / ${fmtNum(userUsage.daily_token_limit)})`,
      });
    }
    if (userUsage.daily_usage.length > 1) {
      const costs = userUsage.daily_usage.map((d) => d.estimated_cost);
      const avg = costs.reduce((a, b) => a + b, 0) / costs.length;
      if (costs.some((c) => avg > 0 && c > avg * 2)) {
        items.push({ type: 'warning', message: 'Cost spike detected — a day exceeded 2× the average' });
      }
    }
    if (userUsage.cost_by_model.length > 1 && userUsage.estimated_total_cost > 0) {
      for (const m of userUsage.cost_by_model) {
        if (m.cost / userUsage.estimated_total_cost > 0.7) {
          items.push({ type: 'warning', message: `${m.category} accounts for >70% of total cost` });
          break;
        }
      }
    }
    return items;
  }, [userUsage, tokenPercent]);

  // ── Effects ────────────────────────────────────────────────────────────────
  useEffect(() => {
    setIsFetching(true);
    fetchUsage(RANGE_DAYS[range]).finally(() => setIsFetching(false));
  }, [fetchUsage, range]);

  // ── Render ─────────────────────────────────────────────────────────────────
  const usage = userUsage;
  const rangeLabel = RANGE_LABELS[range];

  const tooltipStyle = {
    backgroundColor: 'hsl(var(--card))',
    border: '1px solid hsl(var(--border))',
    borderRadius: '8px',
    fontSize: '12px',
  };

  const kpis = [
    {
      label: `Requests (${rangeLabel})`,
      value: fmtNum(usage?.total_requests ?? 0),
      sub: 'AI calls made',
      icon: <Activity className="h-4 w-4" />,
      color: 'text-blue-600',
      bg: 'bg-blue-50 dark:bg-blue-950',
    },
    {
      label: `Tokens (${rangeLabel})`,
      value: fmtNum(usage?.total_tokens ?? 0),
      sub: `${fmtNum(usage?.total_prompt_tokens ?? 0)} in · ${fmtNum(usage?.total_completion_tokens ?? 0)} out`,
      icon: <Hash className="h-4 w-4" />,
      color: 'text-green-600',
      bg: 'bg-green-50 dark:bg-green-950',
    },
    {
      label: `Est. Cost (${rangeLabel})`,
      value: fmtCost(usage?.estimated_total_cost ?? 0),
      sub: `${fmtCost(usage?.avg_cost_per_request ?? 0)} avg/request`,
      icon: <DollarSign className="h-4 w-4" />,
      color: 'text-amber-600',
      bg: 'bg-amber-50 dark:bg-amber-950',
    },
    {
      label: `Conversations (${rangeLabel})`,
      value: fmtNum(usage?.total_conversations ?? 0),
      sub: `${fmtNum(usage?.total_messages ?? 0)} messages`,
      icon: <MessagesSquare className="h-4 w-4" />,
      color: 'text-purple-600',
      bg: 'bg-purple-50 dark:bg-purple-950',
    },
  ];

  // Show spinner on initial load (no data yet)
  if (isLoadingSettings && !usage) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* ── Filter bar ── */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground tabular-nums">{periodLabel}</span>
        <div className="flex items-center gap-2">
          {isFetching && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
          <div className="flex gap-1">
            {(Object.keys(RANGE_DAYS) as Range[]).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                disabled={isFetching}
                className={`px-3 py-1.5 text-xs rounded-md font-medium transition-colors ${
                  range === r
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground hover:bg-accent'
                }`}
              >
                {RANGE_LABELS[r]}
              </button>
            ))}
          </div>
          <button
            onClick={() => { setIsFetching(true); fetchUsage(RANGE_DAYS[range]).finally(() => setIsFetching(false)); }}
            disabled={isFetching}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title="Refresh"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* ── Governance Alerts ── */}
      {alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((a, i) => (
            <div
              key={i}
              className={`flex items-center gap-2 p-3 rounded-lg text-sm ${
                a.type === 'danger'
                  ? 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800'
                  : 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800'
              }`}
            >
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {a.message}
            </div>
          ))}
        </div>
      )}

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="rounded-lg border bg-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <div className={`p-1.5 rounded-md ${kpi.bg} ${kpi.color}`}>{kpi.icon}</div>
              <span className="text-xs text-muted-foreground leading-tight">{kpi.label}</span>
            </div>
            <p className="text-2xl font-bold tabular-nums">{kpi.value}</p>
            <p className="text-xs text-muted-foreground mt-1">{kpi.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Daily token limit bar ── */}
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium">Today's Token Usage</span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {fmtNum(usage?.tokens_used_today ?? 0)} / {fmtNum(usage?.daily_token_limit ?? 100_000)}
            {' '}({tokenPercent}%)
          </span>
        </div>
        <div className="h-2.5 bg-muted rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              tokenPercent > 95 ? 'bg-red-500' : tokenPercent > 80 ? 'bg-amber-500' : 'bg-primary'
            }`}
            style={{ width: `${tokenPercent}%` }}
          />
        </div>
      </div>

      {/* ── Token Consumption chart ── */}
      <div className="rounded-lg border bg-card p-4">
        <span className="text-sm font-medium block mb-4">Token Consumption — Input vs Output</span>
        {dailyData.length > 0 && dailyData.some((d) => d.tokens > 0) ? (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={dailyData} barCategoryGap="30%">
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} className="text-muted-foreground" />
              <YAxis tick={{ fontSize: 11 }} className="text-muted-foreground" tickFormatter={fmtNum} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v: number, name: string) => [fmtNum(v), name === 'prompt_tokens' ? 'Input tokens' : 'Output tokens']}
              />
              <Bar dataKey="prompt_tokens" name="prompt_tokens" stackId="tokens" fill="hsl(217, 91%, 60%)" radius={[0, 0, 0, 0]} />
              <Bar dataKey="completion_tokens" name="completion_tokens" stackId="tokens" fill="hsl(142, 71%, 45%)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex items-center justify-center h-[220px] text-sm text-muted-foreground">
            No token data for this period
          </div>
        )}
      </div>

      {/* ── Cost Trend ── */}
      <div className="rounded-lg border bg-card p-4">
        <span className="text-sm font-medium block mb-4">Estimated Cost Trend</span>
        {dailyData.length > 0 && dailyData.some((d) => d.estimated_cost > 0) ? (
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={dailyData}>
              <defs>
                <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(3)}`} />
              <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [fmtCost(v), 'Cost']} />
              <Area type="monotone" dataKey="estimated_cost" stroke="hsl(var(--primary))" fill="url(#costGrad)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex items-center justify-center h-[200px] text-sm text-muted-foreground">
            No cost data for this period
          </div>
        )}
      </div>

      {/* ── Model breakdown side-by-side ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border bg-card p-4">
          <span className="text-sm font-medium block mb-4">Cost by Model</span>
          {modelCostData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={modelCostData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => fmtCost(v)} />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={110} />
                <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [fmtCost(v), 'Cost']} />
                <Bar dataKey="cost" radius={[0, 4, 4, 0]}>
                  {modelCostData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[200px] text-sm text-muted-foreground">No data</div>
          )}
        </div>

        <div className="rounded-lg border bg-card p-4">
          <span className="text-sm font-medium block mb-4">Token Share by Model</span>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={pieData} cx="50%" cy="50%" innerRadius={45} outerRadius={75} dataKey="value" paddingAngle={2}>
                  {pieData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [fmtNum(v), 'Tokens']} />
                <Legend iconSize={10} wrapperStyle={{ fontSize: '11px' }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-[200px] text-sm text-muted-foreground">No data</div>
          )}
        </div>
      </div>

      {/* ── Efficiency Panel ── */}
      <div className="rounded-lg border bg-card p-4">
        <span className="text-sm font-medium block mb-4">Efficiency Metrics</span>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-primary/10 text-primary"><Gauge className="h-5 w-5" /></div>
            <div>
              <p className="text-xs text-muted-foreground">Output / Input Ratio</p>
              <p className="text-xl font-bold tabular-nums">{(usage?.token_efficiency_ratio ?? 0).toFixed(2)}×</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-sky-100 dark:bg-sky-950 text-sky-600"><BarChart3 className="h-5 w-5" /></div>
            <div>
              <p className="text-xs text-muted-foreground">Avg Prompt / Response</p>
              <p className="text-sm font-medium tabular-nums">
                {usage?.total_requests
                  ? `${fmtNum(Math.round(usage.total_prompt_tokens / usage.total_requests))} → ${fmtNum(Math.round(usage.total_completion_tokens / usage.total_requests))}`
                  : '—'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-100 dark:bg-amber-950 text-amber-600"><Clock className="h-5 w-5" /></div>
            <div>
              <p className="text-xs text-muted-foreground">Peak Hour (local)</p>
              <p className="text-xl font-bold">
                {usage && usage.peak_hour >= 0
                  ? new Date(2000, 0, 1, usage.peak_hour).toLocaleTimeString('en-US', { hour: 'numeric', hour12: true })
                  : '—'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* ── Model Breakdown Table ── */}
      {usage?.model_breakdown && usage.model_breakdown.length > 0 && (
        <div className="rounded-lg border bg-card p-4">
          <span className="text-sm font-medium block mb-3">Model Breakdown</span>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground text-xs">
                  <th className="pb-2 font-medium">Model</th>
                  <th className="pb-2 font-medium text-right">Requests</th>
                  <th className="pb-2 font-medium text-right">Input</th>
                  <th className="pb-2 font-medium text-right">Output</th>
                  <th className="pb-2 font-medium text-right">Total</th>
                  <th className="pb-2 font-medium text-right">Est. Cost</th>
                </tr>
              </thead>
              <tbody>
                {usage.model_breakdown.map((m) => (
                  <tr key={m.model} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="py-2 font-medium text-xs">{m.model}</td>
                    <td className="py-2 text-right tabular-nums text-xs">{m.request_count.toLocaleString()}</td>
                    <td className="py-2 text-right tabular-nums text-xs">{fmtNum(m.prompt_tokens)}</td>
                    <td className="py-2 text-right tabular-nums text-xs">{fmtNum(m.completion_tokens)}</td>
                    <td className="py-2 text-right tabular-nums text-xs font-medium">{fmtNum(m.total_tokens)}</td>
                    <td className="py-2 text-right tabular-nums text-xs">{fmtCost(m.estimated_cost)}</td>
                  </tr>
                ))}
                {/* Totals row */}
                <tr className="bg-muted/30 font-semibold">
                  <td className="py-2 text-xs">Total</td>
                  <td className="py-2 text-right tabular-nums text-xs">{fmtNum(usage.total_requests)}</td>
                  <td className="py-2 text-right tabular-nums text-xs">{fmtNum(usage.total_prompt_tokens)}</td>
                  <td className="py-2 text-right tabular-nums text-xs">{fmtNum(usage.total_completion_tokens)}</td>
                  <td className="py-2 text-right tabular-nums text-xs">{fmtNum(usage.total_tokens)}</td>
                  <td className="py-2 text-right tabular-nums text-xs">{fmtCost(usage.estimated_total_cost)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
