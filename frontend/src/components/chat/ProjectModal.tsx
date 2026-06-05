'use client';

import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/Dialog';
import { Button } from '@/components/ui/Button';
import { useChatStore } from '@/lib/store';
import { Project } from '@/lib/api';
import { toast } from 'sonner';

const ICONS = ['📁', '💼', '🔬', '🎨', '📊', '📝', '🚀', '⭐', '🏗️', '🧠'];

const COLORS: { label: string; value: string; bg: string; ring: string }[] = [
  { label: 'Violet', value: 'violet', bg: 'bg-violet-500', ring: 'ring-violet-500' },
  { label: 'Blue', value: 'blue', bg: 'bg-blue-500', ring: 'ring-blue-500' },
  { label: 'Green', value: 'green', bg: 'bg-green-500', ring: 'ring-green-500' },
  { label: 'Amber', value: 'amber', bg: 'bg-amber-500', ring: 'ring-amber-500' },
  { label: 'Rose', value: 'rose', bg: 'bg-rose-500', ring: 'ring-rose-500' },
  { label: 'Slate', value: 'slate', bg: 'bg-slate-500', ring: 'ring-slate-500' },
];

interface Props {
  open: boolean;
  onClose: () => void;
  project?: Project;
  onSaved?: () => void;
}

export function ProjectModal({ open, onClose, project, onSaved }: Props) {
  const { createProject, updateProject } = useChatStore();
  const isEdit = !!project;

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [icon, setIcon] = useState('📁');
  const [color, setColor] = useState('blue');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [saving, setSaving] = useState(false);

  // Populate fields when editing
  useEffect(() => {
    if (project) {
      setName(project.name);
      setDescription(project.description || '');
      setIcon(project.icon || '📁');
      setColor(project.color || 'blue');
      setSystemPrompt(project.system_prompt || '');
    } else {
      setName('');
      setDescription('');
      setIcon('📁');
      setColor('blue');
      setSystemPrompt('');
    }
  }, [project, open]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    try {
      if (isEdit && project) {
        await updateProject(project.id, { name: name.trim(), description: description.trim() || undefined, icon, color, system_prompt: systemPrompt.trim() || undefined });
        toast.success('Project updated');
      } else {
        await createProject({ name: name.trim(), description: description.trim() || undefined, icon, color, system_prompt: systemPrompt.trim() || undefined });
        toast.success('Project created');
      }
      onSaved?.();
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '';
      toast.error(`Failed to ${isEdit ? 'update' : 'create'} project${msg ? `: ${msg}` : ''}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit Project' : 'New Project'}</DialogTitle>
        </DialogHeader>
        <div className="px-6 pb-6 overflow-y-auto">
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Name */}
        <div>
          <label className="block text-sm font-medium mb-1">Project name *</label>
          <input
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            placeholder="e.g. Q4 Planning"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
          />
        </div>

        {/* Icon */}
        <div>
          <label className="block text-sm font-medium mb-1">Icon</label>
          <div className="flex flex-wrap gap-2">
            {ICONS.map((em) => (
              <button
                key={em}
                type="button"
                onClick={() => setIcon(em)}
                className={`w-9 h-9 text-lg rounded-md border transition-colors ${
                  icon === em
                    ? 'border-primary bg-primary/10'
                    : 'border-transparent hover:border-muted-foreground/30'
                }`}
              >
                {em}
              </button>
            ))}
          </div>
        </div>

        {/* Color */}
        <div>
          <label className="block text-sm font-medium mb-1">Color</label>
          <div className="flex gap-2">
            {COLORS.map((c) => (
              <button
                key={c.value}
                type="button"
                onClick={() => setColor(c.value)}
                title={c.label}
                className={`w-7 h-7 rounded-full ${c.bg} transition-all ${
                  color === c.value ? `ring-2 ring-offset-2 ${c.ring}` : ''
                }`}
              />
            ))}
          </div>
        </div>

        {/* Description */}
        <div>
          <label className="block text-sm font-medium mb-1">Description</label>
          <textarea
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            placeholder="Optional description"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        {/* System Prompt */}
        <div>
          <label className="block text-sm font-medium mb-1">Custom AI instructions</label>
          <textarea
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            placeholder="e.g. Always respond with a formal tone. Focus on financial analysis."
            rows={3}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
          />
          <p className="text-xs text-muted-foreground mt-1">
            These instructions are added to every chat in this project.
          </p>
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!name.trim() || saving}>
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Create project'}
          </Button>
        </div>
      </form>
        </div>
      </DialogContent>
    </Dialog>
  );
}
