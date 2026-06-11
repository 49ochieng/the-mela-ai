"use client";
import useSWR from "swr";
import { useEffect, useMemo, useState } from "react";
import { api, fetcher } from "@/lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, EmptyState } from "@/components/ui";
import { Save, ChevronDown, ChevronRight, RefreshCw, Radar, AlertTriangle } from "lucide-react";

type Team = { id: string; displayName: string };
type Channel = { id: string; displayName: string; description?: string | null };

function parseEntry(raw: string): { teamId: string; channelId: string; name: string } | null {
  const parts = raw.split("|");
  if (parts.length < 2) return null;
  return { teamId: parts[0], channelId: parts[1], name: parts[2] || parts[1] };
}

function makeEntry(teamId: string, channelId: string, name: string): string {
  return `${teamId}|${channelId}|${name}`;
}

function ChannelList({
  teamId, selected, toggle,
}: {
  teamId: string;
  selected: Set<string>;
  toggle: (entry: string, on: boolean) => void;
}) {
  const { data, error } = useSWR<{ items: Channel[]; error?: string; message?: string }>(
    `/api/connections/teams/${teamId}/channels`, fetcher,
  );
  if (error) return <div className="text-xs text-danger px-2 py-2">Failed to load channels.</div>;
  if (!data) return <div className="text-xs text-muted px-2 py-2">Loading channels…</div>;
  if (data.error) {
    return (
      <div className="text-xs text-warning px-2 py-2 inline-flex items-center gap-1">
        <AlertTriangle size={12} /> {data.message ?? data.error}
      </div>
    );
  }
  const items = data.items ?? [];
  if (items.length === 0) return <div className="text-xs text-muted px-2 py-2">No channels.</div>;
  return (
    <div className="space-y-1 pl-6 pr-2 py-1">
      {items.map((c) => {
        const entry = makeEntry(teamId, c.id, c.displayName);
        const isOn = selected.has(`${teamId}|${c.id}`);
        return (
          <label key={c.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-canvas/40 rounded px-1.5">
            <input
              type="checkbox"
              checked={isOn}
              onChange={(e) => toggle(entry, e.target.checked)}
              className="accent-brand"
            />
            <span className="text-ink">{c.displayName}</span>
            {c.description ? <span className="text-xs text-muted truncate">— {c.description}</span> : null}
          </label>
        );
      })}
    </div>
  );
}

export default function TeamsSettings() {
  const { data, mutate } = useSWR<any>("/api/settings/teams", fetcher);
  const { data: teamsResp, mutate: refreshTeams, isLoading: teamsLoading } =
    useSWR<{ items: Team[]; error?: string; message?: string }>("/api/connections/teams/joined", fetcher);

  const [form, setForm] = useState<any>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [scanMsg, setScanMsg] = useState<string | null>(null);

  useEffect(() => { if (data) setForm(data); }, [data]);

  const selectedSet = useMemo(() => {
    const s = new Set<string>();
    for (const raw of (form.selected_channel_ids ?? [])) {
      const p = parseEntry(raw);
      if (p) s.add(`${p.teamId}|${p.channelId}`);
    }
    return s;
  }, [form.selected_channel_ids]);

  if (!data) return <div className="text-muted">Loading…</div>;

  const toggleChannel = (entry: string, on: boolean) => {
    const p = parseEntry(entry);
    if (!p) return;
    const key = `${p.teamId}|${p.channelId}`;
    const current: string[] = form.selected_channel_ids ?? [];
    let next: string[];
    if (on) {
      const filtered = current.filter((r) => {
        const pp = parseEntry(r);
        return !pp || `${pp.teamId}|${pp.channelId}` !== key;
      });
      next = [...filtered, entry];
    } else {
      next = current.filter((r) => {
        const pp = parseEntry(r);
        return !pp || `${pp.teamId}|${pp.channelId}` !== key;
      });
    }
    setForm({ ...form, selected_channel_ids: next });
  };

  const save = async () => {
    setSaving(true);
    try {
      await api("/api/settings/teams", {
        method: "PATCH",
        body: JSON.stringify({
          mentions_only: !!form.mentions_only,
          include_thread_context: !!form.include_thread_context,
          teams_scan_enabled: !!form.teams_scan_enabled,
          selected_channel_ids: form.selected_channel_ids ?? [],
        }),
      });
      await mutate();
      setSavedAt(new Date().toLocaleTimeString());
    } finally {
      setSaving(false);
    }
  };

  const runTeamsScan = async () => {
    setScanMsg(null);
    try {
      const r: any = await api("/api/scans/run", {
        method: "POST",
        body: JSON.stringify({ source: "teams" }),
      });
      setScanMsg(`Scan queued (id ${r?.id ?? "?"}). Check Scans page for progress.`);
    } catch (e: any) {
      setScanMsg(`Failed to queue Teams scan: ${e?.message ?? e}`);
    }
  };

  const teams = teamsResp?.items ?? [];
  const teamsError = teamsResp?.error;
  const channelCount = (form.selected_channel_ids ?? []).length;

  const Toggle = ({ value, onChange, label, hint }: { value: boolean; onChange: () => void; label: string; hint: string }) => (
    <label className="flex items-start gap-3 cursor-pointer">
      <button
        type="button"
        onClick={onChange}
        className={`relative shrink-0 mt-0.5 w-10 h-6 rounded-full transition-colors ${value ? "bg-brand" : "bg-canvas border border-hairline"}`}
      >
        <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-soft transition-transform ${value ? "translate-x-4" : ""}`} />
      </button>
      <span className="flex-1">
        <span className="block text-sm font-medium text-ink">{label}</span>
        <span className="block text-xs text-muted mt-0.5">{hint}</span>
      </span>
    </label>
  );

  return (
    <div className="space-y-6 max-w-4xl">
      <PageHeader
        eyebrow="Settings"
        title="Teams"
        description="Pick the Teams channels Mela should watch for action items."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="ghost" leftIcon={<RefreshCw size={14} />} onClick={() => refreshTeams()}>
              Refresh
            </Button>
            <Button leftIcon={<Radar size={14} />} onClick={runTeamsScan} disabled={!form.teams_scan_enabled || channelCount === 0}>
              Run Teams scan
            </Button>
          </div>
        }
      />

      <Card>
        <CardHeader title="General" subtitle="Master switches for Teams scanning" />
        <div className="space-y-4">
          <Toggle
            value={!!form.teams_scan_enabled}
            onChange={() => setForm({ ...form, teams_scan_enabled: !form.teams_scan_enabled })}
            label="Enable Teams scanning"
            hint="When off, scheduled and manual scans skip Teams entirely."
          />
          <Toggle
            value={!!form.mentions_only}
            onChange={() => setForm({ ...form, mentions_only: !form.mentions_only })}
            label="Only scan messages where I'm @mentioned"
            hint="Recommended. Off = scan every message in selected channels."
          />
          <Toggle
            value={!!form.include_thread_context}
            onChange={() => setForm({ ...form, include_thread_context: !form.include_thread_context })}
            label="Include short thread context"
            hint="Pull up to 3 recent replies as context for the AI extractor."
          />
        </div>
      </Card>

      <Card padded={false}>
        <div className="px-5 py-4 border-b border-hairline flex items-center justify-between">
          <div>
            <div className="text-sm font-medium text-ink">Channels</div>
            <div className="text-xs text-muted">{channelCount} selected</div>
          </div>
        </div>

        {teamsError ? (
          <div className="px-5 py-6 text-sm text-warning inline-flex items-center gap-2">
            <AlertTriangle size={14} /> {teamsResp?.message ?? "Cannot list teams. Reconnect Microsoft on the Connections page."}
          </div>
        ) : teamsLoading ? (
          <div className="px-5 py-6 text-sm text-muted">Loading your teams…</div>
        ) : teams.length === 0 ? (
          <EmptyState title="No joined teams" description="You aren't a member of any Microsoft Teams. Join a team in Microsoft Teams, then refresh." />
        ) : (
          <div className="divide-y divide-hairline">
            {teams.map((t) => {
              const open = !!expanded[t.id];
              const selectedHere = (form.selected_channel_ids ?? []).filter((r: string) => {
                const p = parseEntry(r);
                return p && p.teamId === t.id;
              }).length;
              return (
                <div key={t.id}>
                  <button
                    onClick={() => setExpanded({ ...expanded, [t.id]: !open })}
                    className="w-full flex items-center gap-2 px-5 py-3 text-left hover:bg-canvas/40"
                  >
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <span className="text-sm font-medium text-ink">{t.displayName}</span>
                    {selectedHere > 0 && <Badge tone="brand">{selectedHere} selected</Badge>}
                  </button>
                  {open && <ChannelList teamId={t.id} selected={selectedSet} toggle={toggleChannel} />}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <div className="flex items-center gap-3">
        <Button leftIcon={<Save size={14} />} onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
        {savedAt && <span className="text-xs text-muted">Saved at {savedAt}</span>}
        {scanMsg && <span className="text-xs text-muted">{scanMsg}</span>}
      </div>
    </div>
  );
}
