'use client';

import { useState, useEffect, useCallback } from 'react';
import { api, ConnectorStatus, IndexStats } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { RefreshCw, CheckCircle, XCircle, AlertCircle, Loader2, Database } from 'lucide-react';

const CONNECTOR_LABELS: Record<string, string> = {
  sharepoint: 'SharePoint',
  onedrive: 'OneDrive',
  email: 'Email (Inbox)',
  planner: 'MS Planner',
  org_website: 'Org Website',
  public_web: 'Public Web',
};

function StatusIcon({ healthy, enabled }: { healthy?: boolean; enabled: boolean }) {
  if (!enabled) return <AlertCircle className="h-4 w-4 text-muted-foreground" />;
  if (healthy === undefined) return <AlertCircle className="h-4 w-4 text-yellow-500" />;
  if (healthy) return <CheckCircle className="h-4 w-4 text-green-500" />;
  return <XCircle className="h-4 w-4 text-destructive" />;
}

export function ConnectorPanel() {
  const [connectors, setConnectors] = useState<ConnectorStatus[]>([]);
  const [indexes, setIndexes] = useState<IndexStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      const [cs, is] = await Promise.all([api.getConnectorStatus(), api.getIndexStatus()]);
      setConnectors(cs);
      setIndexes(is.indexes ?? []);
    } catch {
      // silently ignore — connectors may not be configured
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleReindex = async (type: 'sharepoint' | 'org_website') => {
    setSyncing(type);
    setMessage('');
    try {
      if (type === 'sharepoint') await api.reindexSharePoint();
      else await api.reindexOrgWebsite();
      setMessage(`${CONNECTOR_LABELS[type]} reindex queued. Documents will appear in search within a few minutes.`);
      setTimeout(load, 3000);
    } catch (e: any) {
      setMessage(`Error: ${e?.message ?? 'Sync failed'}`);
    } finally {
      setSyncing(null);
    }
  };

  const handleTest = async (ct: string) => {
    setSyncing(`test-${ct}`);
    try {
      const r = await api.testConnector(ct);
      setMessage(`${CONNECTOR_LABELS[ct] ?? ct}: ${r.message}`);
    } catch (e: any) {
      setMessage(`Test failed: ${e?.message}`);
    } finally {
      setSyncing(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin mr-2" />
        Loading connector status…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Connectors table */}
      <div>
        <h3 className="text-sm font-semibold mb-3">Data Connectors</h3>
        <div className="rounded-lg border divide-y text-sm">
          {connectors.map((c) => (
            <div key={c.connector_type} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2 min-w-0">
                <StatusIcon healthy={c.healthy} enabled={c.enabled} />
                <span className="font-medium">{CONNECTOR_LABELS[c.connector_type] ?? c.connector_type}</span>
                {!c.enabled && <span className="text-xs text-muted-foreground">(disabled)</span>}
              </div>
              <div className="flex items-center gap-4 shrink-0">
                <span className="text-xs text-muted-foreground hidden sm:block">
                  {c.docs_indexed > 0 ? `${c.docs_indexed.toLocaleString()} docs` : 'No docs'}
                  {c.last_sync ? ` · ${new Date(c.last_sync).toLocaleDateString()}` : ''}
                </span>
                {c.enabled && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    disabled={syncing === `test-${c.connector_type}`}
                    onClick={() => handleTest(c.connector_type)}
                  >
                    {syncing === `test-${c.connector_type}` ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : 'Test'}
                  </Button>
                )}
              </div>
            </div>
          ))}
          {connectors.length === 0 && (
            <div className="px-4 py-6 text-center text-muted-foreground text-sm">
              No connectors configured. Set SHAREPOINT_SITES and ORG_WEBSITE_ALLOWLIST in environment.
            </div>
          )}
        </div>
      </div>

      {/* Quick actions */}
      <div>
        <h3 className="text-sm font-semibold mb-3">Sync Actions</h3>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!!syncing}
            onClick={() => handleReindex('sharepoint')}
          >
            {syncing === 'sharepoint' ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
            Re-index SharePoint
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!!syncing}
            onClick={() => handleReindex('org_website')}
          >
            {syncing === 'org_website' ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <RefreshCw className="h-4 w-4 mr-1" />}
            Re-crawl Website
          </Button>
          <Button variant="ghost" size="sm" disabled={!!syncing} onClick={load}>
            <RefreshCw className="h-4 w-4 mr-1" />
            Refresh
          </Button>
        </div>
        {message && (
          <p className="mt-2 text-xs text-muted-foreground">{message}</p>
        )}
      </div>

      {/* Search index stats */}
      {indexes.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold mb-3">Search Indexes</h3>
          <div className="rounded-lg border divide-y text-sm">
            {indexes.map((idx) => (
              <div key={idx.index_name} className="flex items-center justify-between px-4 py-2">
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-muted-foreground" />
                  <span className="font-mono text-xs">{idx.index_name}</span>
                </div>
                {idx.error ? (
                  <span className="text-xs text-destructive">{idx.error}</span>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {(idx.document_count ?? 0).toLocaleString()} docs
                    {idx.storage_size_mb ? ` · ${idx.storage_size_mb} MB` : ''}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
