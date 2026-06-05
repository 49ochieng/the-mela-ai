'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, Workflow, WorkflowCreate, WorkflowUpdate, WorkflowRun } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { toast } from 'sonner';
import {
  Play,
  Plus,
  Pencil,
  Trash2,
  ChevronDown,
  ChevronUp,
  Loader2,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Clock,
  Zap,
  Calendar,
  Hash,
  MousePointer,
} from 'lucide-react';

// ── Helpers ───────────────────────────────────────────────────────────────────

const TRIGGER_META: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  manual:   { label: 'Manual',   icon: <MousePointer className="h-3.5 w-3.5" />, color: 'text-blue-500' },
  schedule: { label: 'Schedule', icon: <Calendar className="h-3.5 w-3.5" />,    color: 'text-violet-500' },
  keyword:  { label: 'Keyword',  icon: <Hash className="h-3.5 w-3.5" />,         color: 'text-amber-500' },
  event:    { label: 'Event',    icon: <Zap className="h-3.5 w-3.5" />,          color: 'text-green-500' },
};

const STATUS_MAP: Record<string, { cls: string; label: string }> = {
  active:   { cls: 'bg-green-500/15 text-green-400',  label: 'Active' },
  paused:   { cls: 'bg-yellow-500/15 text-yellow-400', label: 'Paused' },
  draft:    { cls: 'bg-neutral-500/15 text-neutral-400', label: 'Draft' },
  archived: { cls: 'bg-zinc-500/15 text-zinc-400',    label: 'Archived' },
};

const RUN_STATUS_MAP: Record<string, { cls: string }> = {
  completed: { cls: 'text-green-400' },
  failed:    { cls: 'text-red-400' },
  running:   { cls: 'text-blue-400' },
  pending:   { cls: 'text-yellow-400' },
  skipped:   { cls: 'text-muted-foreground' },
};

function fmt(iso: string | null | undefined) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return iso; }
}

// ── Workflow form ─────────────────────────────────────────────────────────────

interface WFFormState {
  name: string;
  description: string;
  trigger_type: string;
  trigger_config_raw: string; // JSON string
  actions_raw: string;        // JSON string
  status: string;
  visibility: string;
}

const BLANK_FORM: WFFormState = {
  name: '', description: '',
  trigger_type: 'manual',
  trigger_config_raw: '{}',
  actions_raw: '[\n  {"type": "send_message", "config": {"template": ""}}\n]',
  status: 'draft',
  visibility: 'user',
};

function WorkflowForm({
  initial,
  onSave,
  onCancel,
  saving,
}: {
  initial?: Partial<WFFormState>;
  onSave: (d: WFFormState) => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const [form, setForm] = useState<WFFormState>({ ...BLANK_FORM, ...initial });
  const [jsonError, setJsonError] = useState<string | null>(null);

  const validate = () => {
    try {
      JSON.parse(form.trigger_config_raw);
      JSON.parse(form.actions_raw);
      setJsonError(null);
      return true;
    } catch (e: any) {
      setJsonError(e.message);
      return false;
    }
  };

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Name</label>
          <input
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="My workflow"
          />
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Description</label>
          <input
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="What does this workflow do?"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Trigger type</label>
          <select
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.trigger_type}
            onChange={e => setForm(f => ({ ...f, trigger_type: e.target.value }))}
          >
            {Object.entries(TRIGGER_META).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Status</label>
          <select
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.status}
            onChange={e => setForm(f => ({ ...f, status: e.target.value }))}
          >
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">
            Trigger config (JSON)
          </label>
          <textarea
            className="w-full text-xs font-mono rounded-md border bg-background px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring min-h-[60px] resize-y"
            value={form.trigger_config_raw}
            onChange={e => setForm(f => ({ ...f, trigger_config_raw: e.target.value }))}
            spellCheck={false}
          />
          <p className="text-[10px] text-muted-foreground mt-0.5">
            e.g. schedule: {`{"cron":"0 9 * * 1-5"}`} · keyword: {`{"keywords":["compliance"]}`}
          </p>
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">
            Actions (JSON array)
          </label>
          <textarea
            className="w-full text-xs font-mono rounded-md border bg-background px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring min-h-[80px] resize-y"
            value={form.actions_raw}
            onChange={e => setForm(f => ({ ...f, actions_raw: e.target.value }))}
            spellCheck={false}
          />
        </div>
      </div>
      {jsonError && (
        <p className="text-xs text-destructive bg-destructive/10 rounded px-2 py-1">{jsonError}</p>
      )}
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="text-xs px-3 py-1.5 rounded-md border hover:bg-muted transition-colors">
          Cancel
        </button>
        <button
          onClick={() => { if (validate()) onSave(form); }}
          disabled={saving || !form.name.trim()}
          className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}

// ── Run history panel ─────────────────────────────────────────────────────────

function RunHistory({ workflowId }: { workflowId: string }) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listWorkflowRuns(workflowId).then(setRuns).catch(() => {}).finally(() => setLoading(false));
  }, [workflowId]);

  if (loading) return <div className="py-2 text-xs text-muted-foreground">Loading runs…</div>;
  if (runs.length === 0) return <div className="py-2 text-xs text-muted-foreground">No runs yet.</div>;

  return (
    <div className="mt-2 rounded-md border overflow-hidden">
      <table className="w-full text-xs">
        <thead className="bg-muted/40">
          <tr>
            <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Status</th>
            <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Steps</th>
            <th className="text-right px-2 py-1.5 font-medium text-muted-foreground">Finished</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {runs.map(r => (
            <tr key={r.id} className="hover:bg-accent/20">
              <td className={`px-2 py-1.5 capitalize font-medium ${RUN_STATUS_MAP[r.status]?.cls ?? ''}`}>
                {r.status}
              </td>
              <td className="px-2 py-1.5 text-muted-foreground">{r.steps_completed}/{r.steps_total}</td>
              <td className="px-2 py-1.5 text-right text-muted-foreground">{fmt(r.finished_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main tab ──────────────────────────────────────────────────────────────────

export function WorkflowsTab() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [templates, setTemplates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Workflow | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [wfs, tmpl] = await Promise.all([
        api.listWorkflows(),
        api.getWorkflowTemplates().catch(() => ({ templates: [] })),
      ]);
      setWorkflows(wfs);
      setTemplates((tmpl as any).templates ?? []);
    } catch (e: any) {
      setError(e.message ?? 'Failed to load workflows');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toForm = (wf: Workflow): Partial<WFFormState> => ({
    name: wf.name,
    description: wf.description ?? '',
    trigger_type: wf.trigger_type,
    trigger_config_raw: JSON.stringify(wf.trigger_config ?? {}, null, 2),
    actions_raw: JSON.stringify(wf.actions ?? [], null, 2),
    status: wf.status,
    visibility: wf.visibility,
  });

  const handleSave = async (form: WFFormState) => {
    setSaving(true);
    const payload = {
      name: form.name,
      description: form.description || undefined,
      trigger_type: form.trigger_type,
      trigger_config: JSON.parse(form.trigger_config_raw),
      actions: JSON.parse(form.actions_raw),
      status: form.status,
      visibility: form.visibility,
    };
    try {
      if (editing) {
        const updated = await api.updateWorkflow(editing.id, payload as WorkflowUpdate);
        setWorkflows(prev => prev.map(w => w.id === editing.id ? updated : w));
        setEditing(null);
      } else {
        const created = await api.createWorkflow(payload as WorkflowCreate);
        setWorkflows(prev => [...prev, created]);
        setAdding(false);
      }
      toast.success('Workflow saved');
    } catch (e: any) {
      toast.error(e.message ?? 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this workflow?')) return;
    try {
      await api.deleteWorkflow(id);
      setWorkflows(prev => prev.filter(w => w.id !== id));
      toast.success('Workflow deleted');
    } catch (e: any) {
      toast.error(e.message ?? 'Failed to delete');
    }
  };

  const handleRun = async (id: string) => {
    setRunning(id);
    try {
      const run = await api.runWorkflow(id);
      toast.success(`Run completed: ${run.status}`);
      // Refresh to update run_count
      load();
    } catch (e: any) {
      toast.error(e.message ?? 'Run failed');
    } finally {
      setRunning(null);
    }
  };

  const handleFromTemplate = async (tmpl: any) => {
    setSaving(true);
    try {
      const created = await api.createWorkflow({
        name: tmpl.name,
        description: tmpl.description,
        trigger_type: tmpl.trigger_type,
        trigger_config: tmpl.trigger_config,
        actions: tmpl.actions,
        status: 'draft',
        visibility: 'user',
      } as WorkflowCreate);
      setWorkflows(prev => [...prev, created]);
      setShowTemplates(false);
      toast.success('Workflow created from template');
    } catch (e: any) {
      toast.error(e.message ?? 'Failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">Workflow Automation</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Build trigger-action pipelines for recurring tasks and events.
          </p>
        </div>
        <div className="flex gap-1.5">
          <Button variant="ghost" size="sm" onClick={() => setShowTemplates(t => !t)}>
            Templates
          </Button>
          <Button
            size="sm"
            onClick={() => { setAdding(true); setEditing(null); }}
          >
            <Plus className="h-3.5 w-3.5 mr-1" />
            New
          </Button>
        </div>
      </div>

      {error && (
        <div className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">{error}</div>
      )}

      {/* Templates */}
      {showTemplates && templates.length > 0 && (
        <div className="rounded-lg border bg-muted/30 p-3 space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Quick-start templates</p>
          {templates.map((t, i) => (
            <div key={i} className="flex items-start gap-3 p-2 rounded-md bg-card border">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">{t.name}</p>
                <p className="text-xs text-muted-foreground">{t.description}</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => handleFromTemplate(t)} disabled={saving}>
                Use
              </Button>
            </div>
          ))}
        </div>
      )}

      {/* Add form */}
      {adding && (
        <WorkflowForm onSave={handleSave} onCancel={() => setAdding(false)} saving={saving} />
      )}

      {/* Workflow list */}
      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : workflows.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <Zap className="h-6 w-6 text-muted-foreground mx-auto mb-2" />
          <p className="text-sm text-muted-foreground">No workflows yet. Create one or start from a template.</p>
        </div>
      ) : (
        <div className="rounded-lg border divide-y">
          {workflows.map(wf => {
            const triggerMeta = TRIGGER_META[wf.trigger_type] ?? TRIGGER_META.manual;
            const statusInfo = STATUS_MAP[wf.status] ?? STATUS_MAP.draft;
            const isExpanded = expanded === wf.id;

            return (
              <div key={wf.id}>
                {editing?.id === wf.id ? (
                  <div className="p-3">
                    <WorkflowForm
                      initial={toForm(wf)}
                      onSave={handleSave}
                      onCancel={() => setEditing(null)}
                      saving={saving}
                    />
                  </div>
                ) : (
                  <div className="p-3">
                    <div className="flex items-start gap-3">
                      <div className={`shrink-0 mt-0.5 ${triggerMeta.color}`}>{triggerMeta.icon}</div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium">{wf.name}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${statusInfo.cls}`}>
                            {statusInfo.label}
                          </span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground font-medium">
                            {triggerMeta.label}
                          </span>
                        </div>
                        {wf.description && (
                          <p className="text-xs text-muted-foreground mt-0.5">{wf.description}</p>
                        )}
                        <div className="flex gap-3 mt-1 text-[11px] text-muted-foreground">
                          <span>{wf.run_count} run{wf.run_count !== 1 ? 's' : ''}</span>
                          {wf.last_run_at && <span>Last: {fmt(wf.last_run_at)}</span>}
                        </div>
                        {isExpanded && <RunHistory workflowId={wf.id} />}
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={() => setExpanded(isExpanded ? null : wf.id)}
                          className="text-xs text-muted-foreground hover:text-foreground p-1"
                          title="Show run history"
                        >
                          {isExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                        </button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRun(wf.id)}
                          disabled={running === wf.id}
                          title="Run now"
                          className="h-7 px-2"
                        >
                          {running === wf.id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <Play className="h-3.5 w-3.5" />}
                        </Button>
                        <button
                          onClick={() => setEditing(wf)}
                          className="p-1 text-muted-foreground hover:text-foreground"
                          title="Edit"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => handleDelete(wf.id)}
                          className="p-1 text-muted-foreground hover:text-destructive"
                          title="Delete"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {workflows.filter(w => w.status === 'active').length} active · {workflows.length} total
      </p>
    </div>
  );
}
