"use client";
import useSWR from "swr";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, fetcher } from "@/lib/api";
import {
  PageHeader, Card, CardHeader, Button, Badge,
  PriorityBadge, SourceBadge, ConfidenceMeter, StatusDot,
  LoadingState, ErrorState,
} from "@/components/ui";
import {
  ArrowLeft, Check, X, ListChecks, FileSpreadsheet,
  Calendar, User, ExternalLink, Sparkles, Clock, Copy,
} from "lucide-react";

const STATUS_TONE: Record<string, "neutral" | "brand" | "success" | "warning" | "danger"> = {
  open: "brand",
  in_progress: "warning",
  needs_review: "warning",
  done: "success",
  ignored: "neutral",
  duplicate: "neutral",
};

function fmt(d?: string | null) {
  if (!d) return "—";
  return new Date(d).toLocaleString();
}

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;
  const { data, error, isLoading, mutate } = useSWR(taskId ? `/api/tasks/${taskId}` : null, fetcher);
  const task = data as any;

  if (isLoading) return <LoadingState rows={6} />;
  if (error)     return <ErrorState description="We couldn't load this task." onRetry={() => mutate()} />;
  if (!task)     return <ErrorState title="Task not found." />;

  const action = async (path: string) => {
    await api(path, { method: "POST" });
    mutate();
  };
  const sendToPlanner = async () => {
    await api(`/api/tasks/${taskId}/planner`, { method: "POST" }).catch(() => {});
    mutate();
  };
  const syncToExcel = async () => {
    await api("/api/excel/sync", { method: "POST", body: JSON.stringify({ task_ids: [taskId] }) }).catch(() => {});
    mutate();
  };

  return (
    <div className="space-y-6 max-w-5xl">
      <Link
        href="/tasks"
        className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink transition-colors"
      >
        <ArrowLeft size={14} /> All tasks
      </Link>

      <PageHeader
        eyebrow="Task"
        title={task.title}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="ghost" leftIcon={<X size={14} />} onClick={() => action(`/api/tasks/${taskId}/ignore`)}>
              Ignore
            </Button>
            <Button size="sm" variant="ghost" leftIcon={<Copy size={14} />} onClick={() => action(`/api/tasks/${taskId}/mark-duplicate`)}>
              Duplicate
            </Button>
            <Button size="sm" variant="ghost" leftIcon={<FileSpreadsheet size={14} />} onClick={syncToExcel}>
              Sync to Excel
            </Button>
            <Button size="sm" variant="ghost" leftIcon={<ListChecks size={14} />} onClick={sendToPlanner}>
              Send to Planner
            </Button>
            <Button size="sm" leftIcon={<Check size={14} />} onClick={() => action(`/api/tasks/${taskId}/mark-done`)}>
              Mark done
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={STATUS_TONE[task.status] ?? "neutral"}>
          <StatusDot tone={(STATUS_TONE[task.status] === "brand" ? "neutral" : STATUS_TONE[task.status]) as "neutral" | "success" | "warning" | "danger" ?? "neutral"} />
          {String(task.status).replace("_", " ")}
        </Badge>
        <PriorityBadge value={task.priority} />
        <SourceBadge source={task.source_type} />
        <ConfidenceMeter value={task.confidence} />
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader title="Description" />
            {task.description ? (
              <p className="text-sm text-ink whitespace-pre-wrap leading-relaxed">{task.description}</p>
            ) : (
              <p className="text-sm text-muted italic">No description was extracted for this task.</p>
            )}
          </Card>

          {(task.priority_reasoning || task.evidence) && (
            <Card>
              <CardHeader
                title="AI extraction"
                subtitle="Why Mela flagged this as a task"
                action={<Badge tone="brand"><Sparkles size={11} /> AI</Badge>}
              />
              <div className="space-y-4 text-sm">
                {task.priority_reasoning && (
                  <div>
                    <div className="text-[11px] uppercase tracking-wider text-muted mb-1">Priority reasoning</div>
                    <p className="text-ink">{task.priority_reasoning}</p>
                  </div>
                )}
                {task.evidence && (
                  <div>
                    <div className="text-[11px] uppercase tracking-wider text-muted mb-1">Evidence</div>
                    <blockquote className="border-l-2 border-brand/40 pl-4 italic text-muted">
                      {task.evidence}
                    </blockquote>
                  </div>
                )}
              </div>
            </Card>
          )}

          {task.source_link && (
            <Card>
              <CardHeader title="Source" subtitle="Where this task came from" />
              <a
                href={task.source_link}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm font-medium text-brand hover:underline"
              >
                {task.source_type === "teams" ? "Open in Teams" : "Open original message"} <ExternalLink size={12} />
              </a>
            </Card>
          )}

          {task.source_type === "teams" && task.source_meta && (
            <Card>
              <CardHeader title="Teams details" subtitle="From Microsoft Teams" />
              <dl className="space-y-3 text-sm">
                {task.source_meta.team_name && (
                  <Row icon={<User size={14} />} label="Team">{task.source_meta.team_name}</Row>
                )}
                {task.source_meta.channel_name && (
                  <Row icon={<User size={14} />} label="Channel">#{task.source_meta.channel_name}</Row>
                )}
                {task.source_meta.sender_name && (
                  <Row icon={<User size={14} />} label="Sender">{task.source_meta.sender_name}</Row>
                )}
                {task.source_meta.received_at && (
                  <Row icon={<Clock size={14} />} label="Posted">{fmt(task.source_meta.received_at)}</Row>
                )}
                <Row icon={<Sparkles size={14} />} label="Mentioned you">
                  {task.source_meta.is_mention ? "Yes" : "No"}
                </Row>
                {task.source_meta.subject_or_channel && task.source_meta.subject_or_channel !== task.source_meta.channel_name && (
                  <Row icon={<User size={14} />} label="Subject">{task.source_meta.subject_or_channel}</Row>
                )}
              </dl>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Details" />
            <dl className="space-y-3 text-sm">
              <Row icon={<Calendar size={14} />} label="Due">
                {task.due_date ? fmt(task.due_date) : (task.due_date_raw || "—")}
              </Row>
              <Row icon={<User size={14} />} label="Assigned to">
                {task.assigned_to || "Me"}
              </Row>
              <Row icon={<Clock size={14} />} label="Created">
                {fmt(task.created_at)}
              </Row>
            </dl>
          </Card>

          <Card>
            <CardHeader title="Sync status" />
            {task.syncs && task.syncs.length > 0 ? (
              <ul className="space-y-3">
                {task.syncs.map((s: any, i: number) => (
                  <li key={i} className="flex items-start justify-between gap-3 text-sm">
                    <div className="min-w-0">
                      <div className="font-medium text-ink capitalize">{s.target_type || s.target}</div>
                      <div className="text-xs text-muted">{fmt(s.synced_at)}</div>
                      {s.target_url && (
                        <a href={s.target_url} target="_blank" rel="noopener noreferrer" className="text-xs text-brand hover:underline inline-flex items-center gap-1 mt-1">
                          Open <ExternalLink size={10} />
                        </a>
                      )}
                      {s.error_message && (
                        <div className="text-xs text-danger mt-1">{s.error_message}</div>
                      )}
                    </div>
                    <Badge tone={s.sync_status === "synced" ? "success" : s.sync_status === "failed" ? "danger" : "neutral"}>
                      <StatusDot tone={s.sync_status === "synced" ? "success" : s.sync_status === "failed" ? "danger" : "neutral"} />
                      {s.sync_status}
                    </Badge>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">Not synced yet.</p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Row({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-muted mt-0.5">{icon}</span>
      <div className="flex-1 min-w-0">
        <dt className="text-[11px] uppercase tracking-wider text-muted">{label}</dt>
        <dd className="text-sm text-ink mt-0.5 break-words">{children}</dd>
      </div>
    </div>
  );
}

