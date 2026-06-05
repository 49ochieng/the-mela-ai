'use client';

/**
 * Worker Registry tab — admin-only.
 *
 * Lets an admin connect a worker WITHOUT writing code:
 *   1. paste base_url + api key (and pick auth scheme)
 *   2. click "Probe"  → backend /probe discovers capabilities
 *   3. review/edit the suggested manifest
 *   4. click "Save"   → backend PUT /registry/{id}
 *
 * Also lists registered workers with quick health, Test, and Delete
 * actions so connecting and verifying is one continuous flow.
 */

import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Plug,
  PlugZap,
  RefreshCw,
  Trash2,
  Zap,
} from 'lucide-react';

import { Button } from '@/components/ui/Button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/Dialog';
import { Input } from '@/components/ui/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/Select';
import {
  api,
  WorkerAuthScheme,
  WorkerManifest,
  WorkerProbeResult,
  WorkerProtocol,
} from '@/lib/api';
import { cn } from '@/lib/utils';

type WizardStep = 'connect' | 'review';

interface ConnectFormState {
  base_url: string;
  api_key: string;
  auth_header: string;
  health_path: string;
}

const DEFAULT_FORM: ConnectFormState = {
  base_url: '',
  api_key: '',
  auth_header: 'X-Api-Key',
  health_path: '/health',
};

function StatusPill({ status }: { status?: string | null }) {
  const map: Record<string, { dot: string; badge: string; label: string }> = {
    healthy:     { dot: 'bg-emerald-500', badge: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 ring-1 ring-emerald-500/20', label: 'Healthy' },
    degraded:    { dot: 'bg-yellow-500',  badge: 'bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 ring-1 ring-yellow-500/20',   label: 'Degraded' },
    unreachable: { dot: 'bg-red-500',     badge: 'bg-red-500/10 text-red-600 dark:text-red-400 ring-1 ring-red-500/20',               label: 'Unreachable' },
    unconfigured:{ dot: 'bg-slate-400',   badge: 'bg-slate-500/10 text-slate-500 ring-1 ring-slate-400/20',                           label: 'Unconfigured' },
    unknown:     { dot: 'bg-slate-400',   badge: 'bg-slate-500/10 text-slate-500 ring-1 ring-slate-400/20',                           label: 'Unknown' },
  };
  const s = map[status || 'unknown'] ?? map.unknown;
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium', s.badge)}>
      <span className={cn('h-1.5 w-1.5 rounded-full shrink-0', s.dot)} />
      {s.label}
    </span>
  );
}

export function WorkerRegistryTab() {
  const [workers, setWorkers] = useState<WorkerManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [connectOpen, setConnectOpen] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listWorkers();
      setWorkers(list);
    } catch (err: any) {
      toast.error(`Failed to load workers: ${err?.message || err}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDelete = async (workerId: string) => {
    if (!confirm(`Remove worker "${workerId}"? This unregisters it from Mela.`))
      return;
    try {
      await api.deleteWorker(workerId);
      toast.success(`Removed ${workerId}`);
      refresh();
    } catch (err: any) {
      toast.error(`Delete failed: ${err?.message || err}`);
    }
  };

  const handleTest = async (workerId: string) => {
    setTestingId(workerId);
    try {
      const out = await api.testWorker(workerId);
      const r = out.result;
      if (r.success) {
        toast.success(
          `Test passed (${out.capability}, ${r.metadata.latency_ms}ms)`,
        );
      } else {
        toast.error(
          `Test failed: ${r.error?.code ?? 'UNKNOWN'} — ${r.error?.message ?? ''}`,
        );
      }
    } catch (err: any) {
      toast.error(`Test failed: ${err?.message || err}`);
    } finally {
      setTestingId(null);
    }
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {workers.length > 0 && (
            <span className="inline-flex items-center justify-center h-5 min-w-5 px-1.5 rounded-full bg-primary/10 text-primary text-xs font-semibold">
              {workers.length}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5 mr-1.5', loading && 'animate-spin')} />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setConnectOpen(true)}>
            <Plug className="h-3.5 w-3.5 mr-1.5" />
            Connect worker
          </Button>
        </div>
      </div>

      {loading && workers.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin mb-3 opacity-50" />
          <span className="text-sm">Loading workers…</span>
        </div>
      ) : workers.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 border-2 border-dashed border-border rounded-xl text-muted-foreground">
          <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center mb-3">
            <Plug className="h-6 w-6 opacity-40" />
          </div>
          <p className="text-sm font-medium mb-1">No workers registered</p>
          <p className="text-xs text-center max-w-xs mb-4">
            Connect an external MCP worker and Mela will discover its capabilities automatically.
          </p>
          <Button size="sm" onClick={() => setConnectOpen(true)}>
            <Plug className="h-3.5 w-3.5 mr-1.5" />
            Connect your first worker
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {workers.map((w) => (
            <div
              key={w.id}
              className="group flex items-center gap-4 p-4 rounded-xl border bg-card hover:shadow-sm transition-all duration-150"
            >
              {/* Icon */}
              <div className="w-10 h-10 rounded-lg bg-primary/8 flex items-center justify-center shrink-0">
                <PlugZap className="h-5 w-5 text-primary" />
              </div>

              {/* Info */}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                  <span className="font-semibold text-sm text-foreground">{w.display_name}</span>
                  <StatusPill status={w.status} />
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <code className="font-mono text-[11px] bg-muted px-1.5 py-0.5 rounded">{w.id}</code>
                  <span>{w.protocol.toUpperCase()} · v{w.version}</span>
                  <span className="flex items-center gap-1">
                    <Zap className="h-3 w-3" />
                    {w.capabilities.length} {w.capabilities.length === 1 ? 'capability' : 'capabilities'}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground/70 mt-0.5 truncate font-mono">{w.base_url}</p>
              </div>

              {/* Actions */}
              <div className="flex gap-1.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleTest(w.id)}
                  disabled={testingId === w.id}
                >
                  {testingId === w.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5" />
                  )}
                  <span className="ml-1.5">Test</span>
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleDelete(w.id)}
                  className="text-destructive hover:text-destructive hover:bg-destructive/10"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConnectWorkerModal
        open={connectOpen}
        onOpenChange={setConnectOpen}
        onSaved={() => {
          setConnectOpen(false);
          refresh();
        }}
      />
    </div>
  );
}

// ── Connect modal: 3-step Connect → Review → Save ──────────────────────

function ConnectWorkerModal({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [step, setStep] = useState<WizardStep>('connect');
  const [form, setForm] = useState<ConnectFormState>(DEFAULT_FORM);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<WorkerProbeResult | null>(null);
  const [manifest, setManifest] = useState<WorkerManifest | null>(null);
  const [saving, setSaving] = useState(false);

  // Reset on close.
  useEffect(() => {
    if (!open) {
      setStep('connect');
      setForm(DEFAULT_FORM);
      setProbeError(null);
      setProbeResult(null);
      setManifest(null);
    }
  }, [open]);

  const runProbe = async () => {
    if (!form.base_url.trim()) {
      setProbeError('base URL is required');
      return;
    }
    setProbing(true);
    setProbeError(null);
    try {
      const result = await api.probeWorker({
        base_url: form.base_url.trim(),
        api_key: form.api_key.trim() || undefined,
        auth_header: form.auth_header.trim() || 'X-Api-Key',
        health_path: form.health_path.trim() || '/health',
      });
      if (!result.success) {
        setProbeError(
          `${result.error_code ?? 'PROBE_FAILED'}: ${result.error_message ?? 'unknown error'}`,
        );
        setProbeResult(null);
        return;
      }
      setProbeResult(result);
      // Build editable manifest from suggestion.
      const built: WorkerManifest = {
        id: result.suggested_id || 'worker',
        display_name:
          result.suggested_display_name || result.suggested_id || 'Worker',
        version: result.suggested_version || '0.1.0',
        protocol: 'mcp' as WorkerProtocol,
        base_url: result.base_url,
        health_check_url: form.health_path.trim() || '/health',
        auth_scheme: (form.api_key.trim()
          ? 'api_key'
          : 'none') as WorkerAuthScheme,
        auth_config: form.api_key.trim()
          ? { header: result.suggested_auth_header || form.auth_header }
          : {},
        capabilities: result.capabilities.map((c) => ({
          name: c.name,
          description: c.description || c.name,
          input_params: c.input_params || {},
          output_shape: {},
          is_async: c.is_async,
          estimated_ms: 1000,
        })),
      };
      setManifest(built);
      setStep('review');
    } catch (err: any) {
      setProbeError(err?.message || String(err));
    } finally {
      setProbing(false);
    }
  };

  const save = async () => {
    if (!manifest) return;
    if (!manifest.id.trim() || !manifest.display_name.trim()) {
      toast.error('id and display_name are required');
      return;
    }
    setSaving(true);
    try {
      const saved = await api.upsertWorker(manifest);
      toast.success(`Connected ${saved.display_name}`);
      onSaved();
    } catch (err: any) {
      toast.error(`Save failed: ${err?.message || err}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg rounded-2xl shadow-2xl [&>button]:hidden">
        <DialogHeader className="pb-2">
          <DialogTitle className="flex items-center gap-2 text-base">
            <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center">
              <Plug className="h-3.5 w-3.5 text-white" />
            </div>
            Connect a worker
          </DialogTitle>
          <DialogDescription className="sr-only">Connect an MCP worker</DialogDescription>
        </DialogHeader>

        {/* Step indicator */}
        <div className="flex items-center gap-2 mb-4">
          {(['connect', 'review'] as WizardStep[]).map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              {i > 0 && <div className={cn('h-px flex-1 w-8', step === 'review' ? 'bg-primary' : 'bg-border')} />}
              <div className={cn(
                'flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full transition-colors',
                step === s
                  ? 'bg-primary text-white'
                  : i === 0 && step === 'review'
                  ? 'bg-primary/10 text-primary'
                  : 'bg-muted text-muted-foreground',
              )}>
                <span>{i + 1}</span>
                <span>{s === 'connect' ? 'Connect' : 'Review'}</span>
              </div>
            </div>
          ))}
        </div>

        {step === 'connect' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs font-semibold block mb-1.5">
                Worker base URL <span className="text-destructive">*</span>
              </label>
              <Input
                placeholder="https://taskradar.example.com/mcp"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                autoFocus
                className="font-mono text-sm"
              />
              <p className="text-[11px] text-muted-foreground mt-1.5">
                The MCP endpoint that responds to <code className="bg-muted px-1 rounded">tools/list</code>
              </p>
            </div>
            <div>
              <label className="text-xs font-semibold block mb-1.5">
                API key <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <Input
                type="password"
                placeholder="Leave blank if the worker is public"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-semibold block mb-1.5">Auth header</label>
                <Input
                  placeholder="X-Api-Key"
                  value={form.auth_header}
                  onChange={(e) => setForm({ ...form, auth_header: e.target.value })}
                  className="font-mono text-sm"
                />
              </div>
              <div>
                <label className="text-xs font-semibold block mb-1.5">Health path</label>
                <Input
                  placeholder="/health"
                  value={form.health_path}
                  onChange={(e) => setForm({ ...form, health_path: e.target.value })}
                  className="font-mono text-sm"
                />
              </div>
            </div>

            {probeError && (
              <div className="flex items-start gap-2.5 p-3 rounded-xl border border-destructive/30 bg-destructive/5 text-xs">
                <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-destructive" />
                <div>
                  <div className="font-semibold text-destructive">Probe failed</div>
                  <div className="text-muted-foreground mt-0.5">{probeError}</div>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={probing}>
                Cancel
              </Button>
              <Button onClick={runProbe} disabled={probing}>
                {probing ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                    Probing…
                  </>
                ) : (
                  <>
                    <PlugZap className="h-3.5 w-3.5 mr-1.5" />
                    Probe & discover
                  </>
                )}
              </Button>
            </div>
          </div>
        )}

        {step === 'review' && manifest && probeResult && (
          <div className="space-y-4">
            {/* Discovery banner */}
            <div className="flex items-start gap-3 p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5">
              <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5 text-emerald-500" />
              <div className="text-xs">
                <div className="font-semibold text-emerald-700 dark:text-emerald-400">
                  Discovered {probeResult.capabilities.length} {probeResult.capabilities.length === 1 ? 'capability' : 'capabilities'}
                </div>
                <div className="text-muted-foreground mt-0.5">
                  Health check:{' '}
                  {probeResult.health_ok === true
                    ? `OK (${probeResult.health_latency_ms}ms)`
                    : probeResult.health_ok === false
                    ? 'failed — worker may not implement /health, that is fine'
                    : 'not attempted'}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-semibold block mb-1.5">
                  Worker id <span className="text-destructive">*</span>
                </label>
                <Input
                  value={manifest.id}
                  onChange={(e) => setManifest({ ...manifest, id: e.target.value })}
                  className="font-mono text-sm"
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  Used in tool names: <code className="bg-muted px-1 rounded">worker__{manifest.id}__…</code>
                </p>
              </div>
              <div>
                <label className="text-xs font-semibold block mb-1.5">
                  Display name <span className="text-destructive">*</span>
                </label>
                <Input
                  value={manifest.display_name}
                  onChange={(e) => setManifest({ ...manifest, display_name: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs font-semibold block mb-1.5">Version</label>
                <Input
                  value={manifest.version}
                  onChange={(e) => setManifest({ ...manifest, version: e.target.value })}
                  className="font-mono text-sm"
                />
              </div>
              <div>
                <label className="text-xs font-semibold block mb-1.5">Protocol</label>
                <Select
                  value={manifest.protocol}
                  onValueChange={(v) => setManifest({ ...manifest, protocol: v as WorkerProtocol })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="mcp">MCP</SelectItem>
                    <SelectItem value="rest">REST</SelectItem>
                    <SelectItem value="webhook">Webhook</SelectItem>
                    <SelectItem value="grpc">gRPC</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold block mb-1.5">Base URL</label>
              <Input
                value={manifest.base_url}
                onChange={(e) => setManifest({ ...manifest, base_url: e.target.value })}
                className="font-mono text-sm"
              />
            </div>

            {/* Capabilities */}
            <div>
              <div className="text-xs font-semibold mb-2">
                Capabilities
                <span className="ml-1.5 inline-flex items-center justify-center h-4 min-w-4 px-1 rounded-full bg-primary/10 text-primary text-[10px] font-semibold">
                  {manifest.capabilities.length}
                </span>
              </div>
              <div className="border rounded-xl divide-y max-h-40 overflow-y-auto bg-muted/20">
                {manifest.capabilities.map((c) => (
                  <div key={c.name} className="px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <code className="font-semibold text-foreground">{c.name}</code>
                      {c.is_async && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-500 ring-1 ring-blue-500/20">
                          async
                        </span>
                      )}
                    </div>
                    {c.description && (
                      <div className="text-muted-foreground mt-0.5 truncate">{c.description}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div className="flex justify-between gap-2 pt-1">
              <Button variant="ghost" onClick={() => setStep('connect')} disabled={saving}>
                ← Back
              </Button>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
                  Cancel
                </Button>
                <Button onClick={save} disabled={saving}>
                  {saving ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                      Saving…
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1.5" />
                      Connect worker
                    </>
                  )}
                </Button>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
