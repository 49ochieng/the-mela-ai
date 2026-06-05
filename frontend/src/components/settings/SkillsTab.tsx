'use client';

import { useEffect, useState, useCallback } from 'react';
import { api, Skill, SkillCreate, SkillUpdate } from '@/lib/api';

const CATEGORY_LABELS: Record<string, string> = {
  writing: 'Writing',
  data: 'Data & Analytics',
  coding: 'Coding',
  research: 'Research',
  spreadsheet: 'Spreadsheet',
  compliance: 'Compliance',
  general: 'General',
};

const CATEGORY_COLORS: Record<string, string> = {
  writing: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  data: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  coding: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  research: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  spreadsheet: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  compliance: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  general: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
};

function CategoryBadge({ category }: { category: string }) {
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[category] ?? CATEGORY_COLORS.general}`}>
      {CATEGORY_LABELS[category] ?? category}
    </span>
  );
}

interface SkillFormState {
  name: string;
  description: string;
  category: string;
  trigger_keywords: string; // comma-separated
  instruction_block: string;
}

const DEFAULT_FORM: SkillFormState = {
  name: '', description: '', category: 'general',
  trigger_keywords: '', instruction_block: '',
};

function SkillForm({
  initial,
  onSave,
  onCancel,
  saving,
}: {
  initial?: Partial<SkillFormState>;
  onSave: (data: SkillFormState) => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const [form, setForm] = useState<SkillFormState>({ ...DEFAULT_FORM, ...initial });

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Name</label>
          <input
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="My skill"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Category</label>
          <select
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.category}
            onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
          >
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Trigger keywords (comma-separated)</label>
          <input
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.trigger_keywords}
            onChange={e => setForm(f => ({ ...f, trigger_keywords: e.target.value }))}
            placeholder="analyze, data, metrics"
          />
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Description</label>
          <input
            className="w-full text-sm rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
            value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Brief description of what this skill does"
          />
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-muted-foreground mb-1 block">Instruction block</label>
          <textarea
            className="w-full text-sm rounded-md border bg-background px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring min-h-[100px] resize-y"
            value={form.instruction_block}
            onChange={e => setForm(f => ({ ...f, instruction_block: e.target.value }))}
            placeholder="When helping with this topic: always start by... use the format..."
          />
        </div>
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="text-xs px-3 py-1.5 rounded-md border hover:bg-muted transition-colors">
          Cancel
        </button>
        <button
          onClick={() => onSave(form)}
          disabled={saving || !form.name.trim() || !form.instruction_block.trim()}
          className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}

// ── Main tab ──────────────────────────────────────────────────────────────

export function SkillsTab() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [saving, setSaving] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setSkills(await api.listSkills());
    } catch (e: any) {
      setError(e.message ?? 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleToggle = async (skill: Skill) => {
    try {
      const updated = await api.updateSkill(skill.id, { is_enabled: !skill.is_enabled });
      setSkills(prev => prev.map(s => s.id === skill.id ? updated : s));
    } catch (e: any) {
      setError(e.message ?? 'Failed to update');
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this skill?')) return;
    try {
      await api.deleteSkill(id);
      setSkills(prev => prev.filter(s => s.id !== id));
    } catch (e: any) {
      setError(e.message ?? 'Failed to delete');
    }
  };

  const toFormState = (skill: Skill): Partial<SkillFormState> => ({
    name: skill.name,
    description: skill.description ?? '',
    category: skill.category,
    trigger_keywords: skill.trigger_keywords
      ? (() => { try { return JSON.parse(skill.trigger_keywords!).join(', '); } catch { return ''; } })()
      : '',
    instruction_block: skill.instruction_block,
  });

  const handleSave = async (form: SkillFormState) => {
    setSaving(true);
    const keywords = form.trigger_keywords
      .split(',').map(k => k.trim()).filter(Boolean);
    try {
      if (editing) {
        const updated = await api.updateSkill(editing.id, {
          name: form.name, description: form.description,
          category: form.category, trigger_keywords: keywords,
          instruction_block: form.instruction_block,
        } as SkillUpdate);
        setSkills(prev => prev.map(s => s.id === editing.id ? updated : s));
        setEditing(null);
      } else {
        const created = await api.createSkill({
          name: form.name, description: form.description,
          category: form.category, trigger_keywords: keywords,
          instruction_block: form.instruction_block,
          visibility: 'user',
        } as SkillCreate);
        setSkills(prev => [...prev, created]);
        setAdding(false);
      }
    } catch (e: any) {
      setError(e.message ?? 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const categories = Array.from(new Set(skills.map(s => s.category)));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">AI Skills</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Skills add domain-specific guidance when your message matches their trigger keywords.
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
        <SkillForm onSave={handleSave} onCancel={() => setAdding(false)} saving={saving} />
      )}

      {loading ? (
        <div className="text-center text-sm text-muted-foreground py-6">Loading…</div>
      ) : skills.length === 0 ? (
        <div className="text-center text-sm text-muted-foreground py-8">No skills loaded.</div>
      ) : (
        <div className="rounded-lg border divide-y">
          {skills.map(skill => (
            <div key={skill.id} className={!skill.is_enabled ? 'opacity-50' : ''}>
              {editing?.id === skill.id ? (
                <div className="p-3">
                  <SkillForm
                    initial={toFormState(skill)}
                    onSave={handleSave}
                    onCancel={() => setEditing(null)}
                    saving={saving}
                  />
                </div>
              ) : (
                <div className="p-3">
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium">{skill.name}</span>
                        <CategoryBadge category={skill.category} />
                        {skill.is_builtin && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 font-medium">
                            Built-in
                          </span>
                        )}
                      </div>
                      {skill.description && (
                        <p className="text-xs text-muted-foreground mt-0.5">{skill.description}</p>
                      )}
                      {expanded === skill.id && skill.trigger_keywords && (
                        <p className="text-xs text-muted-foreground mt-1">
                          <span className="font-medium">Triggers:</span>{' '}
                          {(() => { try { return JSON.parse(skill.trigger_keywords).join(', '); } catch { return skill.trigger_keywords; } })()}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        onClick={() => setExpanded(expanded === skill.id ? null : skill.id)}
                        className="text-xs text-muted-foreground hover:text-foreground px-1"
                        title="Show keywords"
                      >
                        {expanded === skill.id ? '▲' : '▼'}
                      </button>
                      <button
                        onClick={() => handleToggle(skill)}
                        className={`relative inline-flex h-4 w-8 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${skill.is_enabled ? 'bg-primary' : 'bg-muted-foreground/30'}`}
                        role="switch"
                        aria-checked={skill.is_enabled}
                        title={skill.is_enabled ? 'Disable' : 'Enable'}
                      >
                        <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${skill.is_enabled ? 'translate-x-4' : 'translate-x-0'}`} />
                      </button>
                      {!skill.is_builtin && (
                        <>
                          <button onClick={() => setEditing(skill)} className="text-xs text-muted-foreground hover:text-foreground px-1">Edit</button>
                          <button onClick={() => handleDelete(skill.id)} className="text-xs text-destructive hover:text-destructive/80 px-1">Del</button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {skills.filter(s => s.is_enabled).length} of {skills.length} skills active.
        Built-in skills cannot be deleted but can be disabled.
      </p>
    </div>
  );
}
