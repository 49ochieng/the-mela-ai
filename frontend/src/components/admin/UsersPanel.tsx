'use client';

import { useEffect, useState, useCallback } from 'react';
import { Search, Edit, Shield, User, Ban, ChevronRight, ShieldCheck, X, Settings2, DollarSign, Cpu } from 'lucide-react';
import { api, User as UserType, UserDetail, AdminAccessRequest, ModelRanking, ModelAccessRule, UserBudgetStatus } from '@/lib/api';

// ── Governance Modal (Budget + Model Access) ───────────────────────────────

function GovernanceModal({ user, onClose }: { user: UserType; onClose: () => void }) {
  const [tab, setTab] = useState<'budget' | 'models'>('budget');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Budget state
  const [budgetStatus, setBudgetStatus] = useState<UserBudgetStatus | null>(null);
  const [tokenBudget, setTokenBudget] = useState<string>('');
  const [costBudget, setCostBudget] = useState<string>('');
  const [period, setPeriod] = useState<'monthly' | 'daily'>('monthly');
  const [hardStop, setHardStop] = useState(false);
  const [tokenWarningPct, setTokenWarningPct] = useState(80);
  const [costWarningPct, setCostWarningPct] = useState(80);

  // Model access state
  const [allModels, setAllModels] = useState<ModelRanking[]>([]);
  const [accessRules, setAccessRules] = useState<ModelAccessRule[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  // Load budget on mount
  useEffect(() => {
    api.getUserDetail(user.id).then((detail) => {
      // Check if there's budget info in detail or load separately
    }).catch(() => {});

    // Load effective budget (try via admin endpoint)
    api.getMyBudget().catch(() => {});
  }, [user.id]);

  // Load models and access rules
  useEffect(() => {
    if (tab !== 'models') return;
    setModelsLoading(true);
    Promise.all([
      api.getModelRankings(),
      api.getModelAccessRules(),
    ]).then(([models, rules]) => {
      setAllModels(models);
      setAccessRules(rules.filter((r) => r.user_id === user.id));
    }).catch(() => {}).finally(() => setModelsLoading(false));
  }, [tab, user.id]);

  const saveBudget = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.setUserBudget({
        user_id: user.id,
        token_budget: tokenBudget ? parseInt(tokenBudget) : null,
        cost_budget: costBudget ? parseFloat(costBudget) : null,
        period,
        hard_stop: hardStop,
        token_warning_pct: tokenWarningPct,
        cost_warning_pct: costWarningPct,
      });
      setSuccess('Budget saved successfully');
      setTimeout(() => setSuccess(null), 3000);
    } catch (e: any) {
      setError(e.message || 'Failed to save budget');
    } finally {
      setSaving(false);
    }
  };

  const toggleModelAccess = async (modelId: string, currentlyAllowed: boolean | null) => {
    setSaving(true);
    setError(null);
    try {
      if (currentlyAllowed !== null) {
        // Find and delete existing rule
        const existing = accessRules.find((r) => r.model_id === modelId);
        if (existing) {
          await api.deleteModelAccessRule(existing.id);
          setAccessRules((prev) => prev.filter((r) => r.id !== existing.id));
        }
      } else {
        // No rule exists — create a deny rule (block the model)
        const rule = await api.setModelAccessRule({
          model_id: modelId,
          is_allowed: false,
          user_id: user.id,
        });
        setAccessRules((prev) => [...prev, rule]);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to update model access');
    } finally {
      setSaving(false);
    }
  };

  const setModelAllowed = async (modelId: string, isAllowed: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const rule = await api.setModelAccessRule({ model_id: modelId, is_allowed: isAllowed, user_id: user.id });
      setAccessRules((prev) => {
        const filtered = prev.filter((r) => r.model_id !== modelId);
        return [...filtered, rule];
      });
    } catch (e: any) {
      setError(e.message || 'Failed to update model access');
    } finally {
      setSaving(false);
    }
  };

  const removeModelRule = async (modelId: string) => {
    const existing = accessRules.find((r) => r.model_id === modelId);
    if (!existing) return;
    setSaving(true);
    try {
      await api.deleteModelAccessRule(existing.id);
      setAccessRules((prev) => prev.filter((r) => r.id !== existing.id));
    } catch (e: any) {
      setError(e.message || 'Failed to remove rule');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <div>
            <h3 className="font-semibold text-gray-900">User Governance</h3>
            <p className="text-xs text-gray-500 mt-0.5">{user.name} — {user.email}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b px-5">
          {([['budget', 'Budget & Limits', DollarSign], ['models', 'Model Access', Cpu]] as const).map(([id, label, Icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                tab === id
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5">
          {error && (
            <div className="mb-3 px-3 py-2 text-sm text-red-700 bg-red-50 rounded-lg border border-red-200">
              {error}
            </div>
          )}
          {success && (
            <div className="mb-3 px-3 py-2 text-sm text-green-700 bg-green-50 rounded-lg border border-green-200">
              {success}
            </div>
          )}

          {tab === 'budget' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Token Budget</label>
                  <input
                    type="number"
                    value={tokenBudget}
                    onChange={(e) => setTokenBudget(e.target.value)}
                    placeholder="Unlimited"
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <p className="text-[11px] text-gray-400 mt-1">Total tokens per period</p>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Cost Budget ($)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={costBudget}
                    onChange={(e) => setCostBudget(e.target.value)}
                    placeholder="Unlimited"
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <p className="text-[11px] text-gray-400 mt-1">Max USD per period</p>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Budget Period</label>
                <select
                  value={period}
                  onChange={(e) => setPeriod(e.target.value as 'monthly' | 'daily')}
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="monthly">Monthly</option>
                  <option value="daily">Daily</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Token Warning at (%)</label>
                  <input
                    type="number"
                    min="1" max="99"
                    value={tokenWarningPct}
                    onChange={(e) => setTokenWarningPct(Number(e.target.value))}
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Cost Warning at (%)</label>
                  <input
                    type="number"
                    min="1" max="99"
                    value={costWarningPct}
                    onChange={(e) => setCostWarningPct(Number(e.target.value))}
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>

              <label className="flex items-start gap-3 p-3 rounded-lg border border-gray-200 cursor-pointer hover:bg-gray-50">
                <input
                  type="checkbox"
                  checked={hardStop}
                  onChange={(e) => setHardStop(e.target.checked)}
                  className="mt-0.5 rounded"
                />
                <div>
                  <p className="text-sm font-medium text-gray-800">Hard Stop</p>
                  <p className="text-xs text-gray-500">Block all requests when budget is exceeded. Without this, usage is tracked but requests still succeed.</p>
                </div>
              </label>

              <button
                onClick={saveBudget}
                disabled={saving}
                className="w-full py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {saving ? 'Saving…' : 'Save Budget Policy'}
              </button>
            </div>
          )}

          {tab === 'models' && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500">
                Set per-user model access. Models with no rule inherit global defaults (open access if no global rules exist).
              </p>

              {modelsLoading ? (
                <p className="text-sm text-gray-400">Loading models…</p>
              ) : (
                <div className="space-y-1.5">
                  {allModels.map((model) => {
                    const rule = accessRules.find((r) => r.model_id === model.model_id);
                    const isAllowed = rule ? rule.is_allowed : null;

                    return (
                      <div key={model.model_id} className="flex items-center justify-between p-3 rounded-lg border border-gray-100 hover:bg-gray-50">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-800 truncate">{model.display_name}</p>
                          <p className="text-[11px] text-gray-400">{model.model_id}</p>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          {isAllowed === null ? (
                            <span className="text-[11px] text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">Inherited</span>
                          ) : isAllowed ? (
                            <span className="text-[11px] text-green-700 bg-green-50 px-2 py-0.5 rounded-full border border-green-200">Allowed</span>
                          ) : (
                            <span className="text-[11px] text-red-600 bg-red-50 px-2 py-0.5 rounded-full border border-red-200">Blocked</span>
                          )}
                          <div className="flex gap-1">
                            <button
                              onClick={() => setModelAllowed(model.model_id, true)}
                              disabled={saving || isAllowed === true}
                              className="px-2 py-1 text-[11px] font-medium rounded text-green-700 hover:bg-green-50 disabled:opacity-40"
                              title="Allow"
                            >
                              Allow
                            </button>
                            <button
                              onClick={() => setModelAllowed(model.model_id, false)}
                              disabled={saving || isAllowed === false}
                              className="px-2 py-1 text-[11px] font-medium rounded text-red-600 hover:bg-red-50 disabled:opacity-40"
                              title="Block"
                            >
                              Block
                            </button>
                            {isAllowed !== null && (
                              <button
                                onClick={() => removeModelRule(model.model_id)}
                                disabled={saving}
                                className="px-2 py-1 text-[11px] font-medium rounded text-gray-400 hover:bg-gray-100"
                                title="Remove rule (inherit)"
                              >
                                Reset
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function UserDetailDrawer({ userId, onClose }: { userId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getUserDetail(userId)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [userId]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative bg-white w-full max-w-md shadow-xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h3 className="font-semibold text-gray-900">User Detail</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {loading && <p className="text-gray-400">Loading...</p>}
          {detail && (
            <>
              <div>
                <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Profile</p>
                <p className="font-medium text-gray-900">{detail.user.name}</p>
                <p className="text-sm text-gray-500">{detail.user.email}</p>
                <div className="mt-2 flex gap-2 flex-wrap">
                  <span className="px-2 py-0.5 text-xs rounded-full bg-blue-50 text-blue-700">{detail.user.role}</span>
                  <span className={`px-2 py-0.5 text-xs rounded-full ${detail.user.is_active ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                    {detail.user.is_active ? 'Active' : 'Blocked'}
                  </span>
                </div>
              </div>
              <div>
                <p className="text-xs text-gray-400 uppercase tracking-wide mb-2">Usage (30d)</p>
                <div className="grid grid-cols-2 gap-3">
                  {([
                    ['Tokens', detail.usage.total_tokens.toLocaleString()],
                    ['Requests', detail.usage.total_requests],
                    ['Conversations', detail.usage.conversations],
                    ['Est. Cost', `$${detail.usage.estimated_cost_usd}`],
                  ] as [string, string | number][]).map(([k, v]) => (
                    <div key={k} className="bg-gray-50 rounded-lg p-3">
                      <p className="text-xs text-gray-400">{k}</p>
                      <p className="font-semibold text-gray-800">{v}</p>
                    </div>
                  ))}
                </div>
              </div>
              {detail.recent_conversations.length > 0 && (
                <div>
                  <p className="text-xs text-gray-400 uppercase tracking-wide mb-2">Recent Conversations</p>
                  <ul className="space-y-1">
                    {detail.recent_conversations.map((c) => (
                      <li key={c.id} className="text-sm text-gray-600 truncate">{c.title || 'Untitled'}</li>
                    ))}
                  </ul>
                </div>
              )}
              {detail.recent_audit.length > 0 && (
                <div>
                  <p className="text-xs text-gray-400 uppercase tracking-wide mb-2">Recent Activity</p>
                  <ul className="space-y-1">
                    {detail.recent_audit.map((a, i) => (
                      <li key={i} className="text-xs text-gray-500 flex gap-2 items-center">
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${a.success ? 'bg-green-400' : 'bg-red-400'}`} />
                        {a.action}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function EditUserModal({ user, onClose, onSaved }: { user: UserType; onClose: () => void; onSaved: () => void }) {
  const [role, setRole] = useState<'user' | 'admin' | 'viewer'>(
    (user.role as 'user' | 'admin' | 'viewer') ?? 'user'
  );
  const [isActive, setIsActive] = useState(user.is_active);
  const [tokenLimit, setTokenLimit] = useState<number | ''>(user.daily_token_limit ?? '');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const wasUser = user.role !== 'admin';
      await api.updateAdminUser(user.id, {
        role,
        is_active: isActive,
        daily_token_limit: tokenLimit === '' ? undefined : Number(tokenLimit),
      });
      if (role === 'admin' && wasUser) {
        // Notify the promoting admin that the target will see a banner
        alert(`${user.name || user.email} has been promoted to Admin.\nThey will see a notification on their next login.`);
      }
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative bg-white rounded-xl shadow-xl w-full max-w-sm p-6 space-y-4">
        <h3 className="font-semibold text-gray-900">Edit User</h3>
        <p className="text-sm text-gray-500">{user.name} — {user.email}</p>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value as 'user' | 'admin' | 'viewer')} className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm">
            <option value="user">User</option>
            <option value="admin">Admin</option>
            <option value="viewer">Viewer</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Daily Token Limit</label>
          <input
            type="number"
            value={tokenLimit}
            onChange={(e) => setTokenLimit(e.target.value === '' ? '' : Number(e.target.value))}
            placeholder="Unlimited"
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} className="rounded" />
          Account Active
        </label>
        <div className="flex gap-2 justify-end pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg">Cancel</button>
          <button onClick={save} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function UsersPanel({
  accessRequests = [],
  onRequestsChange,
}: {
  accessRequests?: AdminAccessRequest[];
  onRequestsChange?: () => void;
}) {
  const [users, setUsers] = useState<UserType[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [editUser, setEditUser] = useState<UserType | null>(null);
  const [detailUserId, setDetailUserId] = useState<string | null>(null);
  const [governanceUser, setGovernanceUser] = useState<UserType | null>(null);
  const [promotingId, setPromotingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await api.listAdminUsers();
    setUsers(res);
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const approveRequest = useCallback(async (req: AdminAccessRequest) => {
    setPromotingId(req.user_id);
    try {
      await api.updateAdminUser(req.user_id, { role: 'admin' });
      await load();
      onRequestsChange?.();
    } finally {
      setPromotingId(null);
    }
  }, [load, onRequestsChange]);

  const filtered = users.filter(
    (u) =>
      u.name?.toLowerCase().includes(search.toLowerCase()) ||
      u.email?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900">Users</h2>
        <p className="text-sm text-gray-400">{users.length} total</p>
      </div>

      {/* Pending Admin Access Requests */}
      {accessRequests.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2">
            <ShieldCheck size={16} className="text-amber-600" />
            <p className="text-sm font-semibold text-amber-800">
              {accessRequests.length} pending admin access request{accessRequests.length > 1 ? 's' : ''}
            </p>
          </div>
          <div className="space-y-2">
            {accessRequests.map((req) => (
              <div key={req.user_id} className="flex items-center justify-between bg-white rounded-lg px-3 py-2 border border-amber-100">
                <div>
                  <p className="text-sm font-medium text-gray-800">{req.name || req.email}</p>
                  <p className="text-xs text-gray-400">{req.email}</p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => approveRequest(req)}
                    disabled={promotingId === req.user_id}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                  >
                    <ShieldCheck size={12} />
                    {promotingId === req.user_id ? 'Promoting…' : 'Promote to Admin'}
                  </button>
                  <button
                    onClick={onRequestsChange}
                    className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600"
                    title="Dismiss"
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or email…"
          className="w-full border border-gray-200 rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm">Loading users…</p>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">User</th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Role</th>
                <th className="text-left px-5 py-2.5 text-gray-500 font-medium">Status</th>
                <th className="text-right px-5 py-2.5 text-gray-500 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => (
                <tr key={u.id} className="border-t border-gray-50 hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <p className="font-medium text-gray-800">{u.name || '—'}</p>
                    <p className="text-xs text-gray-400">{u.email}</p>
                  </td>
                  <td className="px-5 py-3">
                    <span className="flex items-center gap-1 text-gray-600">
                      {u.role === 'admin' ? <Shield size={14} className="text-blue-500" /> : <User size={14} />}
                      {u.role}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${u.is_active ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                      {u.is_active ? 'Active' : 'Blocked'}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right flex justify-end gap-2">
                    <button onClick={() => setDetailUserId(u.id)} className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600" title="View detail">
                      <ChevronRight size={15} />
                    </button>
                    <button onClick={() => setEditUser(u)} className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600" title="Edit user">
                      <Edit size={15} />
                    </button>
                    <button onClick={() => setGovernanceUser(u)} className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-blue-600" title="Budget & model access">
                      <Settings2 size={15} />
                    </button>
                    <button
                      onClick={async () => {
                        await api.updateAdminUser(u.id, { is_active: !u.is_active });
                        load();
                      }}
                      className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-red-500"
                      title={u.is_active ? 'Block user' : 'Unblock user'}
                    >
                      <Ban size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editUser && <EditUserModal user={editUser} onClose={() => setEditUser(null)} onSaved={load} />}
      {detailUserId && <UserDetailDrawer userId={detailUserId} onClose={() => setDetailUserId(null)} />}
      {governanceUser && <GovernanceModal user={governanceUser} onClose={() => setGovernanceUser(null)} />}
    </div>
  );
}
