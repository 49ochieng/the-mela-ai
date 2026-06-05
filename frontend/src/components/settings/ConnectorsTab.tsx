'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, ConnectorStatus, ConnectorJob, IndexStats } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { toast } from 'sonner';
import {
  Loader2,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Play,
  Database,
  Globe,
  Mail,
  LayoutList,
  HardDrive,
  Cloud,
} from 'lucide-react';

// ── Config ───────────────────────────────────────────────────────────────────

const CONNECTOR_META: Record<string, { label: string; icon: React.ReactNode; description: string }> = {
  sharepoint: {
    label: 'SharePoint',
    icon: <Cloud className="h-4 w-4" />,
    description: 'Crawl document libraries from SharePoint sites',
  },
  onedrive: {
    label: 'OneDrive',
    icon: <HardDrive className="h-4 w-4" />,
    description: "Index files from users' OneDrive",
  },
  email: {
    label: 'Outlook Email',
    icon: <Mail className="h-4 w-4" />,
    description: 'Index email subjects and previews (delegated, user-scoped)',
  },
  planner: {
    label: 'Planner',
    icon: <LayoutList className="h-4 w-4" />,
    description: 'Index tasks and plans from Microsoft Planner groups',
  },
  org_website: {
    label: 'Organisation Website',
    icon: <Globe className="h-4 w-4" />,
    description: 'Crawl approved organisation domains (sitemap-first)',
  },
  public_web: {
    label: 'Public Web Search',
    icon: <Database className="h-4 w-4" />,
    description: 'Live web search at query time — admin-controlled domain allowlist',
  },
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { cls: string; label: string }> = {
    done: { cls: 'bg-green-500/15 text-green-400', label: 'Done' },
    running: { cls: 'bg-blue-500/15 text-blue-400', label: 'Running' },
    pending: { cls: 'bg-yellow-500/15 text-yellow-400', label: 'Pending' },
    failed: { cls: 'bg-red-500/15 text-red-400', label: 'Failed' },
    dead_letter: { cls: 'bg-red-700/15 text-red-600', label: 'Dead letter' },
  };
  const s = map[status] ?? { cls: 'bg-neutral-500/15 text-neutral-400', label: status };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${s.cls}`}>{s.label}</span>
  );
}

function HealthDot({ healthy }: { healthy?: boolean | null }) {
  if (healthy == null) return <span className="w-2 h-2 rounded-full bg-neutral-500 inline-block" />;
  return healthy
    ? <CheckCircle2 className="h-4 w-4 text-green-400" />
    : <XCircle className="h-4 w-4 text-red-400" />;
}

function fmt(iso: string | null | undefined) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

// ── Main component ────────────────────────────────────────────────────────────

export function ConnectorsTab() {
  const [statuses, setStatuses] = useState<ConnectorStatus[]>([]);
  const [jobs, setJobs] = useState<ConnectorJob[]>([]);
  const [indexes, setIndexes] = useState<IndexStats[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [testingType, setTestingType] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoadingStatus(true);
    try {
      const [statusRes, jobsRes, idxRes] = await Promise.allSettled([
        api.getConnectorStatus(),
        api.getConnectorJobs(),
        api.getIndexStatus(),
      ]);
      if (statusRes.status === 'fulfilled') setStatuses(statusRes.value ?? []);
      if (jobsRes.status === 'fulfilled') setJobs(jobsRes.value?.jobs ?? []);
      if (idxRes.status === 'fulfilled') setIndexes(idxRes.value?.indexes ?? []);
    } catch {
      // non-critical
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const handleTest = async (connectorType: string) => {
    setTestingType(connectorType);
    try {
      const res = await api.testConnector(connectorType);
      if (res?.status === 'ok') {
        toast.success(`${CONNECTOR_META[connectorType]?.label ?? connectorType}: ${res.message}`);
      } else {
        toast.error(`${CONNECTOR_META[connectorType]?.label ?? connectorType}: ${res?.message ?? 'Connection failed'}`);
      }
      await loadAll();
    } catch {
      toast.error('Connection test failed');
    } finally {
      setTestingType(null);
    }
  };

  const handleReindex = async (connectorType: string) => {
    setReindexing(connectorType);
    try {
      if (connectorType === 'sharepoint') await api.reindexSharePoint();
      else if (connectorType === 'org_website') await api.reindexOrgWebsite();
      else if (connectorType === 'onedrive') {
        await api.syncOneDrive(false);
        toast.success('OneDrive sync queued');
        setTimeout(loadAll, 2000);
        return;
      }
      toast.success(`Full reindex queued for ${CONNECTOR_META[connectorType]?.label ?? connectorType}`);
      setTimeout(loadAll, 2000);
    } catch (err: any) {
      toast.error(err?.message || 'Sync/reindex failed');
    } finally {
      setReindexing(null);
    }
  };

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">Enterprise Knowledge Connectors</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Index SharePoint, OneDrive, Email, Planner, and web sources into Azure AI Search
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={loadAll} disabled={loadingStatus}>
          {loadingStatus ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
        </Button>
      </div>

      {/* Connector cards */}
      <div className="space-y-2">
        {statuses.map((s) => {
          const meta = CONNECTOR_META[s.connector_type] ?? {
            label: s.connector_type, icon: <Database className="h-4 w-4" />, description: '',
          };
          const canReindex = ['sharepoint', 'org_website', 'onedrive'].includes(s.connector_type);

          return (
            <div
              key={s.connector_type}
              className={`rounded-lg border p-4 ${s.enabled ? 'bg-card' : 'bg-card/40 opacity-60'}`}
            >
              <div className="flex items-start gap-3">
                <div className={`p-2 rounded-md shrink-0 ${s.enabled ? 'bg-primary/10 text-primary' : 'bg-neutral-800 text-neutral-500'}`}>
                  {meta.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{meta.label}</span>
                    <HealthDot healthy={s.healthy} />
                    {!s.enabled && (
                      <span className="text-xs text-muted-foreground">(disabled in env)</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">{meta.description}</p>
                  <div className="flex gap-4 mt-1.5 text-xs text-muted-foreground">
                    <span>Last sync: {fmt(s.last_sync)}</span>
                    <span>{s.docs_indexed.toLocaleString()} docs</span>
                    {s.errors > 0 && (
                      <span className="text-red-400 flex items-center gap-0.5">
                        <AlertCircle className="h-3 w-3" />{s.errors} errors
                      </span>
                    )}
                  </div>
                </div>

                {s.enabled && (
                  <div className="flex items-center gap-1.5 shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleTest(s.connector_type)}
                      disabled={testingType === s.connector_type}
                      title="Test connection"
                    >
                      {testingType === s.connector_type
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <CheckCircle2 className="h-3.5 w-3.5" />}
                      <span className="ml-1 hidden sm:inline">Test</span>
                    </Button>
                    {canReindex && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleReindex(s.connector_type)}
                        disabled={reindexing === s.connector_type}
                        title={s.connector_type === 'onedrive' ? 'Sync OneDrive' : 'Full reindex'}
                      >
                        {reindexing === s.connector_type
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Play className="h-3.5 w-3.5" />}
                        <span className="ml-1 hidden sm:inline">{s.connector_type === 'onedrive' ? 'Sync' : 'Reindex'}</span>
                      </Button>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {!loadingStatus && statuses.length === 0 && (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            No connector data. Backend may be offline or Search not configured.
          </div>
        )}
      </div>

      {/* Index status */}
      {indexes.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Azure AI Search Indexes
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {indexes.map((idx) => (
              <div key={idx.index_name} className="rounded-lg border bg-card p-3">
                <p className="text-xs font-medium truncate">{idx.index_name}</p>
                {idx.error ? (
                  <p className="text-xs text-red-400 mt-1">{idx.error}</p>
                ) : (
                  <>
                    <p className="text-lg font-bold mt-1">{idx.document_count?.toLocaleString() ?? '—'}</p>
                    <p className="text-xs text-muted-foreground">docs · {idx.storage_size_mb ?? '?'} MB</p>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent jobs */}
      {jobs.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Recent Sync Jobs
          </h4>
          <div className="rounded-lg border overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-neutral-900/50">
                <tr>
                  <th className="text-left px-3 py-2 font-medium text-muted-foreground">Connector</th>
                  <th className="text-left px-3 py-2 font-medium text-muted-foreground">Type</th>
                  <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                  <th className="text-right px-3 py-2 font-medium text-muted-foreground">Docs</th>
                  <th className="text-right px-3 py-2 font-medium text-muted-foreground">Finished</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.slice(0, 10).map((j) => (
                  <tr key={j.id} className="hover:bg-accent/30">
                    <td className="px-3 py-2 capitalize">{j.connector_type.replace('_', ' ')}</td>
                    <td className="px-3 py-2 text-muted-foreground">{j.job_type}</td>
                    <td className="px-3 py-2"><StatusBadge status={j.status} /></td>
                    <td className="px-3 py-2 text-right">{j.docs_processed}</td>
                    <td className="px-3 py-2 text-right text-muted-foreground">{fmt(j.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Config note */}
      <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
        <strong>Configuration:</strong> Connectors are enabled via environment variables.
        SharePoint sites are set in <code className="font-mono">SHAREPOINT_SITES</code>,
        the org website in <code className="font-mono">ORG_WEBSITE_ALLOWLIST</code>.
        Documents auto-index on startup and are available in AI chat immediately.
      </div>
    </div>
  );
}
