'use client';

import { useState, useEffect, useCallback } from 'react';
import { api, OrgSettings, AuditLog } from '@/lib/api';
import { useChatStore } from '@/lib/store';
import { Switch } from '@/components/ui/Switch';
import { Button } from '@/components/ui/Button';
import { toast } from 'sonner';
import {
  Shield,
  Clock,
  Loader2,
  Users,
  Crown,
  UserX,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Mail,
  ScrollText,
  Zap,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react';

interface AdminUser {
  id: string;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  bootstrap_elevated_at?: string | null;
  tokens_used_today: number;
  daily_token_limit: number;
}

type SectionId = 'users' | 'privacy' | 'bootstrap' | 'tokens' | 'auditlogs';

function Section({
  id,
  title,
  icon,
  open,
  onToggle,
  children,
}: {
  id: SectionId;
  title: string;
  icon: React.ReactNode;
  open: boolean;
  onToggle: (id: SectionId) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      <button
        onClick={() => onToggle(id)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-accent/50 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <span className="text-primary">{icon}</span>
          <span className="text-sm font-medium">{title}</span>
        </div>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && <div className="border-t p-4">{children}</div>}
    </div>
  );
}

export function AdminTab() {
  const fetchFeatures = useChatStore((s) => s.fetchFeatures);
  const [orgSettings, setOrgSettings] = useState<OrgSettings | null>(null);
  const [orgLoading, setOrgLoading] = useState(true);

  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [promoting, setPromoting] = useState<string | null>(null);

  // Token controls
  const [tokenUsage, setTokenUsage] = useState<any[] | null>(null);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [editingLimit, setEditingLimit] = useState<string | null>(null);
  const [limitValue, setLimitValue] = useState<string>('');
  const [savingLimit, setSavingLimit] = useState<string | null>(null);

  // Audit logs
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditFilter, setAuditFilter] = useState('');

  const [openSections, setOpenSections] = useState<Set<SectionId>>(new Set<SectionId>(['users']));

  const toggleSection = (id: SectionId) => {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  useEffect(() => {
    api.getOrgSettings()
      .then(setOrgSettings)
      .catch(() => setOrgSettings({ private_chat_enabled: true, private_chat_retention_days: 20 }))
      .finally(() => setOrgLoading(false));
  }, []);

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const data = await api.listAdminUsers();
      setUsers(data);
    } catch {
      toast.error('Failed to load users');
    } finally {
      setUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleTogglePrivateChat = async (enabled: boolean) => {
    if (!orgSettings) return;
    const prev = orgSettings;
    const updated: OrgSettings = { ...orgSettings, private_chat_enabled: enabled };
    setOrgSettings(updated);
    try {
      await api.updateOrgSettings(updated);
      toast.success(`Private chat ${enabled ? 'enabled' : 'disabled'}`);
      await fetchFeatures();
    } catch {
      toast.error('Failed to update organization settings');
      setOrgSettings(prev);
    }
  };

  const handleRoleChange = async (userId: string, newRole: 'admin' | 'user') => {
    setPromoting(userId);
    try {
      await api.updateAdminUser(userId, { role: newRole });
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, role: newRole } : u))
      );
      toast.success(`User ${newRole === 'admin' ? 'promoted to admin' : 'demoted to user'}`);
    } catch {
      toast.error('Failed to update user role');
    } finally {
      setPromoting(null);
    }
  };

  const handleToggleActive = async (userId: string, isActive: boolean) => {
    setPromoting(userId);
    try {
      await api.updateAdminUser(userId, { is_active: isActive });
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, is_active: isActive } : u))
      );
      toast.success(`User ${isActive ? 'activated' : 'deactivated'}`);
    } catch {
      toast.error('Failed to update user');
    } finally {
      setPromoting(null);
    }
  };

  const loadTokenUsage = useCallback(async () => {
    setTokenLoading(true);
    try {
      const data = await api.getAdminTokenUsage(7);
      setTokenUsage(data.users ?? []);
    } catch {
      toast.error('Failed to load token usage');
    } finally {
      setTokenLoading(false);
    }
  }, []);

  const handleSaveLimit = async (userId: string) => {
    const limit = parseInt(limitValue, 10);
    if (isNaN(limit) || limit < 0) { toast.error('Invalid limit'); return; }
    setSavingLimit(userId);
    try {
      await api.updateAdminUser(userId, { daily_token_limit: limit });
      setTokenUsage(prev => prev?.map(u => u.user_id === userId ? { ...u, daily_token_limit: limit } : u) ?? null);
      setUsers(prev => prev.map(u => u.id === userId ? { ...u, daily_token_limit: limit } : u));
      setEditingLimit(null);
      toast.success('Token limit updated');
    } catch {
      toast.error('Failed to update limit');
    } finally {
      setSavingLimit(null);
    }
  };

  const loadAuditLogs = useCallback(async () => {
    setAuditLoading(true);
    try {
      const logs = await api.getAdminAuditLogs({ limit: 50 });
      setAuditLogs(logs);
    } catch {
      toast.error('Failed to load audit logs');
    } finally {
      setAuditLoading(false);
    }
  }, []);

  const filteredLogs = auditFilter
    ? auditLogs.filter(l =>
        l.action.toLowerCase().includes(auditFilter.toLowerCase()) ||
        l.resource_type.toLowerCase().includes(auditFilter.toLowerCase())
      )
    : auditLogs;

  const adminCount = users.filter((u) => u.role === 'admin').length;
  const activeCount = users.filter((u) => u.is_active).length;

  return (
    <div className="space-y-3">
      {/* Stats strip */}
      <div className="grid grid-cols-3 gap-3 mb-1">
        {[
          { label: 'Total Users', value: users.length, color: 'text-blue-600' },
          { label: 'Active', value: activeCount, color: 'text-green-600' },
          { label: 'Admins', value: adminCount, color: 'text-amber-600' },
        ].map((s) => (
          <div key={s.label} className="rounded-lg border bg-card p-3 text-center">
            <p className={`text-xl font-bold ${s.color}`}>{s.value}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Users section */}
      <Section
        id="users"
        title="User Management"
        icon={<Users className="h-4 w-4" />}
        open={openSections.has('users')}
        onToggle={toggleSection}
      >
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs text-muted-foreground">
            Promote users to admin or deactivate accounts.
          </p>
          <Button variant="ghost" size="sm" onClick={loadUsers} disabled={usersLoading}>
            <RefreshCw className={`h-3.5 w-3.5 ${usersLoading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {usersLoading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
            {users.map((user) => (
              <div
                key={user.id}
                className={`flex items-center gap-3 p-3 rounded-lg border text-sm ${
                  !user.is_active ? 'opacity-50 bg-muted/30' : 'bg-background'
                }`}
              >
                {/* Avatar placeholder */}
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0 ${
                  user.role === 'admin'
                    ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                    : 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                }`}>
                  {user.name?.charAt(0)?.toUpperCase() || '?'}
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium truncate">{user.name}</span>
                    {user.role === 'admin' && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300 font-medium">
                        <Crown className="h-2.5 w-2.5" />
                        Admin
                      </span>
                    )}
                    {user.bootstrap_elevated_at && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300 font-medium">
                        Bootstrap
                      </span>
                    )}
                    {!user.is_active && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-100 text-red-600 font-medium">
                        Inactive
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 text-[11px] text-muted-foreground mt-0.5">
                    <Mail className="h-3 w-3 shrink-0" />
                    <span className="truncate">{user.email}</span>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 shrink-0">
                  {promoting === user.id ? (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  ) : (
                    <>
                      {user.role !== 'admin' ? (
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 px-2 text-[11px] border-amber-300 text-amber-700 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-400"
                          onClick={() => handleRoleChange(user.id, 'admin')}
                        >
                          <Crown className="h-3 w-3 mr-1" />
                          Make Admin
                        </Button>
                      ) : (
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 px-2 text-[11px]"
                          onClick={() => handleRoleChange(user.id, 'user')}
                        >
                          Demote
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        className={`h-7 px-2 text-[11px] ${user.is_active ? 'text-red-500 hover:text-red-600 hover:bg-red-50' : 'text-green-600 hover:bg-green-50'}`}
                        onClick={() => handleToggleActive(user.id, !user.is_active)}
                      >
                        <UserX className="h-3 w-3" />
                      </Button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Privacy / org settings */}
      <Section
        id="privacy"
        title="Organization Settings"
        icon={<Shield className="h-4 w-4" />}
        open={openSections.has('privacy')}
        onToggle={toggleSection}
      >
        {orgLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium">Private Chat (Incognito Mode)</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Allow users to start private sessions. Stored {orgSettings?.private_chat_retention_days ?? 20} days for compliance, then deleted.
                </p>
              </div>
              <Switch
                checked={orgSettings?.private_chat_enabled ?? true}
                onCheckedChange={handleTogglePrivateChat}
              />
            </div>
            <div className="flex items-start gap-2 pt-2 border-t text-xs text-muted-foreground">
              <Clock className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <p>
                Private chats are stored for <strong>{orgSettings?.private_chat_retention_days ?? 20} days</strong> for
                governance, then permanently deleted. Admins can access them during this window.
              </p>
            </div>
          </div>
        )}
      </Section>

      {/* Token Controls */}
      <Section
        id="tokens"
        title="Token Controls"
        icon={<Zap className="h-4 w-4" />}
        open={openSections.has('tokens')}
        onToggle={(id) => {
          toggleSection(id);
          if (!openSections.has('tokens') && !tokenUsage) loadTokenUsage();
        }}
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs text-muted-foreground">Per-user daily token limits and usage (last 7 days).</p>
            <Button variant="ghost" size="sm" onClick={loadTokenUsage} disabled={tokenLoading}>
              <RefreshCw className={`h-3.5 w-3.5 ${tokenLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
          {tokenLoading ? (
            <div className="flex justify-center py-4"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : !tokenUsage ? (
            <Button variant="outline" size="sm" onClick={loadTokenUsage}>Load token usage</Button>
          ) : (
            <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
              {tokenUsage.map((u) => {
                const pct = u.daily_token_limit > 0
                  ? Math.min(100, Math.round(u.tokens_used_today / u.daily_token_limit * 100))
                  : 0;
                return (
                  <div key={u.user_id} className="rounded-lg border bg-background p-2.5 text-xs">
                    <div className="flex items-center gap-2 justify-between mb-1.5">
                      <div className="min-w-0">
                        <span className="font-medium truncate block">{u.name}</span>
                        <span className="text-muted-foreground truncate block">{u.email}</span>
                      </div>
                      <div className="shrink-0 text-right">
                        {editingLimit === u.user_id ? (
                          <div className="flex items-center gap-1">
                            <input
                              type="number"
                              className="w-24 text-xs rounded border bg-background px-2 py-1"
                              value={limitValue}
                              onChange={e => setLimitValue(e.target.value)}
                              min={0}
                            />
                            <Button size="sm" className="h-6 px-2 text-[10px]"
                              onClick={() => handleSaveLimit(u.user_id)} disabled={savingLimit === u.user_id}>
                              {savingLimit === u.user_id ? '…' : 'Save'}
                            </Button>
                            <button className="text-muted-foreground hover:text-foreground px-1"
                              onClick={() => setEditingLimit(null)}>✕</button>
                          </div>
                        ) : (
                          <button
                            className="text-muted-foreground hover:text-foreground underline"
                            onClick={() => { setEditingLimit(u.user_id); setLimitValue(String(u.daily_token_limit)); }}
                          >
                            {u.daily_token_limit > 0 ? u.daily_token_limit.toLocaleString() : 'Unlimited'}
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${pct > 80 ? 'bg-red-500' : pct > 50 ? 'bg-amber-500' : 'bg-green-500'}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="text-muted-foreground shrink-0">
                        {u.tokens_used_today.toLocaleString()} / {u.period_tokens.toLocaleString()} (7d)
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </Section>

      {/* Audit Logs */}
      <Section
        id="auditlogs"
        title="Audit Logs"
        icon={<ScrollText className="h-4 w-4" />}
        open={openSections.has('auditlogs')}
        onToggle={(id) => {
          toggleSection(id);
          if (!openSections.has('auditlogs') && auditLogs.length === 0) loadAuditLogs();
        }}
      >
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <input
              className="flex-1 text-xs rounded-md border bg-background px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="Filter by action or resource…"
              value={auditFilter}
              onChange={e => setAuditFilter(e.target.value)}
            />
            <Button variant="ghost" size="sm" onClick={loadAuditLogs} disabled={auditLoading}>
              <RefreshCw className={`h-3.5 w-3.5 ${auditLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
          {auditLoading ? (
            <div className="flex justify-center py-4"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : filteredLogs.length === 0 ? (
            <p className="text-xs text-muted-foreground py-3 text-center">
              {auditLogs.length === 0 ? 'No audit logs yet.' : 'No matches.'}
            </p>
          ) : (
            <div className="rounded-md border overflow-hidden max-h-72 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="bg-muted/40 sticky top-0">
                  <tr>
                    <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Action</th>
                    <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Resource</th>
                    <th className="text-left px-2 py-1.5 font-medium text-muted-foreground">Status</th>
                    <th className="text-right px-2 py-1.5 font-medium text-muted-foreground">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {filteredLogs.map(log => (
                    <tr key={log.id} className="hover:bg-accent/20">
                      <td className="px-2 py-1.5 font-medium">{log.action}</td>
                      <td className="px-2 py-1.5 text-muted-foreground capitalize">{log.resource_type}</td>
                      <td className="px-2 py-1.5">
                        {log.success
                          ? <span className="text-green-500 flex items-center gap-1"><CheckCircle2 className="h-3 w-3" />OK</span>
                          : <span className="text-red-500 flex items-center gap-1"><AlertCircle className="h-3 w-3" />Fail</span>
                        }
                      </td>
                      <td className="px-2 py-1.5 text-right text-muted-foreground">
                        {new Date(log.created_at).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Section>

      {/* Bootstrap info */}
      <Section
        id="bootstrap"
        title="Bootstrap Admin Config"
        icon={<Crown className="h-4 w-4" />}
        open={openSections.has('bootstrap')}
        onToggle={toggleSection}
      >
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Accounts listed in <code className="bg-muted px-1 py-0.5 rounded text-[11px]">BOOTSTRAP_ADMIN_EMAILS</code> are
            automatically elevated to Admin on first sign-in. Elevation is permanent and one-time — it will not be reverted
            even if the account is removed from the list.
          </p>
          <div className="rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
            To add a new bootstrap admin, add their Microsoft email to <code>BOOTSTRAP_ADMIN_EMAILS</code> in the server
            environment and have them sign in. Or use the User Management panel above to promote any user directly.
          </div>
          {users.filter((u) => u.bootstrap_elevated_at).length > 0 && (
            <div className="space-y-1.5 pt-1">
              <p className="text-xs font-medium">Bootstrap-elevated accounts:</p>
              {users.filter((u) => u.bootstrap_elevated_at).map((u) => (
                <div key={u.id} className="flex items-center gap-2 text-xs">
                  <Crown className="h-3 w-3 text-amber-500 shrink-0" />
                  <span className="font-medium">{u.email}</span>
                  <span className="text-muted-foreground">
                    elevated {new Date(u.bootstrap_elevated_at!).toLocaleDateString()}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Section>
    </div>
  );
}
