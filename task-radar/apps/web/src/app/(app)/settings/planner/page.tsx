"use client";
import useSWR from "swr";
import { useEffect, useState } from "react";
import { api, fetcher } from "@/lib/api";
import { PageHeader, Card, CardHeader, Button } from "@/components/ui";
import { Save } from "lucide-react";

export default function PlannerSettings() {
  const { data, mutate } = useSWR("/api/settings/planner", fetcher);
  const plans = useSWR("/api/planner/plans", fetcher);
  const [form, setForm] = useState<any>({});
  useEffect(() => { if (data) setForm(data); }, [data]);
  const buckets = useSWR(form.planner_plan_id ? `/api/planner/plans/${form.planner_plan_id}/buckets` : null, fetcher);

  if (!data) return <div className="text-muted">Loading…</div>;

  const save = async () => {
    await api("/api/settings/planner", { method: "PATCH", body: JSON.stringify(form) });
    mutate();
  };

  function Toggle({ field, label, hint }: { field: string; label: string; hint?: string }) {
    const checked = !!form[field];
    return (
      <label className="flex items-start gap-3 py-3 cursor-pointer">
        <button
          type="button"
          onClick={() => setForm({ ...form, [field]: !checked })}
          className={`relative shrink-0 mt-0.5 w-10 h-6 rounded-full transition-colors ${
            checked ? "bg-brand" : "bg-canvas border border-hairline"
          }`}
        >
          <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-soft transition-transform ${
            checked ? "translate-x-4" : ""
          }`} />
        </button>
        <span className="flex-1">
          <span className="block text-sm font-medium text-ink">{label}</span>
          {hint && <span className="block text-xs text-muted mt-0.5">{hint}</span>}
        </span>
      </label>
    );
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        eyebrow="Settings"
        title="Planner sync"
        description="Push high-confidence tasks into a Microsoft Planner plan and bucket of your choice."
      />

      <Card>
        <CardHeader title="Behavior" />
        <div className="divide-y divide-hairline">
          <Toggle field="planner_sync_enabled" label="Enable Planner integration" />
          <Toggle
            field="approval_required_for_planner"
            label="Require approval before sending tasks to Planner"
            hint="Recommended — keeps Planner clean by letting you review tasks first."
          />
        </div>
      </Card>

      <Card>
        <CardHeader title="Defaults" subtitle="Where new tasks go when you sync" />
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Default plan</label>
            <select
              className="input"
              value={form.planner_plan_id || ""}
              onChange={(e) => setForm({ ...form, planner_plan_id: e.target.value, planner_bucket_id: null })}
            >
              <option value="">— None —</option>
              {(plans.data ?? []).map((p: any) => (
                <option key={p.id} value={p.id}>{p.title}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Default bucket</label>
            <select
              className="input"
              value={form.planner_bucket_id || ""}
              onChange={(e) => setForm({ ...form, planner_bucket_id: e.target.value })}
              disabled={!form.planner_plan_id}
            >
              <option value="">— None —</option>
              {(buckets.data ?? []).map((b: any) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Auto-sync after each scan"
          subtitle="Automatically send newly-extracted tasks to Planner. Requires a default plan."
        />
        <div className="space-y-2">
          {[
            { value: "none", label: "Off", hint: "Don't auto-sync. Use the manual Sync button on tasks." },
            { value: "high", label: "High priority only", hint: "Recommended — keeps Planner focused on what matters now." },
            { value: "high_medium", label: "High and Medium priority" },
            { value: "all", label: "All new tasks", hint: "Caution — can flood Planner if scans return many low-priority tasks." },
          ].map((opt) => (
            <label key={opt.value} className="flex items-start gap-3 p-3 rounded-md border border-hairline hover:bg-canvas cursor-pointer">
              <input
                type="radio"
                name="auto_sync_to_planner_priority"
                className="mt-1"
                checked={(form.auto_sync_to_planner_priority || "none") === opt.value}
                disabled={!form.planner_plan_id || !form.planner_sync_enabled}
                onChange={() => setForm({ ...form, auto_sync_to_planner_priority: opt.value })}
              />
              <span className="flex-1">
                <span className="block text-sm font-medium text-ink">{opt.label}</span>
                {opt.hint && <span className="block text-xs text-muted mt-0.5">{opt.hint}</span>}
              </span>
            </label>
          ))}
          {(!form.planner_plan_id || !form.planner_sync_enabled) && (
            <p className="text-xs text-muted">
              Enable Planner integration and pick a default plan above to use auto-sync.
            </p>
          )}
        </div>
      </Card>

      <Button leftIcon={<Save size={14} />} onClick={save}>Save changes</Button>
    </div>
  );
}

