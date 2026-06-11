"use client";
import useSWR from "swr";
import { useState, useEffect } from "react";
import { api, fetcher } from "@/lib/api";
import { PageHeader, Card, CardHeader, Button, LoadingState } from "@/components/ui";
import { Save, Mail, MessageSquare, Clock } from "lucide-react";

function Toggle({ checked, onChange, label, hint }:
  { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex items-start gap-3 py-3 cursor-pointer">
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`relative shrink-0 mt-0.5 w-10 h-6 rounded-full transition-colors ${
          checked ? "bg-brand" : "bg-canvas border border-hairline"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-soft transition-transform ${
            checked ? "translate-x-4" : ""
          }`}
        />
      </button>
      <span className="flex-1">
        <span className="block text-sm font-medium text-ink">{label}</span>
        {hint && <span className="block text-xs text-muted mt-0.5">{hint}</span>}
      </span>
    </label>
  );
}

export default function ScanSettings() {
  const { data, mutate, isLoading } = useSWR("/api/settings/scan", fetcher);
  const [form, setForm] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  useEffect(() => { if (data) setForm(data); }, [data]);
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await api("/api/settings/scan", { method: "PATCH", body: JSON.stringify(form) });
      mutate();
      setSavedMsg("Saved");
      setTimeout(() => setSavedMsg(null), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        eyebrow="Settings"
        title="Scan settings"
        description="Choose what Mela Task Radar scans, when it runs, and how far back it looks on first sync."
      />

      {isLoading ? <LoadingState rows={3} /> : (
        <>
          <Card>
            <CardHeader title="Sources" subtitle="Enable the channels you want Mela to monitor" />
            <div className="divide-y divide-hairline">
              <Toggle
                checked={!!form.email_scan_enabled}
                onChange={(v) => set("email_scan_enabled", v)}
                label="Scan Outlook email"
                hint="Recent inbox messages will be scanned for action items."
              />
              <Toggle
                checked={!!form.teams_scan_enabled}
                onChange={(v) => set("teams_scan_enabled", v)}
                label="Scan Teams channels"
                hint="Only the channels you select on the Teams page are scanned."
              />
            </div>
          </Card>

          <Card>
            <CardHeader title="Schedule" subtitle="Run scans automatically each day" />
            <div className="space-y-4">
              <Toggle
                checked={!!form.daily_scan_enabled}
                onChange={(v) => set("daily_scan_enabled", v)}
                label="Run a scan automatically each day"
                hint="A quick incremental sweep at the time you choose."
              />
              <div className="grid sm:grid-cols-2 gap-4 pt-2">
                <div>
                  <label className="label flex items-center gap-2"><Clock size={14} /> Daily scan time</label>
                  <input
                    className="input"
                    type="time"
                    value={form.scan_time_local?.slice(0, 5) || "08:00"}
                    onChange={(e) => set("scan_time_local", e.target.value + ":00")}
                  />
                </div>
                <div>
                  <label className="label">Timezone</label>
                  <select
                    className="input"
                    value={form.timezone || "America/Chicago"}
                    onChange={async (e) => {
                      const tz = e.target.value;
                      set("timezone", tz);
                      // Also update the user's profile timezone so the
                      // scheduler honors it for cadence runs.
                      try {
                        await api("/api/me", {
                          method: "PATCH",
                          body: JSON.stringify({ timezone: tz }),
                        });
                      } catch { /* surfaced on save */ }
                    }}
                  >
                    <option value="America/Chicago">Central Time — CT (Chicago)</option>
                    <option value="America/New_York">Eastern Time — ET (New York)</option>
                    <option value="America/Los_Angeles">Pacific Time — PT (Los Angeles)</option>
                    <option value="America/Denver">Mountain Time — MT (Denver)</option>
                    <option value="America/Phoenix">Arizona — MST (no DST)</option>
                    <option value="Pacific/Honolulu">Hawaii — HT</option>
                    <option value="America/Anchorage">Alaska — AKT</option>
                    <option value="UTC">UTC</option>
                  </select>
                  <p className="text-xs text-muted mt-1.5">
                    Used for the daily scan time and for automatic cadence runs.
                    Default is Central Time (CT).
                  </p>
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader title="Initial lookback" subtitle="How far back to scan on first run" />
            <div>
              <label className="label">First-scan lookback (hours)</label>
              <input
                className="input max-w-[200px]"
                type="number"
                value={form.lookback_hours_first_scan ?? 168}
                onChange={(e) => set("lookback_hours_first_scan", Number(e.target.value))}
              />
              <p className="text-xs text-muted mt-1.5">
                Subsequent scans only pick up new messages since the last successful run.
              </p>
            </div>
          </Card>

          <div className="flex items-center gap-3">
            <Button leftIcon={<Save size={14} />} onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save changes"}
            </Button>
            {savedMsg && <span className="text-sm text-success">{savedMsg}</span>}
          </div>
        </>
      )}
    </div>
  );
}

