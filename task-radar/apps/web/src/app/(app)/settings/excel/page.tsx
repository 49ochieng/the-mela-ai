"use client";
import useSWR from "swr";
import { useEffect, useState } from "react";
import { api, fetcher } from "@/lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, StatusDot, EmptyState } from "@/components/ui";
import { FileSpreadsheet, RefreshCw, ExternalLink, Save, Wand2 } from "lucide-react";

export default function ExcelSettings() {
  const { data, mutate } = useSWR("/api/settings/excel", fetcher);
  const status = useSWR("/api/excel/status", fetcher);
  const [form, setForm] = useState<any>({});
  useEffect(() => { if (data) setForm(data); }, [data]);

  if (!data) return <div className="text-muted">Loading…</div>;

  const save = async () => {
    await api("/api/settings/excel", { method: "PATCH", body: JSON.stringify(form) });
    mutate();
  };
  const ensure = async () => {
    await api("/api/excel/create-or-update-workbook", { method: "POST" });
    status.mutate();
  };
  const sync = async () => {
    await api("/api/excel/sync", { method: "POST", body: JSON.stringify({}) });
    status.mutate();
  };

  const hasWorkbook = !!status.data?.workbook_url;

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        eyebrow="Settings"
        title="Excel sync"
        description="Mirror tasks into a workbook in your OneDrive — perfect for weekly reviews and status reports."
      />

      <Card>
        <CardHeader title="Auto-sync" subtitle="Push new tasks into Excel after each scan" />
        <label className="flex items-center gap-3 cursor-pointer">
          <button
            type="button"
            onClick={() => setForm({ ...form, excel_sync_enabled: !form.excel_sync_enabled })}
            className={`relative shrink-0 w-10 h-6 rounded-full transition-colors ${
              form.excel_sync_enabled ? "bg-brand" : "bg-canvas border border-hairline"
            }`}
          >
            <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-soft transition-transform ${
              form.excel_sync_enabled ? "translate-x-4" : ""
            }`} />
          </button>
          <span className="text-sm text-ink font-medium">Auto-sync new tasks to Excel after each scan</span>
        </label>
        {form.excel_sync_enabled && (
          <label className="flex items-center gap-3 cursor-pointer mt-4 pt-4 border-t border-hairline">
            <button
              type="button"
              onClick={() => setForm({ ...form, auto_archive_to_excel: !form.auto_archive_to_excel })}
              className={`relative shrink-0 w-10 h-6 rounded-full transition-colors ${
                form.auto_archive_to_excel ? "bg-brand" : "bg-canvas border border-hairline"
              }`}
            >
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-soft transition-transform ${
                form.auto_archive_to_excel ? "translate-x-4" : ""
              }`} />
            </button>
            <span className="text-sm text-ink">Archive every new task automatically (uncheck to push tasks manually only)</span>
          </label>
        )}
        <div className="mt-5">
          <Button leftIcon={<Save size={14} />} onClick={save}>Save</Button>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Workbook"
          subtitle="Mela Task Radar maintains a single TaskInbox workbook in your OneDrive."
          action={
            hasWorkbook ? (
              <Badge tone="success"><StatusDot tone="success" /> Ready</Badge>
            ) : (
              <Badge tone="neutral"><StatusDot tone="neutral" /> Not created</Badge>
            )
          }
        />
        {hasWorkbook ? (
          <div className="space-y-4">
            <a
              href={status.data!.workbook_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm font-medium text-brand hover:underline"
            >
              <FileSpreadsheet size={15} /> Open TaskInbox.xlsx <ExternalLink size={12} />
            </a>
            {status.data?.last_sync_at && (
              <div className="text-xs text-muted">
                Last sync: {new Date(status.data.last_sync_at).toLocaleString()}
              </div>
            )}
            <div className="flex gap-2 pt-2 border-t border-hairline">
              <Button variant="ghost" size="sm" leftIcon={<Wand2 size={14} />} onClick={ensure}>
                Repair workbook
              </Button>
              <Button size="sm" leftIcon={<RefreshCw size={14} />} onClick={sync}>
                Sync now
              </Button>
            </div>
          </div>
        ) : (
          <EmptyState
            icon={<FileSpreadsheet size={20} />}
            title="No workbook yet"
            description="Create the TaskInbox workbook in your OneDrive to start syncing tasks."
            action={<Button leftIcon={<Wand2 size={14} />} onClick={ensure}>Create workbook</Button>}
          />
        )}
      </Card>
    </div>
  );
}

