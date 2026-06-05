'use client';

import { useEffect, useState } from 'react';
import { useTheme } from 'next-themes';
import { useChatStore } from '@/lib/store';
import {
  Dialog,
  DialogContent,
} from '@/components/ui/Dialog';
import { Switch } from '@/components/ui/Switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { UsageTab } from './UsageTab';
import { ConnectorsTab } from './ConnectorsTab';
import { KnowledgeTab } from './KnowledgeTab';
import { AgentMemoryTab } from './AgentMemoryTab';
import { AdminTab } from './AdminTab';
import { ModelsTab } from './ModelsTab';
import { InstructionsTab } from './InstructionsTab';
import { SkillsTab } from './SkillsTab';
import { MonitoringTab } from './MonitoringTab';
import { WorkflowsTab } from './WorkflowsTab';
import { WorkerRegistryTab } from './WorkerRegistryTab';
import { MCPClientsTab } from './MCPClientsTab';
import { toast } from 'sonner';
import {
  Palette,
  BarChart3,
  Plug,
  Shield,
  Cpu,
  BookOpen,
  Zap,
  Sun,
  Moon,
  Monitor,
  Download,
  Trash2,
  Brain,
  AlertTriangle,
  Settings2,
  Database,
  Activity,
  GitBranch,
  PlugZap,
  Key,
  X,
} from 'lucide-react';
import { cn } from '@/lib/utils';

type TabId = 'appearance' | 'usage' | 'knowledge' | 'agent-memory' | 'connectors' | 'models' | 'instructions' | 'skills' | 'privacy' | 'admin' | 'monitoring' | 'workflows' | 'workers' | 'mcp-clients';

interface NavItem {
  id: TabId;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
  adminOnly?: boolean;
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Personal',
    items: [
      { id: 'appearance',   label: 'Appearance',   icon: <Palette className="h-4 w-4" /> },
      { id: 'models',       label: 'Models',        icon: <Cpu className="h-4 w-4" /> },
      { id: 'usage',        label: 'Usage',         icon: <BarChart3 className="h-4 w-4" /> },
      { id: 'privacy',      label: 'Privacy',       icon: <Shield className="h-4 w-4" /> },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { id: 'knowledge',    label: 'Knowledge',     icon: <Database className="h-4 w-4" /> },
      { id: 'agent-memory', label: 'Agent Memory',  icon: <Brain className="h-4 w-4" /> },
      { id: 'connectors',   label: 'Connectors',    icon: <Plug className="h-4 w-4" /> },
      { id: 'instructions', label: 'Instructions',  icon: <BookOpen className="h-4 w-4" /> },
      { id: 'skills',       label: 'Skills',        icon: <Zap className="h-4 w-4" /> },
      { id: 'workflows',    label: 'Workflows',     icon: <GitBranch className="h-4 w-4" /> },
    ],
  },
  {
    label: 'Admin',
    adminOnly: true,
    items: [
      { id: 'admin',        label: 'Admin',         icon: <Settings2 className="h-4 w-4" />, adminOnly: true },
      { id: 'workers',      label: 'Workers',       icon: <PlugZap className="h-4 w-4" />, adminOnly: true },
      { id: 'mcp-clients',  label: 'MCP Clients',   icon: <Key className="h-4 w-4" />, adminOnly: true },
      { id: 'monitoring',   label: 'Monitoring',    icon: <Activity className="h-4 w-4" />, adminOnly: true },
    ],
  },
];

// Tab display names for the content header
const TAB_META: Record<TabId, { title: string; description: string }> = {
  appearance:    { title: 'Appearance',    description: 'Customize the look and feel of Mela AI' },
  models:        { title: 'Models',        description: 'Configure AI model preferences and defaults' },
  usage:         { title: 'Usage',         description: 'View your token usage and activity stats' },
  privacy:       { title: 'Privacy',       description: 'Manage data retention, memory, and privacy controls' },
  knowledge:     { title: 'Knowledge',     description: 'Manage knowledge bases and document sources' },
  'agent-memory':{ title: 'Agent Memory',  description: 'Configure persistent memory for your AI agents' },
  connectors:    { title: 'Connectors',    description: 'Connect Microsoft 365, SharePoint, and more' },
  instructions:  { title: 'Instructions',  description: 'Define custom instructions for your AI assistant' },
  skills:        { title: 'Skills',        description: 'Enable and manage AI capabilities and tools' },
  workflows:     { title: 'Workflows',     description: 'Automate tasks with custom workflow definitions' },
  admin:         { title: 'Admin',         description: 'Manage organization-wide settings and users' },
  workers:       { title: 'Worker Registry', description: 'Connect external MCP workers to Mela\'s orchestration brain' },
  'mcp-clients': { title: 'MCP Clients',   description: 'Manage API clients that connect to Mela\'s MCP server' },
  monitoring:    { title: 'Monitoring',    description: 'View system health, logs, and performance metrics' },
};

export function SettingsModal() {
  const { isSettingsOpen, setSettingsOpen, userPreferences, fetchPreferences, updateUserPreferences, fetchFeatures, userFeatures } = useChatStore();
  const [activeTab, setActiveTab] = useState<TabId>('appearance');

  const isAdmin = userFeatures?.role === 'admin';

  useEffect(() => {
    if (isSettingsOpen) {
      fetchPreferences();
      fetchFeatures();
    }
  }, [isSettingsOpen, fetchPreferences, fetchFeatures]);

  const visibleGroups = NAV_GROUPS.filter((g) => !g.adminOnly || isAdmin);
  const currentMeta = TAB_META[activeTab];

  return (
    <Dialog open={isSettingsOpen} onOpenChange={setSettingsOpen}>
      <DialogContent className="max-w-5xl h-[700px] flex flex-col p-0 gap-0 overflow-hidden rounded-2xl shadow-2xl [&>button]:hidden">
        {/* ── Top bar ── */}
        <div className="flex items-center justify-between px-6 py-4 border-b bg-gradient-to-r from-primary/5 via-background to-background shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-primary flex items-center justify-center shadow-sm">
              <Settings2 className="h-4.5 w-4.5 text-white" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground leading-none">Settings</h2>
              <p className="text-xs text-muted-foreground mt-0.5">Mela AI workspace preferences</p>
            </div>
          </div>
          <button
            onClick={() => setSettingsOpen(false)}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* ── Sidebar ── */}
          <nav className="w-52 shrink-0 border-r bg-muted/20 flex flex-col overflow-y-auto py-3">
            {visibleGroups.map((group, gi) => (
              <div key={group.label} className={cn('px-3', gi > 0 && 'mt-1')}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60 px-2 py-1.5 select-none">
                  {group.label}
                </p>
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => setActiveTab(item.id)}
                      className={cn(
                        'flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg text-sm text-left transition-all duration-150',
                        activeTab === item.id
                          ? 'bg-primary text-white font-medium shadow-sm'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
                      )}
                    >
                      <span className={cn(
                        'shrink-0 transition-colors',
                        activeTab === item.id ? 'text-white' : 'text-muted-foreground',
                      )}>
                        {item.icon}
                      </span>
                      <span className="truncate">{item.label}</span>
                    </button>
                  ))}
                </div>
                {gi < visibleGroups.length - 1 && (
                  <div className="mt-3 mb-1 border-t border-border/50" />
                )}
              </div>
            ))}
          </nav>

          {/* ── Content ── */}
          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            {/* Section header */}
            <div className="px-6 py-4 border-b bg-background/50 shrink-0">
              <h3 className="text-sm font-semibold text-foreground">{currentMeta.title}</h3>
              <p className="text-xs text-muted-foreground mt-0.5">{currentMeta.description}</p>
            </div>

            {/* Tab body */}
            <div className="flex-1 overflow-y-auto p-6">
              {activeTab === 'appearance' && (
                <AppearanceSection preferences={userPreferences} onUpdate={updateUserPreferences} />
              )}
              {activeTab === 'usage' && <UsageTab />}
              {activeTab === 'knowledge' && <KnowledgeTab />}
              {activeTab === 'agent-memory' && <AgentMemoryTab />}
              {activeTab === 'connectors' && <ConnectorsTab />}
              {activeTab === 'models' && <ModelsTab />}
              {activeTab === 'instructions' && <InstructionsTab />}
              {activeTab === 'skills' && <SkillsTab />}
              {activeTab === 'privacy' && (
                <PrivacySection preferences={userPreferences} features={userFeatures} onUpdate={updateUserPreferences} />
              )}
              {activeTab === 'admin' && isAdmin && <AdminTab />}
              {activeTab === 'workers' && isAdmin && <WorkerRegistryTab />}
              {activeTab === 'mcp-clients' && isAdmin && <MCPClientsTab />}
              {activeTab === 'monitoring' && isAdmin && <MonitoringTab />}
              {activeTab === 'workflows' && <WorkflowsTab />}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Appearance (inline) ──────────────────────────────────────────────────────

interface AppearanceSectionProps {
  preferences: { theme: string; memory_enabled: boolean; data_retention_days: number } | null;
  onUpdate: (prefs: Partial<{ theme: string; memory_enabled: boolean; data_retention_days: number }>) => Promise<void>;
}

function AppearanceSection({ preferences, onUpdate }: AppearanceSectionProps) {
  const { theme, setTheme } = useTheme();

  const themes = [
    { value: 'light', label: 'Light', icon: <Sun className="h-5 w-5" /> },
    { value: 'dark', label: 'Dark', icon: <Moon className="h-5 w-5" /> },
    { value: 'system', label: 'System', icon: <Monitor className="h-5 w-5" /> },
  ];

  const handleThemeChange = (value: string) => {
    setTheme(value);
    onUpdate({ theme: value });
  };

  const currentTheme = theme || preferences?.theme || 'system';

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-sm font-semibold text-foreground mb-1">Theme</h3>
        <p className="text-xs text-muted-foreground mb-4">
          Choose how Mela AI looks to you
        </p>
        <div className="grid grid-cols-3 gap-3">
          {themes.map((t) => (
            <button
              key={t.value}
              onClick={() => handleThemeChange(t.value)}
              className={cn(
                'flex flex-col items-center gap-3 p-5 rounded-xl border-2 transition-all duration-150 shadow-sm hover:shadow-md',
                currentTheme === t.value
                  ? 'border-primary bg-primary/5 ring-2 ring-primary/20'
                  : 'border-border bg-card hover:border-primary/40',
              )}
            >
              <div
                className={cn(
                  'p-3 rounded-xl transition-colors',
                  currentTheme === t.value
                    ? 'bg-primary text-white shadow-sm'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                {t.icon}
              </div>
              <div className="text-center">
                <span className={cn(
                  'text-sm font-medium block',
                  currentTheme === t.value ? 'text-primary' : 'text-foreground',
                )}>
                  {t.label}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Privacy (inline) ─────────────────────────────────────────────────────────

interface PrivacySectionProps {
  preferences: { theme: string; memory_enabled: boolean; data_retention_days: number; default_private_mode?: boolean } | null;
  features: { role: string; sso_configured: boolean; features: Record<string, boolean> } | null;
  onUpdate: (prefs: Partial<{ theme: string; memory_enabled: boolean; data_retention_days: number; default_private_mode?: boolean }>) => Promise<void>;
}

function PrivacySection({ preferences, features, onUpdate }: PrivacySectionProps) {
  const { deleteAllHistory, exportData } = useChatStore();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteAllHistory();
      toast.success('All chat history deleted');
      setShowDeleteConfirm(false);
    } catch {
      toast.error('Failed to delete history');
    } finally {
      setDeleting(false);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      await exportData();
      toast.success('Data exported successfully');
    } catch {
      toast.error('Failed to export data');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Memory */}
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-md bg-purple-50 dark:bg-purple-950 text-purple-600">
              <Brain className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-medium">Conversational Memory</h3>
              <p className="text-xs text-muted-foreground">
                Allow AI to remember context across messages
              </p>
            </div>
          </div>
          <Switch
            checked={preferences?.memory_enabled ?? true}
            onCheckedChange={(checked) => onUpdate({ memory_enabled: checked })}
          />
        </div>
      </div>

      {/* Private chat default */}
      {features?.features?.['private_chat'] && (
        <div className="rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-violet-50 dark:bg-violet-950 text-violet-600">
                <Shield className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-medium">Always start in Private mode</h3>
                <p className="text-xs text-muted-foreground">
                  New chats will automatically use private (incognito) mode.
                </p>
              </div>
            </div>
            <Switch
              checked={preferences?.default_private_mode ?? false}
              onCheckedChange={(checked) => onUpdate({ default_private_mode: checked })}
            />
          </div>
        </div>
      )}

      {/* Data retention */}
      <div className="rounded-lg border bg-card p-4">
        <h3 className="text-sm font-medium mb-1">Data Retention</h3>
        <p className="text-xs text-muted-foreground mb-3">
          How long to keep your conversation history
        </p>
        <Select
          value={String(preferences?.data_retention_days ?? 365)}
          onValueChange={(v) => onUpdate({ data_retention_days: parseInt(v, 10) })}
        >
          <SelectTrigger className="w-[200px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="30">30 days</SelectItem>
            <SelectItem value="90">90 days</SelectItem>
            <SelectItem value="180">180 days</SelectItem>
            <SelectItem value="365">1 year</SelectItem>
            <SelectItem value="730">2 years</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Export & Delete */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="text-sm font-medium">Your Data</h3>
        <div className="flex items-center justify-between py-2">
          <div>
            <p className="text-sm">Export data</p>
            <p className="text-xs text-muted-foreground">Download all your conversations and data</p>
          </div>
          <Button variant="outline" size="sm" onClick={handleExport} isLoading={exporting}>
            <Download className="h-4 w-4 mr-1" />
            Export
          </Button>
        </div>
        <div className="border-t" />
        <div className="flex items-center justify-between py-2">
          <div>
            <p className="text-sm text-destructive font-medium">Delete all history</p>
            <p className="text-xs text-muted-foreground">Permanently remove all conversations</p>
          </div>
          {showDeleteConfirm ? (
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowDeleteConfirm(false)}>
                Cancel
              </Button>
              <Button variant="destructive" size="sm" onClick={handleDelete} isLoading={deleting}>
                <AlertTriangle className="h-4 w-4 mr-1" />
                Confirm
              </Button>
            </div>
          ) : (
            <Button variant="destructive" size="sm" onClick={() => setShowDeleteConfirm(true)}>
              <Trash2 className="h-4 w-4 mr-1" />
              Delete
            </Button>
          )}
        </div>
      </div>

      {/* Features (read-only) */}
      {features && Object.keys(features.features).length > 0 && (
        <div className="rounded-lg border bg-card p-4">
          <h3 className="text-sm font-medium mb-3">Available Features</h3>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(features.features).map(([key, enabled]) => (
              <div key={key} className="flex items-center gap-2 text-sm">
                <div className={`h-2 w-2 rounded-full ${enabled ? 'bg-green-500' : 'bg-muted'}`} />
                <span className="capitalize">{key.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t flex items-center gap-2 text-xs text-muted-foreground">
            <span>Role: <span className="font-medium capitalize">{features.role}</span></span>
            {features.sso_configured && (
              <span className="px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-950 text-green-700 dark:text-green-400 text-[10px] font-medium">
                SSO
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
