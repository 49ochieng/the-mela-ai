/**
 * Mela AI - Enterprise Admin Console
 * Sidebar-based control panel — 8 sections.
 */

'use client';

import { useEffect, useState, useRef, useCallback, lazy, Suspense } from 'react';
import { useRouter } from 'next/navigation';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import {
  LayoutDashboard,
  Users,
  Building2,
  Cpu,
  AlertTriangle,
  Receipt,
  Settings,
  ClipboardList,
  ChevronLeft,
  Loader2,
  ShieldOff,
  UserPlus,
  UserMinus,
} from 'lucide-react';
import { api, AdminAccessRequest } from '@/lib/api';

// Lazy-load heavy panels to keep initial bundle small
const OverviewPanel    = lazy(() => import('@/components/admin/OverviewPanel'));
const UsersPanel       = lazy(() => import('@/components/admin/UsersPanel'));
const TenantsPanel     = lazy(() => import('@/components/admin/TenantsPanel'));
const ModelsPanel      = lazy(() => import('@/components/admin/ModelsPanel'));
const ErrorsPanel      = lazy(() => import('@/components/admin/ErrorsPanel'));
const InvoicesPanel    = lazy(() => import('@/components/admin/InvoicesPanel'));
const SettingsPanel    = lazy(() => import('@/components/admin/SettingsPanel'));
const AuditPanel       = lazy(() => import('@/components/admin/AuditPanel'));
const OnboardingPanel  = lazy(() => import('@/components/admin/OnboardingPanel'));
const OffboardingPanel = lazy(() => import('@/components/admin/OffboardingPanel'));

// ── Sidebar nav items ─────────────────────────────────────────────────────────

type Section =
  | 'overview'
  | 'users'
  | 'tenants'
  | 'models'
  | 'errors'
  | 'invoices'
  | 'settings'
  | 'audit'
  | 'onboarding'
  | 'offboarding';

interface NavItem {
  id: Section;
  label: string;
  icon: React.ElementType;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'overview',  label: 'Overview',         icon: LayoutDashboard },
  { id: 'users',     label: 'Users',             icon: Users           },
  { id: 'tenants',   label: 'Tenants',           icon: Building2       },
  { id: 'models',    label: 'Model Governance',  icon: Cpu             },
  { id: 'errors',    label: 'Error Log',         icon: AlertTriangle   },
  // Phase 4: Invoices, Onboarding, Offboarding panels are UI shells only —
  // hide them from the sidebar until the backend endpoints exist.  Routes
  // remain reachable for internal demos via direct URL navigation.
  // { id: 'invoices',    label: 'Invoices',          icon: Receipt         },
  { id: 'settings',    label: 'Settings',          icon: Settings        },
  { id: 'audit',       label: 'Audit Trail',       icon: ClipboardList   },
  // { id: 'onboarding',  label: 'Onboarding',        icon: UserPlus        },
  // { id: 'offboarding', label: 'Offboarding',       icon: UserMinus       },
];

// ── Access denied state ───────────────────────────────────────────────────────

function AccessDenied() {
  const router = useRouter();
  const [requested, setRequested] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [dots, setDots] = useState('');

  // Animate the "waiting" dots so the user knows polling is live
  useEffect(() => {
    const t = setInterval(() => setDots((d) => (d.length >= 3 ? '' : d + '.')), 600);
    return () => clearInterval(t);
  }, []);

  const requestAccess = async () => {
    setRequesting(true);
    try {
      await api.requestAdminAccess();
      setRequested(true);
    } catch {
      setRequested(true); // show success regardless to avoid enumeration
    } finally {
      setRequesting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center max-w-sm px-4">
        <ShieldOff size={48} className="mx-auto mb-4 text-gray-300" />
        <h1 className="text-xl font-semibold text-gray-800 mb-2">Access Denied</h1>
        <p className="text-sm text-gray-500 mb-3">
          Your account does not have administrator privileges. Ask an existing admin
          to elevate your role — access will unlock automatically the moment you are promoted.
        </p>
        <p className="text-xs text-gray-400 mb-6 flex items-center justify-center gap-1.5">
          <Loader2 size={12} className="animate-spin" />
          Checking for elevation{dots}
        </p>
        <div className="flex flex-col gap-2 items-center">
          {requested ? (
            <p className="text-sm text-green-600 font-medium">
              Request sent — an admin will be notified by email.
            </p>
          ) : (
            <button
              onClick={requestAccess}
              disabled={requesting}
              className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 w-full"
            >
              {requesting ? 'Sending request…' : 'Request Admin Access'}
            </button>
          )}
          <button onClick={() => router.push('/chat')} className="px-4 py-2 text-sm text-gray-500 hover:text-gray-700">
            Back to Chat
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Panel loader ──────────────────────────────────────────────────────────────

function PanelFallback() {
  return (
    <div className="flex items-center justify-center h-48 text-gray-400">
      <Loader2 size={24} className="animate-spin" />
    </div>
  );
}

function ActivePanel({ section, accessRequests, onRequestsChange }: {
  section: Section;
  accessRequests: AdminAccessRequest[];
  onRequestsChange: () => void;
}) {
  return (
    <Suspense fallback={<PanelFallback />}>
      {section === 'overview'  && <OverviewPanel />}
      {section === 'users'     && <UsersPanel accessRequests={accessRequests} onRequestsChange={onRequestsChange} />}
      {section === 'tenants'   && <TenantsPanel />}
      {section === 'models'    && <ModelsPanel />}
      {section === 'errors'    && <ErrorsPanel />}
      {section === 'invoices'  && <InvoicesPanel />}
      {section === 'settings'    && <SettingsPanel />}
      {section === 'audit'       && <AuditPanel />}
      {section === 'onboarding'  && <OnboardingPanel />}
      {section === 'offboarding' && <OffboardingPanel />}
    </Suspense>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const router = useRouter();
  const { instance } = useMsal();
  const isAuthenticated = useIsAuthenticated();

  const [checking, setChecking] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [section, setSection] = useState<Section>('overview');
  const [accessRequests, setAccessRequests] = useState<AdminAccessRequest[]>([]);

  const loadAccessRequests = useCallback(async () => {
    try {
      const reqs = await api.getAdminAccessRequests();
      setAccessRequests(reqs);
    } catch { /* non-fatal */ }
  }, []);

  // Poll for admin status — refreshes every 4 s while waiting for elevation.
  // Stops as soon as is_admin becomes true.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const checkAdmin = useCallback(async () => {
    try {
      const r = await api.getAdminStatus();
      if (r.is_admin) {
        setIsAdmin(true);
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
      return r.is_admin;
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    const devAuth = api.isDevAuthenticated();
    if (!isAuthenticated && !devAuth) {
      router.push('/');
      return;
    }

    // ── Critical fix: set MSAL instance so getAccessToken() can acquire a
    // Bearer token.  Without this, api.fetch sends no Authorization header
    // and the backend always returns 401 → "Access Denied".
    if (!devAuth && instance) {
      api.setMsalInstance(instance);
    }

    (async () => {
      // In local dev: if MSAL backend token may fail, pre-emptively acquire a
      // dev token so API calls succeed regardless of Azure AD consent state.
      if (!devAuth && process.env.NEXT_PUBLIC_DEV_USERNAME) {
        try { await api.devLogin(); } catch { /* dev login not available in prod */ }
      }
      const isAdminNow = await checkAdmin();
      setChecking(false);

      if (isAdminNow) {
        loadAccessRequests();
      } else {
        // Poll every 4 s so the page unlocks automatically once elevated.
        pollRef.current = setInterval(checkAdmin, 4000);
      }
    })();

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [isAuthenticated, instance, router, checkAdmin, loadAccessRequests]);

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 size={32} className="animate-spin text-gray-300" />
      </div>
    );
  }

  if (!isAdmin) return <AccessDenied />;

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="w-60 flex-shrink-0 bg-white border-r border-gray-100 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-gray-100">
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wider font-medium">Mela AI</p>
            <p className="text-sm font-semibold text-gray-800">Admin Console</p>
          </div>
          <button
            onClick={() => router.push('/chat')}
            className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-700"
            title="Back to Chat"
          >
            <ChevronLeft size={16} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 overflow-y-auto">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setSection(id)}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                section === id
                  ? 'bg-blue-50 text-blue-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
              }`}
            >
              <Icon size={16} className={section === id ? 'text-blue-600' : 'text-gray-400'} />
              <span className="flex-1 text-left">{label}</span>
              {id === 'users' && accessRequests.length > 0 && (
                <span className="ml-auto min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
                  {accessRequests.length}
                </span>
              )}
            </button>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-gray-100">
          <p className="text-xs text-gray-400">Mela AI Enterprise</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <ActivePanel section={section} accessRequests={accessRequests} onRequestsChange={loadAccessRequests} />
      </main>
    </div>
  );
}
