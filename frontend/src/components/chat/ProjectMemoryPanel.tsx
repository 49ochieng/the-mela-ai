'use client';

import { useState } from 'react';
import { useChatStore } from '@/lib/store';
import { Sparkles, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import { toast } from 'sonner';

export function ProjectMemoryPanel() {
  const { currentProject, deleteProjectMemory } = useChatStore();
  const [expanded, setExpanded] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  if (!currentProject) return null;

  const memories = currentProject.memories || [];
  const visibleMemories = expanded ? memories : memories.slice(0, 5);
  const hasMore = memories.length > 5;

  const handleDelete = async (memoryId: string) => {
    setDeletingId(memoryId);
    try {
      await deleteProjectMemory(currentProject.id, memoryId);
      toast.success('Memory removed');
    } catch {
      toast.error('Failed to remove memory');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="mx-3 mb-2 rounded-lg border bg-card/50 overflow-hidden">
      {/* Header */}
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="flex items-center gap-1.5">
          <Sparkles className="h-3 w-3 text-primary" />
          Project Memory
          {memories.length > 0 && (
            <span className="ml-1 rounded-full bg-primary/10 text-primary px-1.5 py-0.5 text-[10px] font-semibold">
              {memories.length}
            </span>
          )}
        </span>
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>

      {/* Memory list (always show if expanded or there are memories) */}
      {(expanded || memories.length <= 5) && memories.length > 0 && (
        <div className="px-3 pb-2 space-y-1">
          {visibleMemories.map((mem) => (
            <div
              key={mem.id}
              className="group flex items-start gap-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <span className="mt-0.5 text-primary shrink-0">•</span>
              <span className="flex-1 leading-relaxed">{mem.fact}</span>
              <button
                className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                onClick={() => handleDelete(mem.id)}
                disabled={deletingId === mem.id}
                title="Remove memory"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}

          {hasMore && (
            <button
              className="text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1 mt-1"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-2.5 w-2.5" /> Show less
                </>
              ) : (
                <>
                  <ChevronDown className="h-2.5 w-2.5" /> Show {memories.length - 5} more
                </>
              )}
            </button>
          )}
        </div>
      )}

      {memories.length === 0 && (
        <p className="px-3 pb-2 text-[10px] text-muted-foreground italic">
          No memories yet — start chatting to build project memory.
        </p>
      )}
    </div>
  );
}
