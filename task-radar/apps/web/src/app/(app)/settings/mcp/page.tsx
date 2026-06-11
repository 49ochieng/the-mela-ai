"use client";
import { useEffect, useState } from "react";
import { PageHeader, Card, CardHeader, Badge, StatusDot, Button } from "@/components/ui";
import {
  Zap, Radar, ListTodo, AlertTriangle, Search, CheckCircle2,
  FileSpreadsheet, ListChecks, FileText, Activity, Copy, Check, ShieldCheck,
  KeyRound, Trash2,
} from "lucide-react";
import { api } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const MCP = process.env.NEXT_PUBLIC_MCP_URL || "http://localhost:8090";

type Tool = {
  id: string;
  name: string;
  description: string;
  icon: React.ReactNode;
  category: "Discover" | "Manage" | "Sync" | "Insights";
};

const TOOLS: Tool[] = [
  { id: "scan_for_tasks",      name: "Scan for tasks",        description: "Trigger a scan across Outlook and Teams to discover new action items.", icon: <Radar size={16} />,           category: "Discover" },
  { id: "get_today_tasks",     name: "Get today's tasks",     description: "Return tasks due today, sorted by priority.",                          icon: <ListTodo size={16} />,         category: "Discover" },
  { id: "get_overdue_tasks",   name: "Get overdue tasks",     description: "Return tasks past their due date.",                                    icon: <AlertTriangle size={16} />,    category: "Discover" },
  { id: "search_tasks",        name: "Search tasks",          description: "Full-text search across task titles, senders, and bodies.",            icon: <Search size={16} />,           category: "Discover" },
  { id: "update_task_status",  name: "Update task status",    description: "Mark a task as done, ignored, or back to open.",                       icon: <CheckCircle2 size={16} />,     category: "Manage"   },
  { id: "sync_tasks_to_excel", name: "Sync tasks to Excel",   description: "Push current tasks to your TaskInbox workbook.",                       icon: <FileSpreadsheet size={16} />,  category: "Sync"     },
  { id: "create_planner_task", name: "Create Planner task",   description: "Send a task to your default Planner plan and bucket.",                 icon: <ListChecks size={16} />,       category: "Sync"     },
  { id: "get_task_brief",      name: "Get task brief",        description: "Return a daily summary of what's on the user's radar.",                icon: <FileText size={16} />,         category: "Insights" },
  { id: "get_scan_status",     name: "Get scan status",       description: "Return the status of the most recent scan run.",                       icon: <Activity size={16} />,         category: "Insights" },
];

type AgentTokenInfo = {
  id: string;
  name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
};

type AgentTokenCreated = AgentTokenInfo & { token: string };

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="btn-ghost text-xs px-2.5 py-1.5"
      title="Copy"
    >
      {copied ? <><Check size={12} /> Copied</> : <><Copy size={12} /> Copy</>}
    </button>
  );
}

function ValueRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-hairline last:border-b-0">
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
        <div className="text-sm font-mono text-ink truncate">{value}</div>
      </div>
      <CopyButton text={value} />
    </div>
  );
}

export default function McpSettings() {
  const callEndpoint = `${MCP}/mcp/call`;
  const httpFallback = `${API}/api/mela/tools/<tool_name>`;

  const [tokens, setTokens] = useState<AgentTokenInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState("Mela AI");
  const [minted, setMinted] = useState<AgentTokenCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoading(true);
      const list = await api<AgentTokenInfo[]>("/api/agent-tokens");
      setTokens(list);
    } catch (e: any) {
      setError(e?.message ?? "Failed to load tokens");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function mint() {
    if (!newName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<AgentTokenCreated>("/api/agent-tokens", {
        method: "POST",
        body: JSON.stringify({ name: newName.trim() }),
      });
      setMinted(created);
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? "Failed to mint token");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    if (!confirm("Revoke this token? Any agent using it will immediately lose access.")) return;
    setBusy(true);
    try {
      await api<void>(`/api/agent-tokens/${id}`, { method: "DELETE" });
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? "Failed to revoke token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        eyebrow="Integration"
        title="Mela connection"
        description="Mela AI — and any other Model Context Protocol client — can call Task Radar's tools to discover, search, update, and sync tasks on your behalf."
        actions={
          <Badge tone="success">
            <StatusDot tone="success" /> MCP server online
          </Badge>
        }
      />

      {/* Connection details */}
      <Card padded={false}>
        <div className="px-5 pt-5">
          <CardHeader
            title="Endpoint"
            subtitle="Point your MCP-compatible client at this URL with your personal agent token."
          />
        </div>
        <ValueRow label="MCP endpoint"       value={callEndpoint} />
        <ValueRow label="Auth header"        value="Authorization: Bearer mtr_at_<your-token>" />
        <ValueRow label="HTTP fallback URL"  value={httpFallback} />
      </Card>

      {/* Mint / manage tokens */}
      <Card padded={false}>
        <div className="px-5 pt-5">
          <CardHeader
            title="Your agent tokens"
            subtitle="Each token authenticates Mela as you. Tokens are shown once at creation — copy and store them securely."
            action={<Badge tone="brand"><KeyRound size={11} /> Per-user</Badge>}
          />
        </div>

        {minted && (
          <div className="mx-5 mb-4 rounded-xl border border-success/30 bg-success/5 p-4">
            <div className="flex items-start gap-3">
              <ShieldCheck size={18} className="text-success shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-ink">Token minted — copy it now</div>
                <div className="text-xs text-muted mt-0.5">This is the only time the full value will be shown.</div>
                <div className="mt-2 flex items-center gap-2">
                  <code className="text-xs font-mono break-all bg-ink/5 px-2 py-1 rounded flex-1">{minted.token}</code>
                  <CopyButton text={minted.token} />
                </div>
                <button className="btn-ghost text-xs mt-2" onClick={() => setMinted(null)}>Dismiss</button>
              </div>
            </div>
          </div>
        )}

        <div className="px-5 pb-3 flex flex-wrap items-end gap-2">
          <div className="flex-1 min-w-[200px]">
            <label className="text-[11px] uppercase tracking-wider text-muted">Token name</label>
            <input
              className="mt-1 w-full px-3 py-2 text-sm rounded-lg border border-hairline bg-paper"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Mela AI desktop"
              maxLength={128}
              disabled={busy}
            />
          </div>
          <Button onClick={mint} disabled={busy || !newName.trim()}>
            <KeyRound size={14} /> Mint token
          </Button>
        </div>

        {error && (
          <div className="mx-5 mb-3 text-xs text-danger">{error}</div>
        )}

        <div className="border-t border-hairline">
          {loading ? (
            <div className="px-5 py-4 text-sm text-muted">Loading…</div>
          ) : tokens.length === 0 ? (
            <div className="px-5 py-4 text-sm text-muted">No tokens yet — mint one above to connect Mela.</div>
          ) : (
            <ul>
              {tokens.map((t) => {
                const revoked = !!t.revoked_at;
                const expired = !!t.expires_at && new Date(t.expires_at) < new Date();
                return (
                  <li key={t.id} className="flex items-center gap-3 px-5 py-3 border-b border-hairline last:border-b-0">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-ink truncate">{t.name}</div>
                      <div className="text-[11px] text-muted">
                        Created {new Date(t.created_at).toLocaleDateString()}
                        {t.expires_at && <> · Expires {new Date(t.expires_at).toLocaleDateString()}</>}
                        {t.last_used_at && <> · Last used {new Date(t.last_used_at).toLocaleString()}</>}
                      </div>
                    </div>
                    {revoked ? (
                      <Badge tone="neutral">Revoked</Badge>
                    ) : expired ? (
                      <Badge tone="warning">Expired</Badge>
                    ) : (
                      <Badge tone="success"><StatusDot tone="success" /> Active</Badge>
                    )}
                    {!revoked && (
                      <button
                        className="btn-ghost text-xs px-2.5 py-1.5 text-danger"
                        onClick={() => revoke(t.id)}
                        disabled={busy}
                        title="Revoke"
                      >
                        <Trash2 size={12} /> Revoke
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </Card>

      {/* Setup steps */}
      <Card>
        <CardHeader title="Set up Mela AI in three steps" />
        <ol className="space-y-5">
          {[
            {
              t: "Mint a personal agent token",
              d: "Use the form above. Copy the token immediately — only its hash is stored, so it cannot be shown again.",
            },
            {
              t: "Add Mela Task Radar to your Mela AI workspace",
              d: "In Mela AI, add a new MCP server. Use the endpoint above and the header Authorization: Bearer <your-token>. Mela will discover all tools automatically.",
            },
            {
              t: "Ask Mela to take action",
              d: "Try: \"What's on my radar today?\" or \"Sync my open tasks to Excel.\" Every call runs as you, scoped to your tenant.",
            },
          ].map((s, i) => (
            <li key={i} className="flex gap-4">
              <div className="shrink-0 w-7 h-7 rounded-full bg-brand text-white text-xs font-semibold flex items-center justify-center">
                {i + 1}
              </div>
              <div>
                <div className="text-sm font-medium text-ink">{s.t}</div>
                <div className="text-sm text-muted mt-0.5">{s.d}</div>
              </div>
            </li>
          ))}
        </ol>
      </Card>

      {/* Tools grid */}
      <div>
        <div className="flex items-end justify-between mb-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Available tools</h2>
            <p className="text-sm text-muted">{TOOLS.length} tools, all read-and-write within your tenant.</p>
          </div>
          <Badge tone="brand"><Zap size={11} /> MCP</Badge>
        </div>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {TOOLS.map((t) => (
            <Card key={t.id} className="p-5 hover:shadow-card transition-shadow">
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-xl bg-brand/10 text-brand flex items-center justify-center shrink-0">
                  {t.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-ink">{t.name}</h3>
                  </div>
                  <code className="text-[11px] text-muted font-mono">{t.id}</code>
                </div>
              </div>
              <p className="text-sm text-muted mt-3 leading-relaxed">{t.description}</p>
              <div className="mt-3 flex items-center gap-2">
                <Badge tone="neutral">{t.category}</Badge>
              </div>
            </Card>
          ))}
        </div>
      </div>

      {/* Security */}
      <Card>
        <CardHeader
          title="Security"
          subtitle="What MCP can and can't do"
          action={<Badge tone="success"><ShieldCheck size={11} /> Tenant isolated</Badge>}
        />
        <ul className="space-y-3 text-sm text-muted">
          <li className="flex gap-3"><CheckCircle2 size={16} className="text-success shrink-0 mt-0.5" /> Each call is authenticated by a per-user agent token — there is no shared service key.</li>
          <li className="flex gap-3"><CheckCircle2 size={16} className="text-success shrink-0 mt-0.5" /> The MCP server forces every tool invocation onto the user identified by the bearer token. Caller-supplied user_id arguments are silently overwritten.</li>
          <li className="flex gap-3"><CheckCircle2 size={16} className="text-success shrink-0 mt-0.5" /> Tools never touch other tenants' data — enforced at the database query layer.</li>
          <li className="flex gap-3"><CheckCircle2 size={16} className="text-success shrink-0 mt-0.5" /> Sync actions (Planner, Excel) inherit the same per-user permissions as the web app.</li>
          <li className="flex gap-3"><CheckCircle2 size={16} className="text-success shrink-0 mt-0.5" /> Revoking a token here takes effect on the next request — no propagation delay.</li>
        </ul>
      </Card>
    </div>
  );
}
