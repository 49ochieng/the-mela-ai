/**
 * Mela AI – Admin Onboarding Panel
 * Structured admin workflow: form → preview → confirm → execute → result
 * Only admins can access. All Graph execution happens server-side.
 */

'use client';

import { useState, useEffect, useCallback } from 'react';
import { api } from '@/lib/api';
import {
  UserPlus, CheckCircle2, XCircle, Clock, Loader2, RefreshCw,
  ChevronRight, AlertTriangle, Info, Eye, Play, FileText,
  Building2, Mail, Calendar, Shield, Tag, Users,
} from 'lucide-react';

// ── Types ──────────────────────────────────────────────────────────────────────

interface OnboardingForm {
  first_name: string;
  last_name: string;
  display_name: string;
  upn: string;
  mail_nickname: string;
  work_email: string;
  department: string;
  job_title: string;
  manager_email: string;
  usage_location: string;
  group_ids: string[];
  sku_ids: string[];
  schedule_orientation: boolean;
  orientation_datetime: string;
  send_welcome_email: boolean;
  welcome_recipients: string;
  create_tasks: boolean;
  notes: string;
  approval_reference: string;
}

const EMPTY_FORM: OnboardingForm = {
  first_name: '', last_name: '', display_name: '', upn: '', mail_nickname: '',
  work_email: '', department: '', job_title: '', manager_email: '',
  usage_location: 'US', group_ids: [], sku_ids: [],
  schedule_orientation: true, orientation_datetime: '',
  send_welcome_email: true, welcome_recipients: '',
  create_tasks: true, notes: '', approval_reference: '',
};

interface PreviewResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  steps_planned?: string[];
  target_upn?: string;
  display_name?: string;
  department?: string;
  job_title?: string;
  usage_location?: string;
  group_count?: number;
  license_count?: number;
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
  target_upn?: string;
  target_display_name?: string;
  entra_id?: string;
  temp_password?: string;
  steps: StepResult[];
  steps_completed: string[];
  steps_failed: string[];
}

interface RunRow {
  id: string;
  target_email: string;
  target_display_name?: string;
  actor_email: string;
  status: string;
  started_at: string;
  completed_at?: string;
  error_summary?: string;
}

interface GroupOption { id: string; displayName: string; description?: string; }
interface LicenseOption { skuId: string; skuPartNumber: string; consumedUnits: number; prepaidUnits: number; }

type Step = 'form' | 'preview' | 'confirm' | 'running' | 'result';

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

// ── Step result row ────────────────────────────────────────────────────────────

function StepRow({ step }: { step: StepResult }) {
  const icon =
    step.status === 'ok'      ? <CheckCircle2 size={14} className="text-green-500" /> :
    step.status === 'failed'  ? <XCircle size={14} className="text-red-500" /> :
                                <Clock size={14} className="text-gray-400" />;
  const label = step.step.replace(/_/g, ' ').replace(/:/g, ': ');
  const detail = step.detail || step.error || step.reason || '';
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-gray-50 last:border-0">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-gray-800">{label}</p>
        {detail && <p className="text-xs text-gray-500 truncate">{detail}</p>}
      </div>
    </div>
  );
}

// ── Section header ─────────────────────────────────────────────────────────────

function SectionHeader({ icon: Icon, title }: { icon: React.ElementType; title: string }) {
  return (
    <div className="flex items-center gap-2 mb-3 pb-1 border-b border-gray-100">
      <Icon size={14} className="text-blue-500" />
      <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{title}</span>
    </div>
  );
}

// ── Main panel ─────────────────────────────────────────────────────────────────

export default function OnboardingPanel() {
  const [step, setStep] = useState<Step>('form');
  const [form, setForm] = useState<OnboardingForm>(EMPTY_FORM);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  // Groups + licenses pickers
  const [groups, setGroups] = useState<GroupOption[]>([]);
  const [licenses, setLicenses] = useState<LicenseOption[]>([]);
  const [groupSearch, setGroupSearch] = useState('');
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loadingLicenses, setLoadingLicenses] = useState(false);

  // Run history
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [selectedRun, setSelectedRun] = useState<RunResult | null>(null);

  const set = (patch: Partial<OnboardingForm>) => setForm(f => ({ ...f, ...patch }));

  // Auto-fill display name and mail nickname from first/last name
  const handleNameChange = (field: 'first_name' | 'last_name', value: string) => {
    const fn = field === 'first_name' ? value : form.first_name;
    const ln = field === 'last_name' ? value : form.last_name;
    const display = `${fn} ${ln}`.trim();
    const nickname = `${fn.toLowerCase().replace(/\s/g, '')}.${ln.toLowerCase().replace(/\s/g, '')}`;
    set({ [field]: value, display_name: display || form.display_name,
          mail_nickname: form.mail_nickname || nickname });
  };

  // Auto-fill UPN from mail_nickname
  const handleNicknameChange = (v: string) => {
    const domain = form.upn.includes('@') ? form.upn.split('@')[1] : '';
    set({ mail_nickname: v, upn: domain ? `${v}@${domain}` : form.upn });
  };

  const loadGroups = useCallback(async (search = '') => {
    setLoadingGroups(true);
    try {
      const r = await api.fetch<{ groups: GroupOption[] }>(`/api/v1/admin/onboarding/groups${search ? `?search=${encodeURIComponent(search)}` : ''}`);
      setGroups(r.groups || []);
    } catch { setGroups([]); } finally { setLoadingGroups(false); }
  }, []);

  const loadLicenses = useCallback(async () => {
    setLoadingLicenses(true);
    try {
      const r = await api.fetch<{ licenses: LicenseOption[] }>('/api/v1/admin/onboarding/licenses');
      setLicenses(r.licenses || []);
    } catch { setLicenses([]); } finally { setLoadingLicenses(false); }
  }, []);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const r = await api.fetch<{ runs: RunRow[] }>('/api/v1/admin/onboarding/runs?limit=20');
      setRuns(r.runs || []);
    } catch { setRuns([]); } finally { setRunsLoading(false); }
  }, []);

  useEffect(() => { loadRuns(); loadGroups(); loadLicenses(); }, [loadRuns, loadGroups, loadLicenses]);

  // ── Validation ───────────────────────────────────────────────────────────────

  const validateForm = (): string[] => {
    const errs: string[] = [];
    if (!form.first_name.trim()) errs.push('First name is required');
    if (!form.last_name.trim()) errs.push('Last name is required');
    if (!form.display_name.trim()) errs.push('Display name is required');
    if (!form.upn.trim()) errs.push('UPN is required');
    else if (!form.upn.includes('@')) errs.push('UPN must include @domain');
    if (!form.mail_nickname.trim()) errs.push('Mail nickname is required');
    return errs;
  };

  // ── Step handlers ────────────────────────────────────────────────────────────

  const handlePreview = async () => {
    const errs = validateForm();
    if (errs.length) { setErrors(errs); return; }
    setErrors([]);
    setLoading(true);
    try {
      const payload = buildPayload();
      const r = await api.fetch<PreviewResult>('/api/v1/admin/onboarding/preview', {
        method: 'POST',
        body: JSON.stringify(payload),
        headers: { 'Content-Type': 'application/json' },
      });
      setPreview(r);
      if (r.valid) setStep('preview');
      else setErrors(r.errors);
    } catch (e: any) {
      setErrors([e.message || 'Preview failed']);
    } finally { setLoading(false); }
  };

  const handleExecute = async () => {
    setStep('running');
    setLoading(true);
    try {
      const payload = buildPayload();
      const r = await api.fetch<RunResult>('/api/v1/admin/onboarding/execute', {
        method: 'POST',
        body: JSON.stringify(payload),
        headers: { 'Content-Type': 'application/json' },
      });
      setResult(r);
      setStep('result');
      loadRuns();
    } catch (e: any) {
      setErrors([e.message || 'Execution failed']);
      setStep('preview');
    } finally { setLoading(false); }
  };

  const handleReset = () => {
    setForm(EMPTY_FORM); setStep('form'); setPreview(null);
    setResult(null); setErrors([]);
  };

  const buildPayload = () => ({
    ...form,
    welcome_recipients: form.welcome_recipients
      ? form.welcome_recipients.split(',').map(s => s.trim()).filter(Boolean)
      : [],
    orientation_datetime: form.orientation_datetime || undefined,
  });

  const loadRunDetail = async (id: string) => {
    try {
      const r = await api.fetch<RunResult>(`/api/v1/admin/onboarding/runs/${id}`);
      setSelectedRun(r as any);
    } catch { /* ignore */ }
  };

  // ── Toggle group/license selection ───────────────────────────────────────────

  const toggleGroup = (id: string) =>
    set({ group_ids: form.group_ids.includes(id)
      ? form.group_ids.filter(g => g !== id)
      : [...form.group_ids, id] });

  const toggleLicense = (id: string) =>
    set({ sku_ids: form.sku_ids.includes(id)
      ? form.sku_ids.filter(s => s !== id)
      : [...form.sku_ids, id] });

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <UserPlus size={20} className="text-blue-600" />
            Employee Onboarding
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Create Entra user accounts and run the full onboarding workflow
          </p>
        </div>
        {step !== 'form' && (
          <button onClick={handleReset}
            className="text-sm text-gray-500 hover:text-gray-800 flex items-center gap-1">
            <RefreshCw size={14} /> New onboarding
          </button>
        )}
      </div>

      {/* Progress indicator */}
      <div className="flex items-center gap-1 text-xs text-gray-400">
        {(['form','preview','confirm','running','result'] as Step[]).map((s, i) => (
          <span key={s} className="flex items-center gap-1">
            {i > 0 && <ChevronRight size={10} />}
            <span className={step === s ? 'text-blue-600 font-semibold' : step > s ? 'text-gray-700' : ''}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
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
          {/* Left column */}
          <div className="space-y-5">
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <SectionHeader icon={Users} title="Identity" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="First name *" value={form.first_name}
                  onChange={v => handleNameChange('first_name', v)} placeholder="Jane" />
                <Field label="Last name *" value={form.last_name}
                  onChange={v => handleNameChange('last_name', v)} placeholder="Smith" />
              </div>
              <Field label="Display name *" value={form.display_name}
                onChange={v => set({ display_name: v })} placeholder="Jane Smith" className="mt-3" />
              <Field label="Mail nickname *" value={form.mail_nickname}
                onChange={handleNicknameChange} placeholder="jane.smith" className="mt-3"
                hint="Used to generate UPN and email address" />
              <Field label="UPN (user@domain) *" value={form.upn}
                onChange={v => set({ upn: v })} placeholder="jane.smith@armely.com" className="mt-3" />
              <Field label="Work email" value={form.work_email}
                onChange={v => set({ work_email: v })} placeholder="jane.smith@armely.com" className="mt-3"
                hint="If different from UPN" />
            </div>

            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <SectionHeader icon={Building2} title="Profile" />
              <Field label="Department" value={form.department}
                onChange={v => set({ department: v })} placeholder="Engineering" />
              <Field label="Job title" value={form.job_title}
                onChange={v => set({ job_title: v })} placeholder="Software Engineer" className="mt-3" />
              <Field label="Manager email" value={form.manager_email}
                onChange={v => set({ manager_email: v })} placeholder="manager@armely.com" className="mt-3" />
              <Field label="Usage location" value={form.usage_location}
                onChange={v => set({ usage_location: v })} placeholder="US" className="mt-3"
                hint="ISO 3166-1 alpha-2 (required for license assignment)" />
            </div>
          </div>

          {/* Right column */}
          <div className="space-y-5">
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <SectionHeader icon={Tag} title="Groups & Licenses" />
              {/* Group picker */}
              <p className="text-xs font-medium text-gray-600 mb-2">Groups</p>
              <div className="flex gap-2 mb-2">
                <input
                  value={groupSearch}
                  onChange={e => setGroupSearch(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && loadGroups(groupSearch)}
                  placeholder="Search groups…"
                  className="flex-1 text-sm border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-300"
                />
                <button onClick={() => loadGroups(groupSearch)}
                  className="px-3 py-1.5 text-sm bg-gray-100 rounded-lg hover:bg-gray-200">
                  {loadingGroups ? <Loader2 size={12} className="animate-spin" /> : 'Search'}
                </button>
              </div>
              <div className="max-h-32 overflow-y-auto space-y-1 border border-gray-100 rounded-lg p-1.5">
                {groups.length === 0 && (
                  <p className="text-xs text-gray-400 px-1">No groups loaded. Search above.</p>
                )}
                {groups.map(g => (
                  <label key={g.id} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-gray-50 cursor-pointer">
                    <input type="checkbox" checked={form.group_ids.includes(g.id)}
                      onChange={() => toggleGroup(g.id)}
                      className="accent-blue-600" />
                    <span className="text-xs text-gray-700">{g.displayName}</span>
                  </label>
                ))}
              </div>
              {form.group_ids.length > 0 && (
                <p className="text-xs text-blue-600 mt-1">{form.group_ids.length} group(s) selected</p>
              )}

              {/* License picker */}
              <p className="text-xs font-medium text-gray-600 mt-4 mb-2">Licenses</p>
              {loadingLicenses ? (
                <Loader2 size={14} className="animate-spin text-gray-400" />
              ) : (
                <div className="max-h-32 overflow-y-auto space-y-1 border border-gray-100 rounded-lg p-1.5">
                  {licenses.length === 0 && (
                    <p className="text-xs text-gray-400 px-1">No licenses available (check Graph permissions).</p>
                  )}
                  {licenses.map(l => (
                    <label key={l.skuId} className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-gray-50 cursor-pointer">
                      <input type="checkbox" checked={form.sku_ids.includes(l.skuId)}
                        onChange={() => toggleLicense(l.skuId)}
                        className="accent-blue-600" />
                      <span className="text-xs text-gray-700">{l.skuPartNumber}</span>
                      <span className="text-xs text-gray-400 ml-auto">{l.consumedUnits}/{l.prepaidUnits}</span>
                    </label>
                  ))}
                </div>
              )}
              {form.sku_ids.length > 0 && (
                <p className="text-xs text-blue-600 mt-1">{form.sku_ids.length} license(s) selected</p>
              )}
            </div>

            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <SectionHeader icon={Calendar} title="Onboarding Actions" />
              <Toggle label="Schedule orientation meeting"
                checked={form.schedule_orientation}
                onChange={v => set({ schedule_orientation: v })} />
              {form.schedule_orientation && (
                <Field label="Orientation date/time" type="datetime-local"
                  value={form.orientation_datetime}
                  onChange={v => set({ orientation_datetime: v })} className="mt-2" />
              )}
              <Toggle label="Send welcome email" checked={form.send_welcome_email}
                onChange={v => set({ send_welcome_email: v })} className="mt-3" />
              {form.send_welcome_email && (
                <Field label="Welcome recipients (comma-sep)" value={form.welcome_recipients}
                  onChange={v => set({ welcome_recipients: v })}
                  placeholder="Leave blank to auto-fill with work email" className="mt-2" />
              )}
              <Toggle label="Create onboarding tasks" checked={form.create_tasks}
                onChange={v => set({ create_tasks: v })} className="mt-3" />
            </div>

            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <SectionHeader icon={FileText} title="Notes & Reference" />
              <Field label="Notes" value={form.notes}
                onChange={v => set({ notes: v })} multiline placeholder="Optional notes for the welcome email and audit log" />
              <Field label="Approval reference" value={form.approval_reference}
                onChange={v => set({ approval_reference: v })} placeholder="Ticket / approval ID" className="mt-3" />
            </div>
          </div>

          {/* Submit */}
          <div className="lg:col-span-2">
            <button
              onClick={handlePreview}
              disabled={loading}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 font-medium text-sm"
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
              Preview onboarding
            </button>
          </div>
        </div>
      )}

      {/* ── PREVIEW ── */}
      {step === 'preview' && preview && (
        <div className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <h3 className="font-semibold text-gray-900 mb-4">Onboarding Preview</h3>

            {preview.warnings?.length > 0 && (
              <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg p-3 space-y-1">
                {preview.warnings.map((w, i) => (
                  <p key={i} className="text-sm text-yellow-800 flex items-start gap-1.5">
                    <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {w}
                  </p>
                ))}
              </div>
            )}

            <div className="grid grid-cols-2 gap-4 mb-5">
              <PreviewRow label="Display Name" value={preview.display_name} />
              <PreviewRow label="UPN" value={preview.target_upn} />
              <PreviewRow label="Department" value={preview.department || '—'} />
              <PreviewRow label="Job Title" value={preview.job_title || '—'} />
              <PreviewRow label="Usage Location" value={preview.usage_location} />
              <PreviewRow label="Groups" value={`${preview.group_count ?? 0} group(s)`} />
              <PreviewRow label="Licenses" value={`${preview.license_count ?? 0} license(s)`} />
            </div>

            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Steps to execute</p>
              <div className="space-y-1">
                {(preview.steps_planned || []).map((s, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm text-gray-700">
                    <CheckCircle2 size={13} className="text-blue-400" />
                    {s}
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-5 flex gap-3">
              <button onClick={() => setStep('confirm')}
                className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium text-sm">
                <Play size={14} /> Confirm and execute
              </button>
              <button onClick={() => setStep('form')}
                className="px-4 py-2.5 text-sm text-gray-600 hover:text-gray-900 border border-gray-200 rounded-lg">
                Edit
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── CONFIRM ── */}
      {step === 'confirm' && (
        <div className="bg-white border border-blue-200 rounded-xl p-6 max-w-lg">
          <div className="flex items-start gap-3">
            <Shield size={20} className="text-blue-500 mt-0.5" />
            <div>
              <h3 className="font-semibold text-gray-900">Confirm Onboarding</h3>
              <p className="text-sm text-gray-500 mt-1">
                You are about to create an Entra ID account for{' '}
                <strong>{form.display_name}</strong> ({form.upn}) and run all configured onboarding steps.
              </p>
              <p className="text-sm text-gray-500 mt-2">
                A temporary password will be generated and shown after execution. This action is logged.
              </p>
              <div className="flex gap-3 mt-5">
                <button onClick={handleExecute} disabled={loading}
                  className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium text-sm disabled:opacity-50">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                  Execute onboarding
                </button>
                <button onClick={() => setStep('preview')}
                  className="px-4 py-2.5 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
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
          <Loader2 size={32} className="animate-spin text-blue-500" />
          <p className="text-sm text-gray-500">Running onboarding workflow…</p>
          <p className="text-xs text-gray-400">Creating Entra user, assigning roles, sending welcome email…</p>
        </div>
      )}

      {/* ── RESULT ── */}
      {step === 'result' && result && (
        <div className="space-y-4">
          <div className={`border rounded-xl p-5 ${
            result.status === 'completed' ? 'bg-green-50 border-green-200' :
            result.status === 'partial'   ? 'bg-yellow-50 border-yellow-200' :
                                            'bg-red-50 border-red-200'
          }`}>
            <div className="flex items-center gap-3 mb-3">
              {result.status === 'completed' ? <CheckCircle2 size={20} className="text-green-600" /> :
               result.status === 'partial'   ? <AlertTriangle size={20} className="text-yellow-600" /> :
                                               <XCircle size={20} className="text-red-600" />}
              <div>
                <h3 className="font-semibold text-gray-900">
                  Onboarding {result.status === 'completed' ? 'completed' :
                              result.status === 'partial'   ? 'partially completed' : 'failed'}
                </h3>
                <p className="text-sm text-gray-500">Run ID: {result.run_id}</p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-4">
              <PreviewRow label="User" value={result.target_display_name} />
              <PreviewRow label="UPN" value={result.target_upn} />
              {result.entra_id && <PreviewRow label="Entra ID" value={result.entra_id} />}
            </div>

            {result.temp_password && (
              <div className="bg-white border border-blue-200 rounded-lg p-3 mb-4">
                <p className="text-xs font-semibold text-blue-700 mb-1">⚠ Temporary password — share securely</p>
                <code className="text-sm font-mono text-gray-900 select-all">{result.temp_password}</code>
                <p className="text-xs text-gray-400 mt-1">User must change on first login.</p>
              </div>
            )}

            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Step Results</p>
              {result.steps.map((s, i) => <StepRow key={i} step={s} />)}
            </div>

            <button onClick={handleReset} className="mt-4 text-sm text-blue-600 hover:underline">
              Start new onboarding
            </button>
          </div>
        </div>
      )}

      {/* ── RUN HISTORY ── */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
          <p className="text-sm font-semibold text-gray-800">Recent Runs</p>
          <button onClick={loadRuns} className="text-gray-400 hover:text-gray-700">
            {runsLoading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          </button>
        </div>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-400 px-5 py-4">No onboarding runs yet.</p>
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
              <h3 className="font-semibold text-gray-900">Run Detail</h3>
              <button onClick={() => setSelectedRun(null)} className="text-gray-400 hover:text-gray-700">✕</button>
            </div>
            <div className="grid grid-cols-2 gap-3 mb-4">
              <PreviewRow label="Target" value={(selectedRun as any).target_display_name || (selectedRun as any).target_email} />
              <PreviewRow label="Status" value={<StatusBadge status={(selectedRun as any).status} />} />
            </div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Steps</p>
            {((selectedRun as any).step_results || []).map((s: StepResult, i: number) =>
              <StepRow key={i} step={s} />)}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Small reusable components ──────────────────────────────────────────────────

function Field({
  label, value, onChange, placeholder, hint, className = '', type = 'text', multiline = false,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; hint?: string; className?: string;
  type?: string; multiline?: boolean;
}) {
  const base = "w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300";
  return (
    <div className={className}>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      {multiline ? (
        <textarea value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} rows={3} className={base} />
      ) : (
        <input type={type} value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} className={base} />
      )}
      {hint && <p className="text-xs text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

function Toggle({ label, checked, onChange, className = '' }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; className?: string;
}) {
  return (
    <label className={`flex items-center gap-3 cursor-pointer ${className}`}>
      <div
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${checked ? 'bg-blue-600' : 'bg-gray-200'}`}
      >
        <span className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transform transition-transform ${checked ? 'translate-x-4' : 'translate-x-0.5'}`} />
      </div>
      <span className="text-sm text-gray-700">{label}</span>
    </label>
  );
}

function PreviewRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-sm font-medium text-gray-800 mt-0.5">{value ?? '—'}</p>
    </div>
  );
}
