'use client';

import { useEffect, useState, useCallback } from 'react';
import {
  api,
  type OrchestrationHealthSummary,
  type OrchestrationTraceListResponse,
  type OrchestrationTraceDetail,
  type WorkerAccessListResponse,
} from '@/lib/api';
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  Cpu,
  Users,
  MessageSquare,
  Zap,
  TrendingUp,
  XCircle,
  Database,
  Network,
  ChevronRight,
  ChevronDown,
  Lock,
  Unlock,
  Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';

interface MonitoringData {
  timestamp: string;
  db_status: string;
  python_version: string;
  users: { total: number; active: number };
  activity: {
    active_sessions_1h: number;
    messages_1h: number;
    messages_24h: number;
    tokens_1h: number;
    tokens_24h: number;
  };
  quality: {
    error_rate_pct: number;
    errors_24h: number;
    total_audit_24h: number;
  };
  model_health: Array<{ model: string; requests_24h: number; tokens_24h: number }>;
  recent_errors: Array<{
    id: string;
    user_id: string;
    action: string;
    resource_type: string;
    created_at: string;
  }>;
}

function StatCard({
  label,
  value,
  sub,
  icon,
  color = 'text-foreground',
}: {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.ReactNode;
  color?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="flex items-center gap-2 text-muted-foreground mb-1.5">
        {icon}
        <span className="text-[11px] uppercase tracking-wide font-medium">{label}</span>
      </div>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-[11px] text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${ok ? 'bg-green-500' : 'bg-red-500'}`} />
  );
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtTime(iso: string) {
  try { return new Date(iso).toLocaleTimeString(undefined, { timeStyle: 'short' }); }
  catch { return iso; }
}

export function MonitoringTab() {
  const [data, setData] = useState<MonitoringData | null>(null);
  const [orchestration, setOrchestration] = useState<OrchestrationHealthSummary | null>(null);
  const [tracesData, setTracesData] = useState<OrchestrationTraceListResponse | null>(null);
  const [tracesError, setTracesError] = useState<string | null>(null);
  const [accessData, setAccessData] = useState<WorkerAccessListResponse | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [grantWorkerId, setGrantWorkerId] = useState<string>('');
  const [grantTenantId, setGrantTenantId] = useState<string>('');
  const [grantBusy, setGrantBusy] = useState(false);
  const [grantMessage, setGrantMessage] = useState<string | null>(null);
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const [traceDetail, setTraceDetail] = useState<OrchestrationTraceDetail | null>(null);
  const [traceDetailLoading, setTraceDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.getMonitoringData();
      setData(result);
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e.message ?? 'Failed to load monitoring data');
    } finally {
      setLoading(false);
    }
    // Orchestration health is a separate, non-fatal call — failures here
    // shouldn't replace the main monitoring panel with an error.
    try {
      const orch = await api.getOrchestrationHealth();
      setOrchestration(orch);
    } catch {
      setOrchestration(null);
    }
    // Trace list — also non-fatal.  An admin without trace data still
    // sees the rest of the monitoring tab.
    try {
      const traces = await api.getOrchestrationTraces({ limit: 20 });
      setTracesData(traces);
      setTracesError(null);
    } catch (e: any) {
      setTracesData(null);
      setTracesError(e?.message ?? 'Trace data unavailable');
    }
    // Worker access — non-fatal.  When default_allow is true the
    // form/table is hidden anyway.
    try {
      const acc = await api.listWorkerAccess({ includeRevoked: false });
      setAccessData(acc);
      setAccessError(null);
    } catch (e: any) {
      setAccessData(null);
      setAccessError(e?.message ?? 'Access data unavailable');
    }
  }, []);

  const reloadAccess = useCallback(async () => {
    try {
      const acc = await api.listWorkerAccess({ includeRevoked: false });
      setAccessData(acc);
      setAccessError(null);
    } catch (e: any) {
      setAccessError(e?.message ?? 'Access data unavailable');
    }
  }, []);

  const handleGrant = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!grantWorkerId.trim() || !grantTenantId.trim()) return;
      setGrantBusy(true);
      setGrantMessage(null);
      try {
        await api.grantWorkerAccess(grantWorkerId.trim(), grantTenantId.trim());
        setGrantWorkerId('');
        setGrantTenantId('');
        setGrantMessage('Access granted.');
        await reloadAccess();
      } catch (err: any) {
        setGrantMessage(err?.message ?? 'Failed to grant access.');
      } finally {
        setGrantBusy(false);
      }
    },
    [grantWorkerId, grantTenantId, reloadAccess],
  );

  const handleRevoke = useCallback(
    async (id: string) => {
      try {
        await api.revokeWorkerAccess(id);
        await reloadAccess();
      } catch (err: any) {
        setAccessError(err?.message ?? 'Failed to revoke.');
      }
    },
    [reloadAccess],
  );

  const handleTraceToggle = useCallback(
    async (traceId: string) => {
      if (expandedTraceId === traceId) {
        setExpandedTraceId(null);
        setTraceDetail(null);
        return;
      }
      setExpandedTraceId(traceId);
      setTraceDetail(null);
      setTraceDetailLoading(true);
      try {
        const detail = await api.getOrchestrationTraceDetail(traceId);
        setTraceDetail(detail);
      } catch {
        setTraceDetail(null);
      } finally {
        setTraceDetailLoading(false);
      }
    },
    [expandedTraceId],
  );

  useEffect(() => {
    load();
    // Auto-refresh every 30s
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-16 text-muted-foreground text-sm">
        <RefreshCw className="h-4 w-4 animate-spin mr-2" />
        Loading monitoring data…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-10">
        <XCircle className="h-8 w-8 text-red-400" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <Button variant="outline" size="sm" onClick={load}>Retry</Button>
      </div>
    );
  }

  if (!data) return null;

  const dbOk = data.db_status === 'ok';
  const errorRateOk = data.quality.error_rate_pct < 5;

  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">System Monitoring</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Live snapshot · auto-refreshes every 30 s
            {lastRefresh && ` · last updated ${fmtTime(lastRefresh.toISOString())}`}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* System status strip */}
      <div className="flex gap-3 flex-wrap">
        <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full border font-medium ${dbOk ? 'bg-green-500/10 border-green-500/30 text-green-600 dark:text-green-400' : 'bg-red-500/10 border-red-500/30 text-red-600'}`}>
          {dbOk ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
          Database {dbOk ? 'OK' : 'ERROR'}
        </div>
        <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full border font-medium ${errorRateOk ? 'bg-green-500/10 border-green-500/30 text-green-600 dark:text-green-400' : 'bg-amber-500/10 border-amber-500/30 text-amber-600'}`}>
          {errorRateOk ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
          Error rate {data.quality.error_rate_pct}%
        </div>
        <div className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-full border bg-card font-medium">
          <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
          Python {data.python_version}
        </div>
      </div>

      {/* Orchestration brain — registered worker apps (Phase 2) */}
      {orchestration && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Network className="h-3.5 w-3.5" />
            Orchestration Workers ({orchestration.worker_count})
          </h4>
          {orchestration.worker_count === 0 ? (
            <p className="text-xs text-muted-foreground rounded-lg border bg-card p-3">
              No workers registered. Set <code>TASK_RADAR_BASE_URL</code> in env to seed Mela Task Radar.
            </p>
          ) : (
            <div className="rounded-lg border overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Worker</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Breaker</th>
                    <th className="text-right px-3 py-2 font-medium text-muted-foreground">Failures</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {orchestration.workers.map((w) => {
                    const ok = w.status === 'healthy';
                    const degraded = w.status === 'degraded';
                    const dotClass = ok
                      ? 'bg-green-500'
                      : degraded
                      ? 'bg-amber-500'
                      : w.status === 'unreachable'
                      ? 'bg-red-500'
                      : 'bg-muted-foreground/40';
                    return (
                      <tr key={w.id} className="hover:bg-accent/20">
                        <td className="px-3 py-2 font-medium">
                          {w.display_name}
                          <span className="ml-2 text-muted-foreground">{w.protocol}</span>
                        </td>
                        <td className="px-3 py-2">
                          <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${dotClass}`} />
                          {w.status}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{w.breaker.state}</td>
                        <td className="px-3 py-2 text-right text-muted-foreground">
                          {w.breaker.failure_count}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Phase 5C: Access Control — admin-only, non-fatal */}
      {accessError && !accessData && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Lock className="h-3.5 w-3.5" />
            Access Control
          </h4>
          <p className="text-xs text-muted-foreground rounded-lg border bg-card p-3">
            Access data unavailable.
          </p>
        </div>
      )}
      {accessData && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            {accessData.default_allow
              ? <Unlock className="h-3.5 w-3.5" />
              : <Lock className="h-3.5 w-3.5" />}
            Access Control
          </h4>
          {accessData.default_allow ? (
            <p className="text-xs text-muted-foreground rounded-lg border bg-card p-3">
              All tenants have access to all workers (default open).
              Set <code>WORKER_ACCESS_DEFAULT_ALLOW=false</code> to enable per-tenant grants.
            </p>
          ) : (
            <div className="space-y-3">
              <form
                onSubmit={handleGrant}
                className="rounded-lg border bg-card p-3 flex flex-col sm:flex-row gap-2"
              >
                <input
                  type="text"
                  placeholder="worker_id"
                  value={grantWorkerId}
                  onChange={(e) => setGrantWorkerId(e.target.value)}
                  className="flex-1 text-xs px-2 py-1 border rounded-md bg-background"
                  disabled={grantBusy}
                />
                <input
                  type="text"
                  placeholder="tenant_id"
                  value={grantTenantId}
                  onChange={(e) => setGrantTenantId(e.target.value)}
                  className="flex-1 text-xs px-2 py-1 border rounded-md bg-background"
                  disabled={grantBusy}
                />
                <Button
                  type="submit"
                  size="sm"
                  variant="outline"
                  disabled={grantBusy || !grantWorkerId.trim() || !grantTenantId.trim()}
                >
                  {grantBusy ? 'Granting…' : 'Grant access'}
                </Button>
              </form>
              {grantMessage && (
                <p className="text-[11px] text-muted-foreground">{grantMessage}</p>
              )}
              {accessData.grants.length === 0 ? (
                <p className="text-xs text-muted-foreground rounded-lg border bg-card p-3">
                  No active grants. No tenant can invoke any worker until grants are issued.
                </p>
              ) : (
                <div className="rounded-lg border overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">Worker</th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">Tenant</th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">Granted</th>
                        <th className="text-left px-3 py-2 font-medium text-muted-foreground">By</th>
                        <th className="text-right px-3 py-2 font-medium text-muted-foreground"></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {accessData.grants.map((g) => (
                        <tr key={g.id} className="hover:bg-accent/20">
                          <td className="px-3 py-2 font-mono text-[11px]">{g.worker_id}</td>
                          <td className="px-3 py-2 font-mono text-[11px]">{g.tenant_id}</td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {g.granted_at ? fmtTime(g.granted_at) : '—'}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground truncate max-w-[120px]" title={g.granted_by}>
                            {g.granted_by.slice(0, 8)}…
                          </td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              size="sm"
                              variant="ghost"
                              title="Revoke access"
                              onClick={() => handleRevoke(g.id)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Phase 4: Orchestration trace viewer — admin-only, non-fatal */}
      {tracesError && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5" />
            Orchestration Traces
          </h4>
          <p className="text-xs text-muted-foreground rounded-lg border bg-card p-3">
            Trace data unavailable.
          </p>
        </div>
      )}
      {tracesData && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5" />
            Orchestration Traces ({tracesData.total})
          </h4>
          {tracesData.traces.length === 0 ? (
            <p className="text-xs text-muted-foreground rounded-lg border bg-card p-3">
              No traces recorded yet. Traces are created when chat hits a worker tool.
            </p>
          ) : (
            <div className="rounded-lg border overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground w-6"></th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Goal</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Tasks</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Duration</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">User</th>
                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {tracesData.traces.map((t) => {
                    const isOpen = expandedTraceId === t.trace_id;
                    const goalStr = t.goal ?? '(no goal)';
                    const goalShort = goalStr.length > 60
                      ? goalStr.slice(0, 60) + '…'
                      : goalStr;
                    const dotClass = t.status === 'completed'
                      ? 'bg-green-500'
                      : t.status === 'partial'
                      ? 'bg-amber-500'
                      : t.status === 'failed'
                      ? 'bg-red-500'
                      : 'bg-blue-500';
                    let durationStr = '—';
                    if (t.created_at && t.completed_at) {
                      const ms =
                        new Date(t.completed_at).getTime() -
                        new Date(t.created_at).getTime();
                      durationStr = ms < 1000
                        ? `${ms}ms`
                        : `${(ms / 1000).toFixed(1)}s`;
                    }
                    return (
                      <>
                        <tr
                          key={t.trace_id}
                          className="hover:bg-accent/20 cursor-pointer"
                          onClick={() => handleTraceToggle(t.trace_id)}
                        >
                          <td className="px-3 py-2 text-muted-foreground">
                            {isOpen
                              ? <ChevronDown className="h-3.5 w-3.5" />
                              : <ChevronRight className="h-3.5 w-3.5" />}
                          </td>
                          <td className="px-3 py-2 font-medium" title={goalStr}>
                            {goalShort}
                          </td>
                          <td className="px-3 py-2">
                            <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${dotClass}`} />
                            {t.status}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {t.task_count - t.failed_task_count}/{t.task_count}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">{durationStr}</td>
                          <td className="px-3 py-2 text-muted-foreground truncate max-w-[140px]" title={t.user_id}>
                            {t.user_id.slice(0, 8)}…
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {t.created_at ? fmtTime(t.created_at) : '—'}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr key={t.trace_id + ':detail'} className="bg-muted/20">
                            <td colSpan={7} className="px-3 py-3">
                              {traceDetailLoading ? (
                                <p className="text-xs text-muted-foreground">Loading task detail…</p>
                              ) : traceDetail && traceDetail.trace_id === t.trace_id ? (
                                traceDetail.tasks.length === 0 ? (
                                  <p className="text-xs text-muted-foreground">No tasks recorded.</p>
                                ) : (
                                  <table className="w-full text-[11px]">
                                    <thead>
                                      <tr className="text-muted-foreground">
                                        <th className="text-left pr-3 pb-1">Worker</th>
                                        <th className="text-left pr-3 pb-1">Capability</th>
                                        <th className="text-left pr-3 pb-1">Status</th>
                                        <th className="text-left pr-3 pb-1">Latency</th>
                                        <th className="text-left pr-3 pb-1">Summary</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {traceDetail.tasks.map((tk) => (
                                        <tr key={tk.task_id} className="align-top">
                                          <td className="pr-3 py-1 font-mono">{tk.worker_id}</td>
                                          <td className="pr-3 py-1 font-mono">{tk.capability}</td>
                                          <td className="pr-3 py-1">{tk.status}</td>
                                          <td className="pr-3 py-1">{tk.latency_ms}ms</td>
                                          <td className="pr-3 py-1 text-muted-foreground">
                                            {tk.summary || tk.error_message || '—'}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                )
                              ) : (
                                <p className="text-xs text-muted-foreground">Trace detail unavailable.</p>
                              )}
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* KPI grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatCard
          label="Active sessions (1h)"
          value={data.activity.active_sessions_1h}
          icon={<Activity className="h-3.5 w-3.5" />}
          color="text-blue-600 dark:text-blue-400"
        />
        <StatCard
          label="Messages (24h)"
          value={fmt(data.activity.messages_24h)}
          sub={`${fmt(data.activity.messages_1h)} last hour`}
          icon={<MessageSquare className="h-3.5 w-3.5" />}
          color="text-violet-600 dark:text-violet-400"
        />
        <StatCard
          label="Tokens (24h)"
          value={fmt(data.activity.tokens_24h)}
          sub={`${fmt(data.activity.tokens_1h)} last hour`}
          icon={<Zap className="h-3.5 w-3.5" />}
          color="text-amber-600 dark:text-amber-400"
        />
        <StatCard
          label="Active users"
          value={data.users.active}
          sub={`${data.users.total} total`}
          icon={<Users className="h-3.5 w-3.5" />}
          color="text-green-600 dark:text-green-400"
        />
      </div>

      {/* Model health */}
      {data.model_health.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Model Activity (24h)
          </h4>
          <div className="rounded-lg border overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-3 py-2 font-medium text-muted-foreground">Model</th>
                  <th className="text-right px-3 py-2 font-medium text-muted-foreground">Requests</th>
                  <th className="text-right px-3 py-2 font-medium text-muted-foreground">Tokens</th>
                  <th className="text-right px-3 py-2 font-medium text-muted-foreground">Avg tokens/req</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.model_health.map((m) => (
                  <tr key={m.model} className="hover:bg-accent/20">
                    <td className="px-3 py-2 font-medium">{m.model}</td>
                    <td className="px-3 py-2 text-right">{m.requests_24h.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right">{fmt(m.tokens_24h)}</td>
                    <td className="px-3 py-2 text-right text-muted-foreground">
                      {m.requests_24h > 0 ? Math.round(m.tokens_24h / m.requests_24h).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Quality panel */}
      <div>
        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
          Quality &amp; Errors (24h)
        </h4>
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-lg border bg-card p-3 text-center">
            <p className={`text-xl font-bold ${errorRateOk ? 'text-green-600 dark:text-green-400' : 'text-red-500'}`}>
              {data.quality.error_rate_pct}%
            </p>
            <p className="text-[11px] text-muted-foreground mt-0.5">Error rate</p>
          </div>
          <div className="rounded-lg border bg-card p-3 text-center">
            <p className="text-xl font-bold text-red-500">{data.quality.errors_24h}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">Failed events</p>
          </div>
          <div className="rounded-lg border bg-card p-3 text-center">
            <p className="text-xl font-bold">{data.quality.total_audit_24h.toLocaleString()}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">Total audit events</p>
          </div>
        </div>
      </div>

      {/* Recent errors */}
      {data.recent_errors.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Recent Errors
          </h4>
          <div className="rounded-lg border divide-y">
            {data.recent_errors.map((e) => (
              <div key={e.id} className="px-3 py-2 flex items-start gap-2 text-xs">
                <AlertCircle className="h-3.5 w-3.5 text-red-400 shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <span className="font-medium">{e.action}</span>
                  <span className="text-muted-foreground"> on {e.resource_type}</span>
                </div>
                <span className="text-muted-foreground shrink-0">{fmtTime(e.created_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.recent_errors.length === 0 && (
        <div className="rounded-lg border border-dashed p-4 text-center">
          <CheckCircle2 className="h-5 w-5 text-green-500 mx-auto mb-1" />
          <p className="text-xs text-muted-foreground">No errors in the last 24 hours</p>
        </div>
      )}
    </div>
  );
}
