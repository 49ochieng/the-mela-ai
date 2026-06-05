'use client';

import { useChatStore } from '@/lib/store';
import { Database, Globe, Share2, FileText, CheckCircle2 } from 'lucide-react';
import { Switch } from '@/components/ui/Switch';

export function KnowledgeTab() {
  const { useRag, setUseRag, useWebSearch, setUseWebSearch, userFeatures, activeProfile } = useChatStore();
  const isWorkMode = activeProfile === 'work' || activeProfile === 'org';

  const hasRag = userFeatures?.features?.['rag'] !== false;
  const hasWebSearch = userFeatures?.features?.['web_search'] !== false;

  return (
    <div className="space-y-6">

      {/* Section: Enterprise Knowledge */}
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          Enterprise Knowledge
        </h3>

        <div className={`rounded-lg border bg-card p-4 ${!hasRag ? 'opacity-50' : ''}`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-blue-50 dark:bg-blue-950 text-blue-600">
                <Database className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-medium">Enterprise Data Sources</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {isWorkMode
                    ? "Work Mode automatically uses your organisation's indexed documents (SharePoint, OneDrive, intranet)."
                    : "Personal Mode blocks enterprise data sources. Switch to Work Mode to use organisation knowledge."}
                </p>
              </div>
            </div>
            <Switch
              checked={isWorkMode && hasRag && useRag}
              onCheckedChange={setUseRag}
              disabled={!hasRag || !isWorkMode}
            />
          </div>

          {/* Connected sources list */}
          {isWorkMode && hasRag && useRag && (
            <div className="mt-4 pt-3 border-t space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground mb-2">Connected sources</p>
              {[
                { label: 'SharePoint', detail: '3 sites · armely.sharepoint.com', icon: <Share2 className="h-3.5 w-3.5" /> },
                { label: 'Organisation Website', detail: 'armely.com', icon: <Globe className="h-3.5 w-3.5" /> },
              ].map((src) => (
                <div key={src.label} className="flex items-center gap-2 text-xs text-muted-foreground">
                  <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
                  <span className="text-foreground font-medium">{src.label}</span>
                  <span>·</span>
                  <span>{src.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Section: Public Web */}
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          Public Web Search
        </h3>

        <div className={`rounded-lg border bg-card p-4 ${!hasWebSearch ? 'opacity-50' : ''}`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-md bg-green-50 dark:bg-green-950 text-green-600">
                <Globe className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-medium">Live Web Search</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  When enabled, the AI searches the public web in real-time and includes
                  clickable source references in its answers.
                </p>
              </div>
            </div>
            <Switch
              checked={hasWebSearch && useWebSearch}
              onCheckedChange={setUseWebSearch}
              disabled={!hasWebSearch}
            />
          </div>

          {hasWebSearch && useWebSearch && (
            <div className="mt-4 pt-3 border-t">
              <p className="text-xs text-muted-foreground">
                Searches are performed via DuckDuckGo. No search history is stored.
                Results appear as cited references in the AI response.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Info note */}
      <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
        <strong>Note:</strong> These settings apply to the current session.
        Enterprise data is indexed from SharePoint and your organisation's website.
        Citations with source links appear below AI responses when data is used.
      </div>
    </div>
  );
}
