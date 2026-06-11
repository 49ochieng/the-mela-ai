"use client";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { api, fetcher } from "@/lib/api";
import { useSession } from "@/lib/useSession";
import {
  Card, CardHeader, MetricCard, PageHeader, Button,
  EmptyState, LoadingState, SourceBadge, PriorityBadge, StatusDot,
} from "@/components/ui";
import {
  ListTodo, AlertTriangle, CalendarClock, ClipboardCheck,
  Radar, ArrowRight, Inbox, Plug, FileSpreadsheet, ListChecks,
  MessageSquare, Mail,
} from "lucide-react";

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export default function Dashboard() {
  const { user } = useSession();
  const today    = useSWR("/api/tasks/today", fetcher);
  const overdue  = useSWR("/api/tasks/overdue", fetcher);
  const open     = useSWR("/api/tasks?status=open&limit=1", fetcher);
  const review   = useSWR("/api/tasks?status=needs_review&limit=1", fetcher);
  // Per-source breakdown so users can see "what came from Teams" at a glance.
  const fromTeams  = useSWR("/api/tasks?source=teams&limit=1", fetcher);
  const fromEmail  = useSWR("/api/tasks?source=email&limit=1", fetcher);
  const scans    = useSWR("/api/scans", fetcher, { refreshInterval: 30000 });
  const conns    = useSWR("/api/connections", fetcher);

  const lastScan = (scans.data ?? [])[0];

  // Toast banner when a recent scan just completed
  const [scanToast, setScanToast] = useState<string | null>(null);
  const lastSeenScanIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!lastScan?.id) return;
    // Initialise on first load — don't toast for stale completions
    if (lastSeenScanIdRef.current === null) {
      lastSeenScanIdRef.current = lastScan.id;
      return;
    }
    if (lastSeenScanIdRef.current !== lastScan.id && lastScan.status === "succeeded") {
      const created = (lastScan.tasks_created_count ?? 0);
      lastSeenScanIdRef.current = lastScan.id;
      if (created > 0) {
        setScanToast(`Scan complete — ${created} new task${created === 1 ? "" : "s"} created.`);
        // Refresh widget counts
        today.mutate(); overdue.mutate(); open.mutate(); review.mutate();
        fromTeams.mutate(); fromEmail.mutate();
        setTimeout(() => setScanToast(null), 6000);
      }
    }
  }, [lastScan?.id, lastScan?.status, lastScan?.tasks_created_count]);

  const runScan = async () => {
    await api("/api/scans/run", { method: "POST", body: JSON.stringify({ source: "all" }) });
    setTimeout(() => { today.mutate(); overdue.mutate(); scans.mutate(); }, 800);
  };

  const firstName = user?.display_name?.split(" ")[0] || "there";

  return (
    <div className="space-y-8">
      {scanToast && (
        <div className="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-lg shadow-lg bg-success text-white text-sm flex items-center gap-2">
          <Radar size={16} /> {scanToast}
          <Link href="/tasks?status=open" className="underline ml-2">View</Link>
        </div>
      )}
      <PageHeader
        eyebrow="Today"
        title={`${greeting()}, ${firstName}.`}
        description="Here's what's on your radar — pulled from Outlook and Teams, prioritized by AI."
        actions={
          <>
            <Link href="/tasks" className="btn-ghost text-sm">View all tasks</Link>
            <Button leftIcon={<Radar size={14} />} onClick={runScan}>Run scan</Button>
          </>
        }
      />

      {/* Metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          label="Open tasks" value={open.data?.total ?? "—"}
          icon={<ListTodo size={16} />} href="/tasks?status=open"
        />
        <MetricCard
          label="Due today" value={today.data?.total ?? "—"}
          icon={<CalendarClock size={16} />} href="/tasks"
        />
        <MetricCard
          label="Overdue" value={overdue.data?.total ?? "—"}
          icon={<AlertTriangle size={16} />} href="/tasks"
        />
        <MetricCard
          label="Needs review" value={review.data?.total ?? "—"}
          icon={<ClipboardCheck size={16} />} href="/tasks?status=needs_review"
        />
      </div>

      {/* Source breakdown — see exactly what came from where */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link href="/tasks?source=teams" className="group">
          <Card className="hover:border-brand transition-colors">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs uppercase tracking-wide text-muted">From Microsoft Teams</div>
                <div className="text-2xl font-semibold text-ink mt-1">
                  {fromTeams.data?.total ?? "—"}
                </div>
                <div className="text-xs text-muted mt-1">
                  Action items detected across your channels and chats
                </div>
              </div>
              <div className="w-10 h-10 rounded-lg bg-purple-500/10 text-purple-500 flex items-center justify-center group-hover:scale-110 transition-transform">
                <MessageSquare size={20} />
              </div>
            </div>
          </Card>
        </Link>
        <Link href="/tasks?source=email" className="group">
          <Card className="hover:border-brand transition-colors">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs uppercase tracking-wide text-muted">From Outlook</div>
                <div className="text-2xl font-semibold text-ink mt-1">
                  {fromEmail.data?.total ?? "—"}
                </div>
                <div className="text-xs text-muted mt-1">
                  Action items detected in your inbox
                </div>
              </div>
              <div className="w-10 h-10 rounded-lg bg-sky-500/10 text-sky-500 flex items-center justify-center group-hover:scale-110 transition-transform">
                <Mail size={20} />
              </div>
            </div>
          </Card>
        </Link>
      </div>

      {/* Today + recent scan */}
      <div className="grid lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Today's focus"
            subtitle="Tasks due today, sorted by priority"
            action={<Link href="/tasks" className="text-sm text-brand hover:underline inline-flex items-center gap-1">All tasks <ArrowRight size={13} /></Link>}
          />
          {today.isLoading ? <LoadingState rows={3} /> : (today.data?.items?.length ?? 0) === 0 ? (
            <EmptyState
              icon={<Inbox size={20} />}
              title="No tasks due today"
              description="When Mela Task Radar finds something due today, it'll show up here."
            />
          ) : (
            <ul className="divide-y divide-hairline">
              {[...(today.data!.items as any[])]
                .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0))
                .slice(0, 6).map((t: any) => (
                <li key={t.id}>
                  <Link href={`/tasks/${t.id}`} className="flex items-start gap-3 py-3 hover:bg-canvas/60 -mx-2 px-2 rounded-xl transition-colors">
                    <div className="mt-1.5"><StatusDot tone={t.priority === "high" ? "danger" : t.priority === "medium" ? "warning" : "success"} /></div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-ink truncate">{t.title}</div>
                      <div className="text-xs text-muted mt-1 flex items-center gap-2">
                        <SourceBadge source={t.source} />
                        {t.due_date && <span>· Due {new Date(t.due_date).toLocaleDateString()}</span>}
                      </div>
                    </div>
                    <PriorityBadge value={t.priority} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Latest scan" subtitle="Most recent radar sweep" />
          {scans.isLoading ? <LoadingState rows={2} /> : !lastScan ? (
            <EmptyState
              icon={<Radar size={20} />}
              title="No scans yet"
              description="Run your first scan to start populating your radar."
              action={<Button onClick={runScan} leftIcon={<Radar size={14} />}>Run scan</Button>}
            />
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <StatusDot tone={lastScan.status === "completed" ? "success" : lastScan.status === "failed" ? "danger" : "warning"} />
                <span className="text-sm font-medium text-ink capitalize">{lastScan.status}</span>
                <span className="text-xs text-muted">· {lastScan.scan_type}</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <Stat n={lastScan.messages_scanned ?? 0} label="Scanned" />
                <Stat n={lastScan.tasks_created ?? 0}   label="Tasks" />
                <Stat n={lastScan.errors_count ?? 0}    label="Errors" />
              </div>
              <div className="text-xs text-muted">
                Started {lastScan.started_at ? new Date(lastScan.started_at).toLocaleString() : "—"}
              </div>
              <Link href="/scans" className="text-sm text-brand hover:underline inline-flex items-center gap-1">
                View scan history <ArrowRight size={13} />
              </Link>
            </div>
          )}
        </Card>
      </div>

      {/* Overdue + integrations */}
      <div className="grid lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader title="Needs your attention" subtitle="Overdue tasks first" />
          {overdue.isLoading ? <LoadingState rows={3} /> : (overdue.data?.items?.length ?? 0) === 0 ? (
            <EmptyState
              icon={<ClipboardCheck size={20} />}
              title="You're all caught up"
              description="No overdue tasks right now. Nice work."
            />
          ) : (
            <ul className="divide-y divide-hairline">
              {overdue.data!.items.slice(0, 5).map((t: any) => (
                <li key={t.id}>
                  <Link href={`/tasks/${t.id}`} className="flex items-start gap-3 py-3 hover:bg-canvas/60 -mx-2 px-2 rounded-xl transition-colors">
                    <div className="mt-1.5"><StatusDot tone="danger" /></div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-ink truncate">{t.title}</div>
                      <div className="text-xs text-muted mt-1 flex items-center gap-2">
                        <SourceBadge source={t.source} />
                        {t.due_date && <span>· Was due {new Date(t.due_date).toLocaleDateString()}</span>}
                      </div>
                    </div>
                    <PriorityBadge value={t.priority} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Integration health" />
          <div className="space-y-3">
            <IntegrationRow
              icon={<Plug size={15} />}
              name="Microsoft 365"
              status={(conns.data?.items?.find?.((c: any) => c.provider === "microsoft" && c.status === "connected")) ? "connected" : "disconnected"}
              href="/settings/connections"
            />
            <IntegrationRow
              icon={<FileSpreadsheet size={15} />}
              name="Excel sync"
              status="optional"
              href="/settings/excel"
            />
            <IntegrationRow
              icon={<ListChecks size={15} />}
              name="Planner sync"
              status="optional"
              href="/settings/planner"
            />
          </div>
        </Card>
      </div>
    </div>
  );
}

function Stat({ n, label }: { n: number | string; label: string }) {
  return (
    <div className="rounded-xl bg-canvas px-3 py-2.5">
      <div className="text-xl font-semibold text-ink tabular-nums">{n}</div>
      <div className="text-[11px] text-muted">{label}</div>
    </div>
  );
}

function IntegrationRow({
  icon, name, status, href,
}: { icon: React.ReactNode; name: string; status: "connected" | "disconnected" | "optional"; href: string }) {
  const tone = status === "connected" ? "success" : status === "disconnected" ? "danger" : "neutral";
  const label = status === "connected" ? "Connected" : status === "disconnected" ? "Not connected" : "Optional";
  return (
    <Link href={href} className="flex items-center gap-3 px-3 py-2.5 rounded-xl border border-hairline hover:bg-canvas/60 transition-colors">
      <span className="w-8 h-8 rounded-lg bg-canvas text-ink/70 flex items-center justify-center">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink">{name}</div>
        <div className="text-xs text-muted flex items-center gap-1.5 mt-0.5">
          <StatusDot tone={tone as any} /> {label}
        </div>
      </div>
      <ArrowRight size={14} className="text-subtle" />
    </Link>
  );
}

