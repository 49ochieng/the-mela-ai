"use client";
import Link from "next/link";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import {
  PageHeader, Button, Card, MetricCard, StatusDot,
  EmptyState, LoadingState, Badge,
} from "@/components/ui";
import { Radar, RotateCcw, AlertTriangle, CheckCircle2, Clock, ChevronRight } from "lucide-react";

type Tone = "success" | "warning" | "danger" | "neutral";

function statusTone(status: string): { tone: Tone; label: string } {
  if (status === "completed") return { tone: "success", label: "Completed" };
  if (status === "completed_with_errors") return { tone: "warning", label: "Completed (errors)" };
  if (status === "running")   return { tone: "warning", label: "Running" };
  if (status === "queued" || status === "pending") return { tone: "neutral", label: "Queued" };
  if (status === "failed")    return { tone: "danger",  label: "Failed" };
  if (status === "cancelled") return { tone: "neutral", label: "Cancelled" };
  return { tone: "neutral", label: status || "—" };
}

function ErrorChips({ categories }: { categories: Record<string, number> | null | undefined }) {
  if (!categories) return null;
  const entries = Object.entries(categories);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1 justify-end">
      {entries.slice(0, 4).map(([k, v]) => (
        <span key={k} className="inline-flex items-center text-[10px] px-1.5 py-0.5 rounded bg-danger/10 text-danger border border-danger/20">
          {k}: {v}
        </span>
      ))}
    </div>
  );
}

export default function ScansPage() {
  const { data, mutate, isLoading } = useSWR<any[]>("/api/scans", fetcher);
  const items = data ?? [];

  const run = async () => {
    await api("/api/scans/run", { method: "POST", body: JSON.stringify({ source: "all" }) });
    mutate();
  };

  const retry = async (id: string) => {
    await api(`/api/scans/${id}/retry`, { method: "POST", body: "{}" });
    mutate();
  };

  const completed = items.filter((s) => s.status === "completed").length;
  const withErrors = items.filter((s) => s.status === "completed_with_errors").length;
  const running = items.filter((s) => s.status === "running" || s.status === "queued" || s.status === "pending").length;
  const failed = items.filter((s) => s.status === "failed").length;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Activity"
        title="Scans"
        description="Each scan sweeps Outlook and your Teams channels. Per-stage diagnostics show exactly what happened."
        actions={<Button leftIcon={<Radar size={14} />} onClick={run}>Run scan</Button>}
      />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <MetricCard label="Total scans" value={items.length} icon={<Radar size={16} />} />
        <MetricCard label="Completed" value={completed} icon={<CheckCircle2 size={16} />} />
        <MetricCard label="With errors" value={withErrors} icon={<AlertTriangle size={16} />} />
        <MetricCard label="In progress" value={running} icon={<Clock size={16} />} />
        <MetricCard label="Failed" value={failed} icon={<AlertTriangle size={16} />} />
      </div>

      <Card padded={false}>
        {isLoading ? (
          <div className="p-6"><LoadingState rows={4} /></div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Radar size={20} />}
            title="No scans yet"
            description="Run your first scan to populate your radar."
            action={<Button leftIcon={<Radar size={14} />} onClick={run}>Run scan</Button>}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-muted bg-canvas/60">
                  <th className="px-4 py-3 font-medium">Started</th>
                  <th className="px-4 py-3 font-medium">Type</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-3 py-3 font-medium text-right">Scanned</th>
                  <th className="px-3 py-3 font-medium text-right" title="Filtered as noise">Noise</th>
                  <th className="px-3 py-3 font-medium text-right" title="Already seen">Dup</th>
                  <th className="px-3 py-3 font-medium text-right" title="Sent to AI">AI</th>
                  <th className="px-3 py-3 font-medium text-right" title="AI returned no task">No-task</th>
                  <th className="px-3 py-3 font-medium text-right">Tasks</th>
                  <th className="px-3 py-3 font-medium text-right" title="Needs manual review">Review</th>
                  <th className="px-3 py-3 font-medium text-right">Errors</th>
                  <th className="px-3 py-3 font-medium text-right"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((s: any) => {
                  const st = statusTone(s.status);
                  const errs = s.errors_count ?? 0;
                  return (
                    <tr key={s.id} className="border-t border-hairline hover:bg-canvas/40">
                      <td className="px-4 py-3.5 text-ink whitespace-nowrap">
                        {s.started_at ? new Date(s.started_at).toLocaleString() : "—"}
                      </td>
                      <td className="px-4 py-3.5 text-muted capitalize">{s.scan_type}</td>
                      <td className="px-4 py-3.5">
                        <span className="inline-flex items-center gap-2">
                          <StatusDot tone={st.tone} />
                          <Badge tone={st.tone}>{st.label}</Badge>
                        </span>
                      </td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-ink">{s.messages_scanned ?? 0}</td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-muted">{s.noise_skipped_count ?? 0}</td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-muted">{s.duplicate_skipped_count ?? 0}</td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-muted">{s.ai_attempted_count ?? 0}</td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-muted">{s.ai_no_task_count ?? 0}</td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-ink font-medium">{s.tasks_created ?? 0}</td>
                      <td className="px-3 py-3.5 text-right tabular-nums text-muted">{s.needs_review_count ?? 0}</td>
                      <td className={`px-3 py-3.5 text-right tabular-nums ${errs > 0 ? "text-danger" : "text-muted"}`}>
                        {errs}
                        <ErrorChips categories={s.error_categories} />
                      </td>
                      <td className="px-3 py-3.5 text-right">
                        <div className="inline-flex items-center gap-2 justify-end">
                          <Link href={`/scans/${s.id}`} className="btn-ghost text-xs inline-flex items-center gap-1" title="Details">
                            Details <ChevronRight size={12} />
                          </Link>
                          {(s.status === "failed" || s.status === "completed_with_errors") && (
                            <button onClick={() => retry(s.id)} className="btn-ghost text-xs inline-flex items-center gap-1" title="Retry">
                              <RotateCcw size={12} /> Retry
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
