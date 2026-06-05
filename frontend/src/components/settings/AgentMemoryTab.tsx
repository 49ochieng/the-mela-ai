'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/Button';
import {
  FileText, Globe, Trash2, RotateCw, Plus, Tag as TagIcon,
  AlertCircle, CheckCircle2, Loader2, Sparkles,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { AgentMemoryItem, AgentMemoryTag } from '@/lib/api';
import { useChatStore } from '@/lib/store';
import { AddKnowledgeModal } from './AddKnowledgeModal';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

type Subtab = 'all' | 'files' | 'websites' | 'templates';

const TAG_STYLE: Record<AgentMemoryTag, string> = {
  knowledge: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  template:  'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  brand:     'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300',
  policy:    'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  demo:      'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
};

function StatusPill({ status }: { status: string }) {
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-green-600">
        <CheckCircle2 className="h-3 w-3" /> Ready
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-red-600">
        <AlertCircle className="h-3 w-3" /> Failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Loader2 className="h-3 w-3 animate-spin" /> {status}
    </span>
  );
}

export function AgentMemoryTab() {
  const { activeProfile, userFeatures } = useChatStore();
  const isWorkMode = activeProfile === 'work' || activeProfile === 'org';
  const isAdmin = userFeatures?.role === 'admin';

  const [subtab, setSubtab] = useState<Subtab>('all');
  const [items, setItems] = useState<AgentMemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.listAgentMemoryItems();
      setItems(r.items);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load items');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Soft-poll non-terminal items every 4s
  useEffect(() => {
    const pending = items.some((i) => i.status !== 'ready' && i.status !== 'failed');
    if (!pending) return;
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [items, refresh]);

  const filtered = useMemo(() => {
    if (subtab === 'files')     return items.filter((i) => i.source_type !== 'public_web');
    if (subtab === 'websites')  return items.filter((i) => i.source_type === 'public_web');
    if (subtab === 'templates') return items.filter((i) => i.tag === 'template');
    return items;
  }, [items, subtab]);

  const onDelete = async (item: AgentMemoryItem) => {
    if (!confirm(`Delete "${item.title}"? This removes it from the index.`)) return;
    try {
      await api.deleteAgentMemoryItem(item.id);
      setItems((cur) => cur.filter((i) => i.id !== item.id));
      toast.success('Removed');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const onReindex = async (item: AgentMemoryItem) => {
    try {
      const updated = await api.reindexAgentMemoryItem(item.id);
      setItems((cur) => cur.map((i) => (i.id === item.id ? updated : i)));
      toast.success('Reindexing started');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Reindex failed');
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-purple-500" />
            Agent Memory
          </h3>
          <p className="text-xs text-muted-foreground mt-1">
            Files, websites, and templates the agent uses to ground answers and shape output.
          </p>
        </div>
        <Button size="sm" onClick={() => setModalOpen(true)}>
          <Plus className="h-4 w-4 mr-1" /> Add knowledge
        </Button>
      </div>

      {/* Subtabs */}
      <div className="flex gap-1 border-b">
        {(['all', 'files', 'websites', 'templates'] as Subtab[]).map((t) => (
          <button
            key={t}
            onClick={() => setSubtab(t)}
            className={cn(
              'px-3 py-1.5 text-xs font-medium border-b-2 -mb-px capitalize transition-colors',
              subtab === t
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Items */}
      {loading ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin inline mr-2" /> Loading…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          No items yet. Click <strong>Add knowledge</strong> to upload a file or add a website.
        </div>
      ) : (
        <ul className="divide-y rounded-lg border bg-card">
          {filtered.map((item) => (
            <li key={item.id} className="flex items-start gap-3 p-3">
              <div className="p-2 rounded-md bg-muted shrink-0">
                {item.source_type === 'public_web'
                  ? <Globe className="h-4 w-4" />
                  : <FileText className="h-4 w-4" />}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium truncate">{item.title}</span>
                  <span className={cn(
                    'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium',
                    TAG_STYLE[item.tag] || TAG_STYLE.knowledge,
                  )}>
                    <TagIcon className="h-2.5 w-2.5" /> {item.tag}
                  </span>
                  <span className="text-[10px] text-muted-foreground uppercase">{item.scope}</span>
                </div>
                <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                  <StatusPill status={item.status} />
                  {item.chunk_count > 0 && <span>{item.chunk_count} chunks</span>}
                  {item.page_count > 0 && <span>{item.page_count} pages</span>}
                  {item.url && (
                    <a
                      href={item.url} target="_blank" rel="noopener noreferrer"
                      className="hover:underline truncate max-w-[260px]"
                    >
                      {item.url}
                    </a>
                  )}
                </div>
                {item.error_message && item.status === 'failed' && (
                  <p className="text-xs text-red-600 mt-1">{item.error_message}</p>
                )}
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => onReindex(item)}
                  className="p-1.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground"
                  title="Reindex"
                >
                  <RotateCw className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => onDelete(item)}
                  className="p-1.5 rounded hover:bg-accent text-muted-foreground hover:text-red-600"
                  title="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <AddKnowledgeModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        canShareWithOrg={isWorkMode || isAdmin}
        onCreated={(item) => setItems((cur) => [item, ...cur])}
      />
    </div>
  );
}
