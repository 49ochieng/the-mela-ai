'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, Instruction, InstructionCreate } from '@/lib/api';

const SCOPE_LABELS: Record<string, string> = {
  global: 'Global',
  org: 'Organization',
  team: 'Team',
  user: 'Personal',
};

const SCOPE_COLORS: Record<string, string> = {
  global: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  org: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  team: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  user: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
};

function ScopeBadge({ scope }: { scope: string }) {
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${SCOPE_COLORS[scope] ?? SCOPE_COLORS.user}`}>
      {SCOPE_LABELS[scope] ?? scope}
    </span>
  );
}

// ── Add / Edit form ────────────────────────────────────────────────────────

interface FormState {
  name: string;
  content: string;
  scope: string;
  priority: number;
}

const DEFAULT_FORM: FormState = { name: '', content: '', scope: 'user', priority: 100 };

function InstructionForm({
  initial,
  onSave,
  onCancel,
  saving,
}: {
  initial?: Partial<FormState>;
  onSave: (data: FormState) => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const [form, setForm] = useState<FormState>({ ...DEFAULT_FORM, ...initial });

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Name</label>
          <input
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="My instruction"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Scope</label>
          <select
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.scope}
            onChange={e => setForm(f => ({ ...f, scope: e.target.value }))}
          >
            <option value="user">Personal</option>
            <option value="org">Organization (admin)</option>
            <option value="global">Global (admin)</option>
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Priority (lower = first)</label>
          <input
            type="number"
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.priority}
            onChange={e => setForm(f => ({ ...f, priority: parseInt(e.target.value) || 100 }))}
            min={1}
            max={999}
          />
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Instruction content</label>
          <textarea
            className="w-full text-sm rounded-md border bg-background px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring min-h-[100px] resize-y"
            value={form.content}
            onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
            placeholder="Always respond in a professional tone. Use clear headings..."
          />
        </div>
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="text-xs px-3 py-1.5 rounded-md border hover:bg-muted transition-colors">
          Cancel
        </button>
        <button
          onClick={() => onSave(form)}
          disabled={saving || !form.name.trim() || !form.content.trim()}
          className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}

// ── Main tab ──────────────────────────────────────────────────────────────

export function InstructionsTab() {
  const [instructions, setInstructions] = useState<Instruction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Instruction | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setInstructions(await api.listInstructions());
    } catch (e: any) {
      setError(e.message ?? 'Failed to load instructions');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleToggle = async (instr: Instruction) => {
    try {
      const updated = await api.updateInstruction(instr.id, { is_enabled: !instr.is_enabled });
      setInstructions(prev => prev.map(i => i.id === instr.id ? updated : i));
    } catch (e: any) {
      setError(e.message ?? 'Failed to update');
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this instruction?')) return;
    try {
      await api.deleteInstruction(id);
      setInstructions(prev => prev.filter(i => i.id !== id));
    } catch (e: any) {
      setError(e.message ?? 'Failed to delete');
    }
  };

  const handleSave = async (form: FormState) => {
    setSaving(true);
    try {
      if (editing) {
        const updated = await api.updateInstruction(editing.id, form);
        setInstructions(prev => prev.map(i => i.id === editing.id ? updated : i));
        setEditing(null);
      } else {
        const created = await api.createInstruction(form as InstructionCreate);
        setInstructions(prev => [...prev, created]);
        setAdding(false);
      }
    } catch (e: any) {
      setError(e.message ?? 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const byScope = ['global', 'org', 'team', 'user'];
  const grouped = byScope.map(scope => ({
    scope,
    items: instructions.filter(i => i.scope === scope),
  })).filter(g => g.items.length > 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">System Instructions</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Instructions are layered into the AI context for every conversation.
          </p>
        </div>
        <button
          onClick={() => { setAdding(true); setEditing(null); }}
          className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          + Add
        </button>
      </div>

      {error && (
        <div className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">{error}</div>
      )}

      {adding && (
        <InstructionForm
          onSave={handleSave}
          onCancel={() => setAdding(false)}
          saving={saving}
        />
      )}

      {loading ? (
        <div className="text-center text-sm text-muted-foreground py-6">Loading…</div>
      ) : grouped.length === 0 ? (
        <div className="text-center text-sm text-muted-foreground py-8">
          No instructions yet. Add one to customize AI behavior.
        </div>
      ) : (
        <div className="space-y-4">
          {grouped.map(({ scope, items }) => (
            <div key={scope}>
              <div className="flex items-center gap-2 mb-2">
                <ScopeBadge scope={scope} />
                <span className="text-xs text-muted-foreground">{items.length} instruction{items.length !== 1 ? 's' : ''}</span>
              </div>
              <div className="rounded-lg border divide-y">
                {items.map(instr => (
                  <div key={instr.id} className={`p-3 ${!instr.is_enabled ? 'opacity-50' : ''}`}>
                    {editing?.id === instr.id ? (
                      <InstructionForm
                        initial={instr}
                        onSave={handleSave}
                        onCancel={() => setEditing(null)}
                        saving={saving}
                      />
                    ) : (
                      <div className="flex items-start gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">{instr.name}</span>
                            <span className="text-xs text-muted-foreground">p:{instr.priority}</span>
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{instr.content}</p>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            onClick={() => handleToggle(instr)}
                            className={`relative inline-flex h-4 w-8 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${instr.is_enabled ? 'bg-primary' : 'bg-muted-foreground/30'}`}
                            role="switch"
                            aria-checked={instr.is_enabled}
                          >
                            <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${instr.is_enabled ? 'translate-x-4' : 'translate-x-0'}`} />
                          </button>
                          {instr.scope === 'user' && (
                            <>
                              <button onClick={() => setEditing(instr)} className="text-xs text-muted-foreground hover:text-foreground px-1">Edit</button>
                              <button onClick={() => handleDelete(instr.id)} className="text-xs text-destructive hover:text-destructive/80 px-1">Del</button>
                            </>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
