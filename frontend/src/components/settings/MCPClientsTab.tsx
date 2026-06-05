'use client';

/**
 * MCP Clients tab — admin-only.
 *
 * MCP clients are external apps that talk TO Mela's MCP server (the
 * inverse of WorkerRegistryTab where Mela talks to external workers).
 * Creating a client mints a one-time API key.  We surface that key
 * exactly once in a "show-once" panel with a copy button — once the
 * admin clicks "I've saved it", the plaintext is wiped from
 * sessionStorage and there is no recovery path (only revoke+recreate).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertCircle,
  CheckCircle2,
  Code2,
  Copy,
  ExternalLink,
  Key,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';

import { Button } from '@/components/ui/Button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/Dialog';
import { Input } from '@/components/ui/Input';
import { api, McpClient, McpClientCreated, McpToolDef } from '@/lib/api';

const SCOPE_WILDCARD = '*';
const SHOW_ONCE_KEY_PREFIX = 'mela.mcp.showonce.';

function loadShowOnceKey(clientId: string): string | null {
  if (typeof sessionStorage === 'undefined') return null;
  return sessionStorage.getItem(SHOW_ONCE_KEY_PREFIX + clientId);
}

function persistShowOnceKey(clientId: string, plaintext: string) {
  if (typeof sessionStorage === 'undefined') return;
  sessionStorage.setItem(SHOW_ONCE_KEY_PREFIX + clientId, plaintext);
}

function clearShowOnceKey(clientId: string) {
  if (typeof sessionStorage === 'undefined') return;
  sessionStorage.removeItem(SHOW_ONCE_KEY_PREFIX + clientId);
}

function fmt(iso: string | null) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function MCPClientsTab() {
  const [clients, setClients] = useState<McpClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [showOnce, setShowOnce] = useState<McpClientCreated | null>(null);
  const [tools, setTools] = useState<McpToolDef[]>([]);
  const [mintFor, setMintFor] = useState<McpClient | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const out = await api.listMcpClients(false);
      setClients(out.clients);
    } catch (err: any) {
      toast.error(`Failed to load clients: ${err?.message || err}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    api
      .getMelaCapabilities()
      .then((c) => setTools(c.tools || []))
      .catch(() => undefined);
  }, [refresh]);

  const handleRevoke = async (client: McpClient) => {
    if (!confirm(`Revoke "${client.client_name}"? The key stops working immediately.`))
      return;
    try {
      await api.revokeMcpClient(client.id);
      clearShowOnceKey(client.id);
      toast.success(`Revoked ${client.client_name}`);
      refresh();
    } catch (err: any) {
      toast.error(`Revoke failed: ${err?.message || err}`);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {clients.length > 0 && (
            <span className="inline-flex items-center justify-center h-5 min-w-5 px-1.5 rounded-full bg-primary/10 text-primary text-xs font-semibold">
              {clients.length}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            New client
          </Button>
        </div>
      </div>

      {/* Capabilities surface */}
      {tools.length > 0 && (
        <details className="border rounded-xl bg-muted/20 group">
          <summary className="px-4 py-2.5 cursor-pointer text-xs font-semibold select-none list-none flex items-center justify-between">
            <span className="flex items-center gap-2">
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
              What can MCP clients call?
              <span className="inline-flex items-center justify-center h-4 min-w-4 px-1 rounded-full bg-primary/10 text-primary text-[10px] font-semibold">{tools.length}</span>
            </span>
            <span className="text-muted-foreground text-[10px]">click to expand</span>
          </summary>
          <div className="px-4 pb-3 pt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {tools.map((t) => (
              <div key={t.name} className="truncate">
                <code className="text-foreground font-medium">{t.name}</code>
                {t.description && <span className="ml-1.5">— {t.description}</span>}
              </div>
            ))}
          </div>
        </details>
      )}

      {loading && clients.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin mb-3 opacity-50" />
          <span className="text-sm">Loading clients…</span>
        </div>
      ) : clients.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 border-2 border-dashed border-border rounded-xl text-muted-foreground">
          <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center mb-3">
            <Key className="h-6 w-6 opacity-40" />
          </div>
          <p className="text-sm font-medium mb-1">No MCP clients yet</p>
          <p className="text-xs text-center max-w-xs mb-4">
            Mint a scoped API key for external apps to call Mela's MCP server.
          </p>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            Create your first client
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {clients.map((c) => {
            const cachedKey = loadShowOnceKey(c.id);
            return (
              <div key={c.id} className="group rounded-xl border bg-card p-4 hover:shadow-sm transition-all duration-150">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="w-9 h-9 rounded-lg bg-primary/8 flex items-center justify-center shrink-0">
                      <Key className="h-4.5 w-4.5 text-primary" />
                    </div>
                    <div className="min-w-0">
                      <div className="font-semibold text-sm text-foreground">{c.client_name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {c.tenant_id ? <><span className="font-mono bg-muted px-1 rounded text-[11px]">{c.tenant_id}</span>{' · '}</> : ''}
                        {c.scopes.includes(SCOPE_WILDCARD)
                          ? 'all tools'
                          : `${c.scopes.length} tool${c.scopes.length === 1 ? '' : 's'}`}
                        {' · '}created {fmt(c.created_at)}
                        {c.last_used_at ? ` · last used ${fmt(c.last_used_at)}` : ''}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setMintFor(c)}
                      title="Mint embed token"
                    >
                      <Code2 className="h-3.5 w-3.5 mr-1.5" />
                      Embed
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRevoke(c)}
                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                      title="Revoke client"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                {cachedKey && (
                  <ShowOncePanel
                    clientId={c.id}
                    plaintext={cachedKey}
                    onAcknowledge={() => {
                      clearShowOnceKey(c.id);
                      // force re-render
                      setClients((prev) => [...prev]);
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      <CreateClientModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        tools={tools}
        onCreated={(created) => {
          persistShowOnceKey(created.id, created.api_key);
          setShowOnce(created);
          setCreateOpen(false);
          refresh();
        }}
      />

      {/* Embed token mint modal */}
      {mintFor && (
        <MintEmbedModal
          client={mintFor}
          tools={tools}
          onClose={() => setMintFor(null)}
        />
      )}

      {/* Show-once dialog (also rendered inline above for the new client). */}
      {showOnce && (
        <Dialog
          open={!!showOnce}
          onOpenChange={(v) => {
            if (!v) setShowOnce(null);
          }}
        >
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                Client created
              </DialogTitle>
              <DialogDescription>
                Copy this API key now — Mela will never show it again.
              </DialogDescription>
            </DialogHeader>
            <ShowOncePanel
              clientId={showOnce.id}
              plaintext={showOnce.api_key}
              onAcknowledge={() => {
                clearShowOnceKey(showOnce.id);
                setShowOnce(null);
              }}
            />
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

// ── Show-once key panel (used both inline and inside the post-create modal) ──

function ShowOncePanel({
  clientId,
  plaintext,
  onAcknowledge,
}: {
  clientId: string;
  plaintext: string;
  onAcknowledge: () => void;
}) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plaintext);
      toast.success('API key copied to clipboard');
    } catch {
      toast.error('Copy failed — please select the key manually');
    }
  };
  return (
    <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-xs space-y-3">
      <div className="flex items-start gap-2">
        <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-amber-500" />
        <div>
          <div className="font-semibold text-amber-700 dark:text-amber-400">Save this key now</div>
          <div className="mt-0.5 text-muted-foreground">
            This is the only time we&rsquo;ll show it. If you lose it, revoke and create a new client.
          </div>
        </div>
      </div>
      <div className="flex gap-2">
        <code className="flex-1 px-3 py-2 rounded-lg bg-background border font-mono text-xs break-all">
          {plaintext}
        </code>
        <Button variant="outline" size="sm" onClick={copy} className="shrink-0">
          <Copy className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex justify-end">
        <Button size="sm" onClick={onAcknowledge}>
          I&rsquo;ve saved it
        </Button>
      </div>
    </div>
  );
}

// ── Create modal ───────────────────────────────────────────────────────

function CreateClientModal({
  open,
  onOpenChange,
  tools,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  tools: McpToolDef[];
  onCreated: (c: McpClientCreated) => void;
}) {
  const [name, setName] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [allTools, setAllTools] = useState(true);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setName('');
      setTenantId('');
      setAllTools(true);
      setPicked({});
    }
  }, [open]);

  const scopes = useMemo(() => {
    if (allTools) return [SCOPE_WILDCARD];
    return Object.keys(picked).filter((k) => picked[k]);
  }, [allTools, picked]);

  const submit = async () => {
    const cleanName = name.trim();
    if (!cleanName) {
      toast.error('Client name is required');
      return;
    }
    if (!allTools && scopes.length === 0) {
      toast.error('Pick at least one tool, or grant all');
      return;
    }
    setSaving(true);
    try {
      const created = await api.createMcpClient({
        client_name: cleanName,
        tenant_id: tenantId.trim() || null,
        scopes,
      });
      onCreated(created);
    } catch (err: any) {
      toast.error(`Create failed: ${err?.message || err}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl shadow-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center">
              <Plus className="h-3.5 w-3.5 text-white" />
            </div>
            New MCP client
          </DialogTitle>
          <DialogDescription className="text-xs">
            Mints a one-time API key the client uses to call Mela&rsquo;s MCP server.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <label className="text-xs font-semibold block mb-1.5">
              Client name <span className="text-destructive">*</span>
            </label>
            <Input
              placeholder='e.g. "Acme Corp Slack bot"'
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div>
            <label className="text-xs font-semibold block mb-1.5">
              Tenant scope <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <Input
              placeholder="Leave blank for cross-tenant access"
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
            />
            <p className="text-[11px] text-muted-foreground mt-1.5">
              When set, requests with this key are pinned to one tenant.
            </p>
          </div>
          <div>
            <div className="text-xs font-semibold mb-2">Tool scopes</div>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={allTools}
                onChange={(e) => setAllTools(e.target.checked)}
              />
              Grant access to <strong>all</strong> Mela tools
            </label>
            {!allTools && (
              <div className="mt-2 border rounded-md max-h-48 overflow-y-auto divide-y">
                {tools.length === 0 ? (
                  <div className="p-3 text-xs text-muted-foreground">
                    No tools advertised — refresh and try again.
                  </div>
                ) : (
                  tools.map((t) => (
                    <label
                      key={t.name}
                      className="flex items-start gap-2 p-2 text-xs cursor-pointer hover:bg-muted/40"
                    >
                      <input
                        type="checkbox"
                        checked={!!picked[t.name]}
                        onChange={(e) =>
                          setPicked({ ...picked, [t.name]: e.target.checked })
                        }
                        className="mt-0.5"
                      />
                      <div className="min-w-0">
                        <code className="text-foreground">{t.name}</code>
                        {t.description && (
                          <div className="text-muted-foreground truncate">
                            {t.description}
                          </div>
                        )}
                      </div>
                    </label>
                  ))
                )}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button onClick={submit} disabled={saving}>
              {saving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  Creating&hellip;
                </>
              ) : (
                <>Create &amp; show key</>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Mint embed token modal ────────────────────────────────────────────

function MintEmbedModal({
  client,
  tools,
  onClose,
}: {
  client: McpClient;
  tools: McpToolDef[];
  onClose: () => void;
}) {
  const cachedKey = loadShowOnceKey(client.id) || '';
  const [apiKey, setApiKey] = useState(cachedKey);
  const [userId, setUserId] = useState('');
  const [tenantId, setTenantId] = useState(client.tenant_id || '');
  const [profileMode, setProfileMode] = useState<'personal' | 'work'>('work');
  const [minting, setMinting] = useState(false);
  const [result, setResult] = useState<{
    embed_url: string;
    embed_token: string;
    expires_at: string;
  } | null>(null);

  const allowedTools = useMemo(() => {
    if (client.scopes.includes(SCOPE_WILDCARD)) {
      return tools.map((t) => t.name);
    }
    return client.scopes;
  }, [client.scopes, tools]);

  const submit = async () => {
    const key = apiKey.trim();
    const uid = userId.trim();
    if (!key) {
      toast.error('API key is required');
      return;
    }
    if (!uid) {
      toast.error('user_id is required');
      return;
    }
    setMinting(true);
    try {
      const out = await api.mintEmbedToken(key, {
        user_id: uid,
        tenant_id: tenantId.trim() || null,
        profile_mode: profileMode,
      });
      setResult(out);
      toast.success('Embed token minted');
    } catch (err: any) {
      toast.error(`Mint failed: ${err?.message || err}`);
    } finally {
      setMinting(false);
    }
  };

  const copy = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    toast.success(`${label} copied`);
  };

  const fullUrl = (() => {
    if (!result) return '';
    if (result.embed_url.startsWith('http')) return result.embed_url;
    if (typeof window === 'undefined') return result.embed_url;
    return `${window.location.origin}${result.embed_url}`;
  })();

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Code2 className="h-4 w-4" /> Mint embed token
          </DialogTitle>
          <DialogDescription>
            One-hour JWT for embedding Mela in another app. Auth is the
            client&rsquo;s plaintext key &mdash; Mela never stores it, so paste
            it again here.
          </DialogDescription>
        </DialogHeader>

        {!result ? (
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium block mb-1">
                Client API key <span className="text-red-500">*</span>
              </label>
              <Input
                type="password"
                placeholder={cachedKey ? '(loaded from this session)' : 'mela_…'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs font-medium block mb-1">
                user_id <span className="text-red-500">*</span>
              </label>
              <Input
                placeholder="External user id this token represents"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium block mb-1">
                  tenant_id
                </label>
                <Input
                  placeholder="optional"
                  value={tenantId}
                  onChange={(e) => setTenantId(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs font-medium block mb-1">
                  Profile mode
                </label>
                <select
                  className="w-full h-9 rounded-md border bg-background px-2 text-sm"
                  value={profileMode}
                  onChange={(e) =>
                    setProfileMode(e.target.value as 'personal' | 'work')
                  }
                >
                  <option value="work">work</option>
                  <option value="personal">personal</option>
                </select>
              </div>
            </div>
            <div className="text-[11px] text-muted-foreground">
              Will inherit {allowedTools.length} tool
              {allowedTools.length === 1 ? '' : 's'} from the client
              scope. Token TTL: 60 minutes.
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={onClose} disabled={minting}>
                Cancel
              </Button>
              <Button onClick={submit} disabled={minting}>
                {minting ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                    Minting&hellip;
                  </>
                ) : (
                  'Mint embed token'
                )}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border bg-green-500/5 border-green-500/30 p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-green-700 dark:text-green-400 mb-2">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Token ready &mdash; expires{' '}
                {new Date(result.expires_at).toLocaleString()}
              </div>
              <div className="space-y-2">
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">
                    Embed URL
                  </div>
                  <div className="flex gap-2">
                    <code className="flex-1 px-2 py-1.5 rounded bg-background border font-mono text-[11px] break-all">
                      {fullUrl}
                    </code>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => copy(fullUrl, 'URL')}
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => window.open(fullUrl, '_blank')}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">
                    Raw JWT (for &lt;mela-chat&gt; web component)
                  </div>
                  <div className="flex gap-2">
                    <code className="flex-1 px-2 py-1.5 rounded bg-background border font-mono text-[11px] break-all max-h-20 overflow-y-auto">
                      {result.embed_token}
                    </code>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => copy(result.embed_token, 'JWT')}
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => {
                  setResult(null);
                  setUserId('');
                }}
              >
                Mint another
              </Button>
              <Button onClick={onClose}>Done</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}


