/**
 * Mela AI – Admin Offboarding Panel
 * Structured admin workflow: target lookup → action config → preview →
 *   first confirm → (second confirm if delete) → execute → result
 * All Graph execution is server-side. Delete requires double confirmation.
 */

'use client';

import { useState, useCallback } from 'react';
import { api } from '@/lib/api';
import {
  UserMinus, CheckCircle2, XCircle, Clock, Loader2, RefreshCw,
  AlertTriangle, Shield, ChevronRight, Search, FileText, Play,
  Eye, Trash2,
} from 'lucide-react';

// ── Types ──────────────────────────────────────────────────────────────────────

interface OffboardingForm {
  target_email: string;
  reason: string;
  effective_date: string;
  disable_sign_in: boolean;
  revoke_sessions: boolean;
  remove_licenses: boolean;
  remove_groups: boolean;
  send_notifications: boolean;
  notification_recipients: string;
  delete_account: boolean;
  confirm_delete: boolean;
  confirm_delete_second: boolean;
  approval_reference: string;
}

const EMPTY_FORM: OffboardingForm = {
  target_email: '', reason: '', effective_date: '',
  disable_sign_in: true, revoke_sessions: true,
  remove_licenses: true, remove_groups: true,
  send_notifications: false, notification_recipients: '',
  delete_account: false, confirm_delete: false, confirm_delete_second: false,
  approval_reference: '',
};

interface PreviewResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  target_entra_id?: string;
  target_display_name?: string;
  target_upn?: string;
  account_enabled?: boolean;
  current_license_count?: number;
  current_group_count?: number;
  licenses?: { id: string; skuId: string; name: string }[];
  groups?: { id: string; displayName: string }[];
  actions_planned?: string[];
  delete_account?: boolean;
}

interface StepResult {
  step: string;
  status: 'ok' | 'failed' | 'skipped';
  detail?: string;
  error?: string;
  reason?: string;
}

interface RunResult {
  run_id: string;
  status: string;
  target_email: string;
  target_display_name?: string;
  target_upn?: string;
  steps: StepResult[];
  steps_completed: string[];
  steps_failed: string[];
  account_deleted: boolean;
}

interface RunRow {
  id: string;
  target_email: string;
  target_display_name?: string;
  actor_email: string;
  status: string;
  started_at: string;
  error_summary?: string;
}

type Step = 'form' | 'preview' | 'confirm1' | 'confirm2' | 'running' | 'result';

// ── Status badge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed:   'bg-green-100 text-green-700',
    partial:     'bg-yellow-100 text-yellow-700',
    failed:      'bg-red-100 text-red-700',
    running:     'bg-blue-100 text-blue-700',
    pending:     'bg-gray-100 text-gray-600',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${map[status] ?? 'bg-gray-100 text-gray-500'}`}>
      {status}
    </span>
  );
}

function StepRow({ step }: { step: StepResult }) {
  const icon =
    step.status === 'ok'      ? <CheckCircle2 size={14} className="text-green-500" /> :
    step.status === 'failed'  ? <XCircle size={14} className="text-red-500" /> :
                                <Clock size={14} className="text-gray-400" />;
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-gray-50 last:border-0">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-gray-800">{step.step.replace(/_/g, ' ')}</p>
        {(step.detail || step.error || step.reason) && (
          <p className="text-xs text-gray-500">{step.detail || step.error || step.reason}</p>
        )}
      </div>
    </div>
  );
}

function Toggle({ label, checked, onChange, danger = false, className = '' }: {
  label: string; checked: boolean; onChange: (v: boolean) => void;
  danger?: boolean; className?: string;
}) {
  return (
    <label className={`flex items-center gap-3 cursor-pointer ${className}`}>
      <div
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${
          checked ? (danger ? 'bg-red-500' : 'bg-blue-600') : 'bg-gray-200'}`}
      >
        <span className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transform transition-transform ${checked ? 'translate-x-4' : 'translate-x-0.5'}`} />
      </div>
      <span className={`text-sm ${danger ? 'text-red-700 font-medium' : 'text-gray-700'}`}>{label}</span>
    </label>
  );
}

// ── Main panel ─────────────────────────────────────────────────────────────────

export default function OffboardingPanel() {
  const [step, setStep] = useState<Step>('form');
  const [form, setForm] = useState<OffboardingForm>(EMPTY_FORM);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  const [runs, setRuns] = useState<RunRow[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [selectedRun, setSelectedRun] = useState<any>(null);

  const set = (patch: Partial<OffboardingForm>) => setForm(f => ({ ...f, ...patch }));

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const r = await api.fetch<{ runs: RunRow[] }>('/api/v1/admin/offboarding/runs?limit=20');
      setRuns(r.runs || []);
    } catch { setRuns([]); } finally { setRunsLoading(false); }
  }, []);

  // Load history on mount
  useState(() => { loadRuns(); });

  const buildPayload = () => ({
    ...form,
    notification_recipients: form.notification_recipients
      ? form.notification_recipients.split(',').map(s => s.trim()).filter(Boolean)
      : [],
    effective_date: form.effective_date || undefined,
  });

  const handlePreview = async () => {
    setErrors([]);
    if (!form.target_email.trim()) {
      setErrors(['Target user email is required.']); return;
    }
    setLoading(true);
    try {
      const r = await api.fetch<PreviewResult>('/api/v1/admin/offboarding/preview', {
        method: 'POST',
        body: JSON.stringify(buildPayload()),
        headers: { 'Content-Type': 'application/json' },
      });
      setPreview(r);
      if (r.valid) setStep('preview');
      else setErrors(r.errors);
    } catch (e: any) {
      setErrors([e.message || 'Preview failed']);
    } finally { setLoading(false); }
  };

  const handleConfirm1 = () => {
    if (form.delete_account) setStep('confirm2');
    else handleExecute();
  };

  const handleExecute = async () => {
    setStep('running');
    setLoading(true);
    try {
      const r = await api.fetch<RunResult>('/api/v1/admin/offboarding/execute', {
        method: 'POST',
        body: JSON.stringify(buildPayload()),
        headers: { 'Content-Type': 'application/json' },
      });
      setResult(r);
      setStep('result');
      loadRuns();
    } catch (e: any) {
      setErrors([e.message || 'Execution failed']);
      setStep(form.delete_account ? 'confirm2' : 'confirm1');
    } finally { setLoading(false); }
  };

  const handleReset = () => {
    setForm(EMPTY_FORM); setStep('form'); setPreview(null);
    setResult(null); setErrors([]);
  };

  const loadRunDetail = async (id: string) => {
    try {
      const r = await api.fetch<any>(`/api/v1/admin/offboarding/runs/${id}`);
      setSelectedRun(r);
    } catch { /* ignore */ }
  };

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <UserMinus size={20} className="text-red-500" />
            Employee Offboarding
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Disable access, remove licenses and groups, optionally delete account
          </p>
        </div>
        {step !== 'form' && (
          <button onClick={handleReset} className="text-sm text-gray-500 hover:text-gray-800 flex items-center gap-1">
            <RefreshCw size={14} /> New offboarding
          </button>
        )}
      </div>

      {/* Progress */}
      <div className="flex items-center gap-1 text-xs text-gray-400">
        {(['form','preview','confirm1','confirm2','running','result'] as Step[]).map((s, i) => (
          <span key={s} className="flex items-center gap-1">
            {i > 0 && <ChevronRight size={10} />}
            <span className={step === s ? 'text-red-600 font-semibold' : ''}>
              {s === 'confirm1' ? 'Confirm' : s === 'confirm2' ? 'Delete confirm' : s.charAt(0).toUpperCase() + s.slice(1)}
            </span>
          </span>
        ))}
      </div>

      {/* Errors */}
      {errors.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-1">
          {errors.map((e, i) => (
            <p key={i} className="text-sm text-red-700 flex items-start gap-1.5">
              <XCircle size={13} className="mt-0.5 shrink-0" /> {e}
            </p>
          ))}
        </div>
      )}

      {/* ── FORM ── */}
      {step === 'form' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Target user */}
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <div className="flex items-center gap-2 mb-3 pb-1 border-b border-gray-100">
              <Search size={14} className="text-red-400" />
              <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Target User</span>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Email or UPN *</label>
              <input
                value={form.target_email}
                onChange={e => set({ target_email: e.target.value })}
                placeholder="departing.employee@armely.com"
                className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-200"
              />
              <p className="text-xs text-gray-400 mt-1">Must match exactly one user in Entra ID.</p>
            </div>
            <div className="mt-4">
              <label className="block text-xs font-medium text-gray-600 mb-1">Reason / Notes</label>
              <textarea
                value={form.reason}
                onChange={e => set({ reason: e.target.value })}
                placeholder="Resignation, termination, role change…"
                rows={3}
                className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-200"
              />
            </div>
            <div className="mt-3">
              <label className="block text-xs font-medium text-gray-600 mb-1">Effective date</label>
              <input type="date" value={form.effective_date}
                onChange={e => set({ effective_date: e.target.value })}
                className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-200" />
            </div>
          </div>

          {/* Actions */}
          <div className="space-y-4">
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <div className="flex items-center gap-2 mb-3 pb-1 border-b border-gray-100">
                <Shield size={14} className="text-orange-400" />
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Access Actions</span>
              </div>
              <div className="space-y-3">
                <Toggle label="Disable sign-in (accountEnabled → false)"
                  checked={form.disable_sign_in} onChange={v => set({ disable_sign_in: v })} />
                <Toggle label="Revoke active sessions"
                  checked={form.revoke_sessions} onChange={v => set({ revoke_sessions: v })} />
                <Toggle label="Remove all licenses"
                  checked={form.remove_licenses} onChange={v => set({ remove_licenses: v })} />
                <Toggle label="Remove from all groups"
                  checked={form.remove_groups} onChange={v => set({ remove_groups: v })} />
              </div>
            </div>

            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <div className="flex items-center gap-2 mb-3 pb-1 border-b border-gray-100">
                <FileText size={14} className="text-blue-400" />
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Notifications</span>
              </div>
              <Toggle label="Send offboarding notification"
                checked={form.send_notifications} onChange={v => set({ send_notifications: v })} />
              {form.send_notifications && (
                <div className="mt-3">
                  <label className="block text-xs font-medium text-gray-600 mb-1">Recipients (comma-sep)</label>
                  <input
                    value={form.notification_recipients}
                    onChange={e => set({ notification_recipients: e.target.value })}
                    placeholder="hr@armely.com, it@armely.com"
                    className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-200"
                  />
                </div>
              )}
            </div>

            <div className="bg-white border border-red-100 rounded-xl p-5">
              <div className="flex items-center gap-2 mb-3 pb-1 border-b border-red-100">
                <Trash2 size={14} className="text-red-400" />
                <span className="text-xs font-semibold text-red-400 uppercase tracking-wide">Destructive Actions</span>
              </div>
              <Toggle label="Delete Entra account (IRREVERSIBLE — requires double confirmation)"
                checked={form.delete_account} onChange={v => set({ delete_account: v, confirm_delete: false, confirm_delete_second: false })}
                danger />
              {form.delete_account && (
                <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3">
                  <p className="text-xs text-red-700">
                    ⚠️ Enabling this will permanently delete the user account from Entra ID.
                    This action <strong>cannot be undone</strong>. Two separate confirmations will be required before execution.
                  </p>
                </div>
              )}
            </div>
          </div>

          <div className="lg:col-span-2 flex items-center gap-4">
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-600 mb-1">Approval reference</label>
              <input value={form.approval_reference}
                onChange={e => set({ approval_reference: e.target.value })}
                placeholder="Ticket / HR approval ID"
                className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-200" />
            </div>
            <div className="pt-5">
              <button onClick={handlePreview} disabled={loading}
                className="flex items-center gap-2 px-5 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 font-medium text-sm">
                {loading ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
                Preview offboarding
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── PREVIEW ── */}
      {step === 'preview' && preview && (
        <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-4">
          <h3 className="font-semibold text-gray-900">Offboarding Dry-run Preview</h3>

          {preview.warnings?.length > 0 && (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 space-y-1">
              {preview.warnings.map((w, i) => (
                <p key={i} className="text-sm text-yellow-800 flex items-start gap-1.5">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {w}
                </p>
              ))}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <InfoRow label="Name" value={preview.target_display_name} />
            <InfoRow label="UPN" value={preview.target_upn} />
            <InfoRow label="Entra ID" value={preview.target_entra_id} />
            <InfoRow label="Sign-in" value={preview.account_enabled ? 'Enabled' : 'Already disabled'} />
            <InfoRow label="Licenses" value={`${preview.current_license_count ?? 0} license(s)`} />
            <InfoRow label="Groups" value={`${preview.current_group_count ?? 0} group(s)`} />
          </div>

          {(preview.licenses?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-500 mb-1 uppercase">Licenses to remove</p>
              <div className="flex flex-wrap gap-1">
                {preview.licenses!.map(l => (
                  <span key={l.skuId} className="px-2 py-0.5 bg-orange-50 text-orange-700 rounded text-xs">{l.name}</span>
                ))}
              </div>
            </div>
          )}

          {(preview.groups?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-500 mb-1 uppercase">Groups to remove from</p>
              <div className="flex flex-wrap gap-1">
                {preview.groups!.map(g => (
                  <span key={g.id} className="px-2 py-0.5 bg-gray-100 text-gray-700 rounded text-xs">{g.displayName}</span>
                ))}
              </div>
            </div>
          )}

          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Actions to execute</p>
            {(preview.actions_planned || []).map((a, i) => (
              <div key={i} className={`flex items-center gap-2 text-sm mb-1 ${a.includes('DELETE') ? 'text-red-700 font-semibold' : 'text-gray-700'}`}>
                {a.includes('DELETE') ? <Trash2 size={13} className="text-red-500" /> : <CheckCircle2 size={13} className="text-blue-400" />}
                {a}
              </div>
            ))}
          </div>

          <div className="flex gap-3 pt-2">
            <button onClick={() => setStep('confirm1')}
              className="flex items-center gap-2 px-5 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium text-sm">
              <Play size={14} /> Proceed to confirmation
            </button>
            <button onClick={() => setStep('form')}
              className="px-4 py-2.5 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
              Edit
            </button>
          </div>
        </div>
      )}

      {/* ── CONFIRM 1 ── */}
      {step === 'confirm1' && (
        <div className="bg-white border border-red-200 rounded-xl p-6 max-w-lg">
          <div className="flex items-start gap-3">
            <Shield size={20} className="text-red-500 mt-0.5" />
            <div>
              <h3 className="font-semibold text-gray-900">Confirm Offboarding</h3>
              <p className="text-sm text-gray-600 mt-1">
                You are about to offboard <strong>{preview?.target_display_name || form.target_email}</strong>.
              </p>
              <p className="text-sm text-gray-500 mt-1">
                Actions: {(preview?.actions_planned || []).filter(a => !a.includes('DELETE')).join(' · ')}
              </p>
              {form.delete_account && (
                <p className="text-sm text-red-700 font-medium mt-2">
                  ⚠️ Account deletion is also selected — a second confirmation will follow.
                </p>
              )}
              <div className="flex gap-3 mt-5">
                <button onClick={handleConfirm1} disabled={loading}
                  className="flex items-center gap-2 px-5 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium text-sm disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  {form.delete_account ? 'Next: confirm delete' : 'Execute offboarding'}
                </button>
                <button onClick={() => setStep('preview')}
                  className="px-4 py-2.5 text-sm text-gray-600 border border-gray-200 rounded-lg">
                  Back
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── CONFIRM 2 (delete) ── */}
      {step === 'confirm2' && (
        <div className="bg-red-50 border-2 border-red-400 rounded-xl p-6 max-w-lg">
          <div className="flex items-start gap-3">
            <Trash2 size={22} className="text-red-600 mt-0.5" />
            <div className="flex-1">
              <h3 className="font-bold text-red-800 text-lg">⚠️ FINAL CONFIRMATION — DELETE ACCOUNT</h3>
              <p className="text-sm text-red-700 mt-2">
                You are about to <strong>permanently delete</strong> the Entra ID account for:
              </p>
              <div className="bg-white border border-red-200 rounded-lg p-3 my-3">
                <p className="font-semibold text-gray-900">{preview?.target_display_name}</p>
                <p className="text-sm text-gray-600">{preview?.target_upn}</p>
                <p className="text-xs text-gray-400">Entra ID: {preview?.target_entra_id}</p>
              </div>
              <p className="text-sm text-red-700 font-medium">
                This action is IRREVERSIBLE. The account will be permanently removed from Entra ID.
              </p>
              <div className="mt-4 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={form.confirm_delete}
                    onChange={e => set({ confirm_delete: e.target.checked })}
                    className="accent-red-600 w-4 h-4" />
                  <span className="text-sm text-red-800">I confirm I want to delete this account</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={form.confirm_delete_second}
                    onChange={e => set({ confirm_delete_second: e.target.checked })}
                    className="accent-red-600 w-4 h-4" />
                  <span className="text-sm text-red-800 font-medium">
                    I understand this is permanent and cannot be undone
                  </span>
                </label>
              </div>
              <div className="flex gap-3 mt-5">
                <button
                  onClick={handleExecute}
                  disabled={!form.confirm_delete || !form.confirm_delete_second || loading}
                  className="flex items-center gap-2 px-5 py-2.5 bg-red-700 text-white rounded-lg hover:bg-red-800 font-bold text-sm disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                  Execute & delete account
                </button>
                <button onClick={() => setStep('confirm1')}
                  className="px-4 py-2.5 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">
                  Back
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── RUNNING ── */}
      {step === 'running' && (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <Loader2 size={32} className="animate-spin text-red-500" />
          <p className="text-sm text-gray-500">Running offboarding workflow…</p>
          <p className="text-xs text-gray-400">Disabling sign-in, revoking sessions, removing access…</p>
        </div>
      )}

      {/* ── RESULT ── */}
      {step === 'result' && result && (
        <div className={`border rounded-xl p-5 ${
          result.status === 'completed' ? 'bg-green-50 border-green-200' :
          result.status === 'partial'   ? 'bg-yellow-50 border-yellow-200' :
                                          'bg-red-50 border-red-200'
        }`}>
          <div className="flex items-center gap-3 mb-4">
            {result.status === 'completed' ? <CheckCircle2 size={20} className="text-green-600" /> :
             result.status === 'partial'   ? <AlertTriangle size={20} className="text-yellow-600" /> :
                                             <XCircle size={20} className="text-red-600" />}
            <div>
              <h3 className="font-semibold text-gray-900">
                Offboarding {result.status === 'completed' ? 'completed' :
                             result.status === 'partial'   ? 'partially completed' : 'failed'}
              </h3>
              <p className="text-sm text-gray-500">Run ID: {result.run_id}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 mb-4">
            <InfoRow label="User" value={result.target_display_name || result.target_email} />
            <InfoRow label="UPN" value={result.target_upn} />
            {result.account_deleted && (
              <div className="col-span-2">
                <span className="text-sm font-bold text-red-700">✓ Account permanently deleted from Entra ID.</span>
              </div>
            )}
          </div>

          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Step Results</p>
          {result.steps.map((s, i) => <StepRow key={i} step={s} />)}

          <button onClick={handleReset} className="mt-4 text-sm text-blue-600 hover:underline">
            Start new offboarding
          </button>
        </div>
      )}

      {/* ── RUN HISTORY ── */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
          <p className="text-sm font-semibold text-gray-800">Recent Offboarding Runs</p>
          <button onClick={loadRuns} className="text-gray-400 hover:text-gray-700">
            {runsLoading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          </button>
        </div>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-400 px-5 py-4">No offboarding runs yet.</p>
        ) : (
          <div className="divide-y divide-gray-50">
            {runs.map(r => (
              <div key={r.id}
                className="flex items-center gap-4 px-5 py-3 hover:bg-gray-50 cursor-pointer"
                onClick={() => loadRunDetail(r.id)}>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">
                    {r.target_display_name || r.target_email}
                  </p>
                  <p className="text-xs text-gray-400">by {r.actor_email} · {new Date(r.started_at).toLocaleString()}</p>
                  {r.error_summary && <p className="text-xs text-red-500">{r.error_summary}</p>}
                </div>
                <StatusBadge status={r.status} />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Run detail drawer */}
      {selectedRun && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-4"
          onClick={() => setSelectedRun(null)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[80vh] overflow-y-auto p-6"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Offboarding Run Detail</h3>
              <button onClick={() => setSelectedRun(null)} className="text-gray-400 hover:text-gray-700">✕</button>
            </div>
            <div className="grid grid-cols-2 gap-3 mb-4">
              <InfoRow label="Target" value={selectedRun.target_display_name || selectedRun.target_email} />
              <InfoRow label="Status" value={<StatusBadge status={selectedRun.status} />} />
            </div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Steps</p>
            {(selectedRun.step_results || []).map((s: StepResult, i: number) =>
              <StepRow key={i} step={s} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-sm font-medium text-gray-800 mt-0.5">{value ?? '—'}</p>
    </div>
  );
}
