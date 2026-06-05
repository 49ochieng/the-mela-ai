'use client';

import { useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/Dialog';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import {
  Upload, Globe, Share2, Cloud, Search,
  Database, Briefcase, Cpu, ArrowLeft,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { AgentMemoryItem, AgentMemoryScope, AgentMemoryTag } from '@/lib/api';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';

type SourceId =
  | 'upload' | 'website' | 'sharepoint' | 'onedrive' | 'azuresearch'
  | 'dataverse' | 'd365' | 'salesforce' | 'servicenow' | 'azuresql';

interface SourceCard {
  id: SourceId;
  label: string;
  description: string;
  icon: React.ReactNode;
  available: boolean;
}

const SOURCES: SourceCard[] = [
  { id: 'upload',      label: 'Upload files',     description: 'PDF, Word, Excel, PowerPoint, Markdown, TXT', icon: <Upload className="h-5 w-5" />, available: true },
  { id: 'website',     label: 'Public website',   description: 'Crawl a public URL (sitemap-aware, SSRF-safe)', icon: <Globe className="h-5 w-5" />, available: true },
  { id: 'sharepoint',  label: 'SharePoint',       description: 'Available in the Connectors tab', icon: <Share2 className="h-5 w-5" />, available: false },
  { id: 'onedrive',    label: 'OneDrive',         description: 'Available in the Connectors tab', icon: <Cloud className="h-5 w-5" />, available: false },
  { id: 'azuresearch', label: 'Azure AI Search',  description: 'Coming soon', icon: <Search className="h-5 w-5" />, available: false },
  { id: 'dataverse',   label: 'Dataverse',        description: 'Coming soon', icon: <Database className="h-5 w-5" />, available: false },
  { id: 'd365',        label: 'Dynamics 365',     description: 'Coming soon', icon: <Briefcase className="h-5 w-5" />, available: false },
  { id: 'salesforce',  label: 'Salesforce',       description: 'Coming soon', icon: <Cpu className="h-5 w-5" />, available: false },
  { id: 'servicenow',  label: 'ServiceNow',       description: 'Coming soon', icon: <Cpu className="h-5 w-5" />, available: false },
  { id: 'azuresql',    label: 'Azure SQL',        description: 'Coming soon', icon: <Database className="h-5 w-5" />, available: false },
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (item: AgentMemoryItem) => void;
  defaultScope?: AgentMemoryScope;
  defaultTag?: AgentMemoryTag;
  canShareWithOrg: boolean;
}

export function AddKnowledgeModal({
  open, onOpenChange, onCreated, defaultScope = 'personal', defaultTag = 'knowledge', canShareWithOrg,
}: Props) {
  const [step, setStep] = useState<'choose' | 'configure'>('choose');
  const [picked, setPicked] = useState<SourceId | null>(null);
  const [scope, setScope] = useState<AgentMemoryScope>(defaultScope);
  const [tag, setTag] = useState<AgentMemoryTag>(defaultTag);
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setStep('choose'); setPicked(null);
    setScope(defaultScope); setTag(defaultTag);
    setTitle(''); setUrl(''); setSubmitting(false);
  };

  const close = () => { onOpenChange(false); reset(); };

  const choose = (s: SourceCard) => {
    if (!s.available) return;
    setPicked(s.id);
    setStep('configure');
  };

  const submitFile = async (file: File) => {
    setSubmitting(true);
    try {
      const item = await api.uploadAgentMemoryFile(file, { scope, tag, title: title || undefined });
      toast.success(`Added "${item.title}" — indexing in background.`);
      onCreated(item);
      close();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Upload failed');
    } finally { setSubmitting(false); }
  };

  const submitUrl = async () => {
    if (!url.trim()) { toast.error('Enter a URL'); return; }
    setSubmitting(true);
    try {
      const item = await api.addAgentMemoryWebsite({
        url: url.trim(), scope, tag, title: title || undefined,
      });
      toast.success(`Crawling "${item.title}" — this may take a moment.`);
      onCreated(item);
      close();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to add website');
    } finally { setSubmitting(false); }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close(); else onOpenChange(true); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <div className="flex items-center gap-2">
            {step === 'configure' && (
              <button
                onClick={() => setStep('choose')}
                className="text-muted-foreground hover:text-foreground"
                aria-label="Back"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            )}
            <DialogTitle>
              {step === 'choose' ? 'Add a knowledge source' : 'Configure source'}
            </DialogTitle>
          </div>
          <DialogDescription>
            {step === 'choose'
              ? 'Pick where Mela should learn from. Files and websites are processed locally with tenant isolation.'
              : 'Set scope, tag, and details. The agent will start indexing in the background.'}
          </DialogDescription>
        </DialogHeader>

        {step === 'choose' && (
          <div className="grid grid-cols-2 gap-2 mt-2 max-h-[440px] overflow-y-auto">
            {SOURCES.map((s) => (
              <button
                key={s.id}
                onClick={() => choose(s)}
                disabled={!s.available}
                className={cn(
                  'flex items-start gap-3 rounded-lg border p-3 text-left transition-colors',
                  s.available
                    ? 'hover:bg-accent hover:border-primary/40'
                    : 'opacity-50 cursor-not-allowed',
                )}
              >
                <div className="p-2 rounded-md bg-muted shrink-0">{s.icon}</div>
                <div className="min-w-0">
                  <div className="text-sm font-medium">{s.label}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">{s.description}</div>
                </div>
              </button>
            ))}
          </div>
        )}

        {step === 'configure' && picked && (
          <div className="space-y-4 mt-2">
            {/* Scope + tag */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium block mb-1">Scope</label>
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value as AgentMemoryScope)}
                  className="w-full h-9 rounded-md border bg-background px-2 text-sm"
                >
                  <option value="personal">Personal (only you)</option>
                  <option value="workspace" disabled={!canShareWithOrg}>
                    Workspace (your tenant)
                  </option>
                  <option value="tenant" disabled={!canShareWithOrg}>
                    Tenant (admin-wide)
                  </option>
                </select>
              </div>
              <div>
                <label className="text-xs font-medium block mb-1">Tag</label>
                <select
                  value={tag}
                  onChange={(e) => setTag(e.target.value as AgentMemoryTag)}
                  className="w-full h-9 rounded-md border bg-background px-2 text-sm"
                >
                  <option value="knowledge">Knowledge</option>
                  <option value="template">Template</option>
                  <option value="brand">Brand</option>
                  <option value="policy">Policy</option>
                  <option value="demo">Demo</option>
                </select>
              </div>
            </div>

            <div>
              <label className="text-xs font-medium block mb-1">
                Title <span className="text-muted-foreground">(optional)</span>
              </label>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="A short label, e.g. 'Q4 brand guide'"
              />
            </div>

            {picked === 'upload' && (
              <div>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept=".pdf,.docx,.xlsx,.pptx,.md,.txt,.csv,.html,.htm"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) submitFile(f);
                  }}
                />
                <Button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={submitting}
                  className="w-full"
                >
                  <Upload className="h-4 w-4 mr-2" />
                  {submitting ? 'Uploading…' : 'Choose file'}
                </Button>
                <p className="text-xs text-muted-foreground mt-2">
                  Max 25 MB. PDF, Word, Excel, PowerPoint, Markdown, TXT, CSV, HTML.
                </p>
              </div>
            )}

            {picked === 'website' && (
              <div className="space-y-2">
                <label className="text-xs font-medium block">URL</label>
                <Input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/docs"
                />
                <p className="text-xs text-muted-foreground">
                  Up to 50 pages, depth 2, same-origin. Private/loopback hosts blocked.
                </p>
                <Button onClick={submitUrl} disabled={submitting} className="w-full">
                  <Globe className="h-4 w-4 mr-2" />
                  {submitting ? 'Queueing…' : 'Crawl website'}
                </Button>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
