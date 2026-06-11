"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { fetcher, api } from "@/lib/api";
import {
  PageHeader, Button, Card, FilterPills, EmptyState, LoadingState,
  PriorityBadge, SourceBadge, ConfidenceMeter, Badge,
} from "@/components/ui";
import {
  Search, FileSpreadsheet, ListChecks, Inbox, Check, X, ExternalLink,
  Edit3, Loader2,
} from "lucide-react";

type StatusFilter = "all" | "open" | "in_progress" | "needs_review" | "done" | "ignored";

export default function TasksPage() {
  const params = useSearchParams();
  const initialStatus = (params.get("status") as StatusFilter) || "all";
  const initialSource = (params.get("source") as "all" | "email" | "teams") || "all";
  const initialPriority = (params.get("priority") as "all" | "high" | "medium" | "low") || "all";

  const [status, setStatus] = useState<StatusFilter>(initialStatus);
  const [priority, setPriority] = useState<"all" | "high" | "medium" | "low">(initialPriority);
  const [source, setSource] = useState<"all" | "email" | "teams">(initialSource);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"created" | "due" | "priority">("created");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [editTask, setEditTask] = useState<any | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");

  // Re-sync filter state when URL params change (e.g. dashboard deep-link)
  useEffect(() => {
    const s = (params.get("status") as StatusFilter) || "all";
    const src = (params.get("source") as "all" | "email" | "teams") || "all";
    const pr = (params.get("priority") as "all" | "high" | "medium" | "low") || "all";
    setStatus(s); setSource(src); setPriority(pr);
  }, [params]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const qs = new URLSearchParams();
  if (status !== "all")   qs.set("status", status);
  if (priority !== "all") qs.set("priority", priority);
  if (source !== "all")   qs.set("source", source);
  if (sort)               qs.set("sort", sort);

  const path = q.trim()
    ? `/api/tasks/search?q=${encodeURIComponent(q.trim())}`
    : `/api/tasks?${qs.toString()}`;
  const { data, isLoading, mutate } = useSWR(path, fetcher);

  const items: any[] = data?.items ?? [];

  const updateStatus = async (id: string, s: string) => {
    setBusyId(id);
    try {
      await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status: s }) });
      mutate();
    } finally {
      setBusyId(null);
    }
  };

  const sendToPlanner = async (id: string) => {
    setBusyId(id);
    try {
      const res = await api(`/api/tasks/${id}/planner`, { method: "POST" });
      if (res?.sync_status === "synced") {
        showToast("Sent to Planner ✓");
      } else {
        showToast(`Planner failed: ${res?.error || "see Settings → Planner"}`);
      }
      mutate();
    } catch (e: any) {
      showToast(`Planner error: ${e?.message || "unknown"}`);
    } finally {
      setBusyId(null);
    }
  };

  const sendAllToPlanner = async () => {
    const ids = items
      .filter((t) => t.status === "open" || t.status === "in_progress" || t.status === "needs_review")
      .map((t) => t.id);
    if (ids.length === 0) {
      showToast("No open tasks to send.");
      return;
    }
    if (!confirm(`Send ${ids.length} task(s) to Planner?`)) return;
    setBulkBusy(true);
    try {
      const res = await api("/api/planner/create-selected-tasks", {
        method: "POST",
        body: JSON.stringify({ task_ids: ids }),
      });
      const synced = (res?.results || []).filter((r: any) => r?.sync_status === "synced").length;
      const failed = (res?.results || []).length - synced;
      showToast(`Planner: ${synced} synced${failed ? `, ${failed} failed` : ""}`);
      mutate();
    } catch (e: any) {
      showToast(`Planner batch error: ${e?.message || "unknown"}`);
    } finally {
      setBulkBusy(false);
    }
  };

  const syncExcel = async () => {
    setBulkBusy(true);
    try {
      const res = await api("/api/excel/sync", { method: "POST", body: JSON.stringify({}) });
      const inserted = res?.inserted ?? 0;
      const updated = res?.updated ?? 0;
      showToast(`Excel: ${inserted} added, ${updated} updated`);
      mutate();
    } catch (e: any) {
      showToast(`Excel error: ${e?.message || "unknown"}`);
    } finally {
      setBulkBusy(false);
    }
  };

  const openEdit = (t: any) => {
    setEditTask(t);
    setEditTitle(t.title || "");
    setEditDescription(t.description || "");
  };

  const saveEdit = async () => {
    if (!editTask) return;
    setBusyId(editTask.id);
    try {
      await api(`/api/tasks/${editTask.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: editTitle, description: editDescription }),
      });
      setEditTask(null);
      mutate();
    } finally {
      setBusyId(null);
    }
  };

  const saveEditAndSend = async () => {
    if (!editTask) return;
    setBusyId(editTask.id);
    try {
      await api(`/api/tasks/${editTask.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: editTitle, description: editDescription }),
      });
      const res = await api(`/api/tasks/${editTask.id}/planner`, { method: "POST" });
      if (res?.sync_status === "synced") {
        showToast("Edited & sent to Planner ✓");
      } else {
        showToast(`Planner failed: ${res?.error || "see Settings"}`);
      }
      setEditTask(null);
      mutate();
    } catch (e: any) {
      showToast(`Error: ${e?.message || "unknown"}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Tasks"
        description="Everything Mela Task Radar has surfaced from your Outlook and Teams. Review, complete, or push them out to your tools."
        actions={
          <>
            <Button
              variant="ghost"
              leftIcon={bulkBusy ? <Loader2 size={14} className="animate-spin" /> : <ListChecks size={14} />}
              onClick={sendAllToPlanner}
              disabled={bulkBusy}
            >
              Send all to Planner
            </Button>
            <Button
              variant="ghost"
              leftIcon={bulkBusy ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />}
              onClick={syncExcel}
              disabled={bulkBusy}
            >
              Sync to Excel
            </Button>
          </>
        }
      />

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-lg shadow-lg bg-ink text-white text-sm">
          {toast}
        </div>
      )}

      {/* Edit modal */}
      {editTask && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
          onClick={() => setEditTask(null)}
        >
          <div
            className="bg-white rounded-xl shadow-2xl max-w-xl w-full p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-ink">Edit task</h3>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted">Title</label>
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="input w-full mt-1"
                placeholder="Task title"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted">Description</label>
              <textarea
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                className="input w-full mt-1 min-h-[120px]"
                placeholder="Description"
              />
            </div>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setEditTask(null)}>Cancel</Button>
              <Button variant="ghost" onClick={saveEdit} disabled={busyId === editTask.id}>
                Save only
              </Button>
              <Button onClick={saveEditAndSend} disabled={busyId === editTask.id} leftIcon={<ListChecks size={14} />}>
                Save & Send to Planner
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Filters card */}
      <Card padded>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="relative flex-1 max-w-md">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-subtle" />
              <input
                placeholder="Search tasks, senders, subjects…"
                className="input pl-9"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as any)}
              className="input max-w-[180px]"
            >
              <option value="created">Newest first</option>
              <option value="due">Due date</option>
              <option value="priority">Priority</option>
            </select>
          </div>

          <div className="flex flex-col gap-3">
            <FilterPills<StatusFilter>
              value={status}
              onChange={setStatus}
              options={[
                { value: "all",          label: "All" },
                { value: "open",         label: "Open" },
                { value: "in_progress",  label: "In progress" },
                { value: "needs_review", label: "Needs review" },
                { value: "done",         label: "Done" },
                { value: "ignored",      label: "Ignored" },
              ]}
            />
            <div className="flex flex-wrap gap-3">
              <FilterPills
                value={priority}
                onChange={(v) => setPriority(v as any)}
                options={[
                  { value: "all",    label: "Any priority" },
                  { value: "high",   label: "High" },
                  { value: "medium", label: "Medium" },
                  { value: "low",    label: "Low" },
                ]}
              />
              <FilterPills
                value={source}
                onChange={(v) => setSource(v as any)}
                options={[
                  { value: "all",   label: "All sources" },
                  { value: "email", label: "Outlook" },
                  { value: "teams", label: "Teams" },
                ]}
              />
            </div>
          </div>
        </div>
      </Card>

      {/* Results */}
      <Card padded={false}>
        {isLoading ? (
          <div className="p-6"><LoadingState rows={5} /></div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Inbox size={20} />}
            title="No tasks match these filters"
            description="Try a wider filter set, or run a scan to bring in new items."
          />
        ) : (
          <ul className="divide-y divide-hairline">
            {items.map((t) => (
              <li key={t.id} className="p-5 hover:bg-canvas/40 transition-colors">
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <Link href={`/tasks/${t.id}`} className="block">
                      <div className="text-[15px] font-medium text-ink hover:text-brand transition-colors">
                        {t.title || "(untitled task)"}
                      </div>
                    </Link>
                    {t.description && (
                      <p className="text-sm text-muted mt-1 line-clamp-2">{t.description}</p>
                    )}
                    <div className="flex flex-wrap items-center gap-2 mt-3">
                      <SourceBadge source={t.source_type} />
                      <PriorityBadge value={t.priority} />
                      {t.status && t.status !== "open" && (
                        <Badge tone={t.status === "done" ? "success" : t.status === "ignored" ? "neutral" : "brand"}>
                          {t.status.replace("_", " ")}
                        </Badge>
                      )}
                      {t.due_date && (
                        <span className="text-xs text-muted">
                          Due {new Date(t.due_date).toLocaleDateString()}
                        </span>
                      )}
                      {t.source_type === "teams" && t.source_meta?.channel_name && (
                        <span className="text-xs text-muted truncate max-w-[260px]">
                          {t.source_meta.team_name ? `${t.source_meta.team_name} / ` : ""}
                          #{t.source_meta.channel_name}
                          {t.source_meta.is_mention ? " · @you" : ""}
                        </span>
                      )}
                      {(t.source_meta?.sender_name || t.sender) && (
                        <span className="text-xs text-muted truncate max-w-[200px]">
                          From {t.source_meta?.sender_name || t.sender}
                        </span>
                      )}
                    </div>
                    {typeof t.confidence === "number" && (
                      <div className="mt-3 max-w-xs"><ConfidenceMeter value={t.confidence} /></div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      title="Mark done (also marks complete in Excel + Planner)"
                      onClick={() => updateStatus(t.id, "done")}
                      className="btn-icon"
                      disabled={busyId === t.id}
                    >
                      {busyId === t.id ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                    </button>
                    <button
                      title="Ignore (removes from active list)"
                      onClick={() => updateStatus(t.id, "ignored")}
                      className="btn-icon"
                      disabled={busyId === t.id}
                    >
                      <X size={16} />
                    </button>
                    <button
                      title="Edit & approve before sending to Planner"
                      onClick={() => openEdit(t)}
                      className="btn-icon"
                      disabled={busyId === t.id}
                    >
                      <Edit3 size={16} />
                    </button>
                    <button
                      title="Send to Planner now (no edit)"
                      onClick={() => sendToPlanner(t.id)}
                      className="btn-icon"
                      disabled={busyId === t.id}
                    >
                      <ListChecks size={16} />
                    </button>
                    <Link href={`/tasks/${t.id}`} className="btn-icon" title="Open">
                      <ExternalLink size={16} />
                    </Link>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

