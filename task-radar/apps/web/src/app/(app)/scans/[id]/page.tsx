"use client";
import { use } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import {
  PageHeader, Button, Card, MetricCard, StatusDot,
  EmptyState, LoadingState, Badge,
} from "@/components/ui";
import { ArrowLeft, RotateCcw, AlertTriangle, CheckCircle2 } from "lucide-react";

type Tone = "success" | "warning" | "danger" | "neutral";

function statusTone(status: string): { tone: Tone; label: string } {
  if (status === "completed") return { tone: "success", label: "Completed" };
  if (status === "completed_with_errors") return { tone: "warning", label: "Completed with errors" };
  if (status === "running")   return { tone: "warning", label: "Running" };
  if (status === "queued" || status === "pending") return { tone: "neutral", label: "Queued" };
  if (status === "failed")    return { tone: "danger",  label: "Failed" };
  return { tone: "neutral", label: status || "—" };
}

function eventTone(status: string): Tone {
  if (status === "success") return "success";
  if (status === "no_task" || status === "skipped") return "neutral";
  if (status === "needs_review") return "warning";
  if (status === "error") return "danger";
  return "neutral";
}

export default function ScanDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: scan, mutate, isLoading } = useSWR<any>(`/api/scans/${id}`, fetcher);
  const { data: events } = useSWR<any[]>(`/api/scans/${id}/events?limit=500`, fetcher);

  const retry = async () => {
    await api(`/api/scans/${id}/retry`, { method: "POST", body: "{}" });
    mutate();
  };

  if (isLoading || !scan) {
    return <div className="p-6"><LoadingState rows={6} /></div>;
  }

  const st = statusTone(scan.status);
  const cats: Record<string, number> = scan.error_categories ?? {};
  const grouped = (events ?? []).reduce((acc: Record<string, any[]>, e: any) => {
    (acc[e.stage] = acc[e.stage] || []).push(e);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={
          <Link href="/scans" className="inline-flex items-center gap-1 text-muted hover:text-ink">
            <ArrowLeft size={12} /> Scans
          </Link>
        }
        title="Scan detail"
        description={`Started ${scan.started_at ? new Date(scan.started_at).toLocaleString() : "—"} • Type: ${scan.scan_type}`}
        actions={
          (scan.status === "failed" || scan.status === "completed_with_errors") ? (
            <Button leftIcon={<RotateCcw size={14} />} onClick={retry}>Retry</Button>
          ) : null
        }
      />

      <Card>
        <div className="flex items-center gap-3 mb-4">
          <StatusDot tone={st.tone} />
          <Badge tone={st.tone}>{st.label}</Badge>
          {scan.completed_at && (
            <span className="text-xs text-muted">Completed {new Date(scan.completed_at).toLocaleString()}</span>
          )}
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <MetricCard label="Scanned" value={scan.messages_scanned ?? 0} />
          <MetricCard label="Noise" value={scan.noise_skipped_count ?? 0} />
          <MetricCard label="Dup" value={scan.duplicate_skipped_count ?? 0} />
          <MetricCard label="Sent to AI" value={scan.ai_attempted_count ?? 0} />
          <MetricCard label="AI success" value={scan.ai_success_count ?? 0} icon={<CheckCircle2 size={14} />} />
          <MetricCard label="AI no-task" value={scan.ai_no_task_count ?? 0} />
          <MetricCard label="AI failed" value={scan.ai_failed_count ?? 0} />
          <MetricCard label="Needs review" value={scan.needs_review_count ?? 0} />
          <MetricCard label="Tasks created" value={scan.tasks_created ?? 0} />
          <MetricCard label="Attach failed" value={scan.attachment_failed_count ?? 0} />
          <MetricCard label="Excel failed" value={scan.excel_failed_count ?? 0} />
          <MetricCard label="Planner failed" value={scan.planner_failed_count ?? 0} />
        </div>

        {Object.keys(cats).length > 0 && (
          <div className="mt-5">
            <div className="text-xs uppercase tracking-wider text-muted mb-2">Error categories</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(cats).map(([k, v]) => (
                <Badge key={k} tone="danger">{k}: {v}</Badge>
              ))}
            </div>
          </div>
        )}

        {scan.error_summary && (
          <div className="mt-4 text-sm text-danger bg-danger/5 border border-danger/20 rounded p-3">
            <AlertTriangle size={14} className="inline mr-1" /> {scan.error_summary}
          </div>
        )}
      </Card>

      <Card padded={false}>
        <div className="px-5 py-4 border-b border-hairline">
          <div className="text-sm font-medium text-ink">Per-message events</div>
          <div className="text-xs text-muted">Grouped by pipeline stage. {events?.length ?? 0} event(s).</div>
        </div>
        {(!events || events.length === 0) ? (
          <EmptyState title="No events recorded" description="This scan didn't emit any per-message events." />
        ) : (
          <div className="divide-y divide-hairline">
            {Object.entries(grouped).map(([stage, evs]) => (
              <div key={stage} className="px-5 py-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm font-medium text-ink capitalize">{stage.replace(/_/g, " ")}</div>
                  <div className="text-xs text-muted">{evs.length} event(s)</div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
                        <th className="py-1.5 pr-3">When</th>
                        <th className="py-1.5 pr-3">Status</th>
                        <th className="py-1.5 pr-3">Source</th>
                        <th className="py-1.5 pr-3">Category</th>
                        <th className="py-1.5 pr-3">Message</th>
                        <th className="py-1.5 pr-3">Retryable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evs.map((e: any) => (
                        <tr key={e.id} className="border-t border-hairline/60">
                          <td className="py-1.5 pr-3 text-muted whitespace-nowrap">
                            {e.created_at ? new Date(e.created_at).toLocaleTimeString() : "—"}
                          </td>
                          <td className="py-1.5 pr-3">
                            <Badge tone={eventTone(e.status)}>{e.status}</Badge>
                          </td>
                          <td className="py-1.5 pr-3 text-muted">{e.source_type ?? "—"}</td>
                          <td className="py-1.5 pr-3 text-muted">{e.category ?? "—"}</td>
                          <td className="py-1.5 pr-3 text-ink">{e.message ?? "—"}</td>
                          <td className="py-1.5 pr-3 text-muted">{e.retryable ? "yes" : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
