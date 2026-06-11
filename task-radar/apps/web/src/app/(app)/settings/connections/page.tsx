"use client";
import useSWR from "swr";
import { api, fetcher, microsoftLoginUrl } from "@/lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, StatusDot, EmptyState } from "@/components/ui";
import { Plug, RefreshCw, LogOut } from "lucide-react";

export default function ConnectionsPage() {
  const { data, mutate } = useSWR("/api/connections", fetcher);
  const conn = data?.items?.find((c: any) => c.provider === "microsoft");
  const connected = conn?.status === "connected";

  const disconnect = async () => {
    await api("/api/connections/microsoft/disconnect", { method: "POST" });
    mutate();
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        eyebrow="Settings"
        title="Connections"
        description="Mela Task Radar connects to Microsoft 365 with delegated, read-only Graph access. You can reconnect or disconnect any time."
      />

      <Card>
        <CardHeader
          title="Microsoft 365"
          subtitle="Outlook, Teams, Excel, and Planner all use this single connection."
          action={
            <Badge tone={connected ? "success" : "neutral"}>
              <StatusDot tone={connected ? "success" : "neutral"} />
              {connected ? "Connected" : "Not connected"}
            </Badge>
          }
        />

        {conn ? (
          <div className="space-y-5">
            {conn.scopes && (
              <div>
                <div className="text-xs font-medium uppercase tracking-wider text-muted mb-2">
                  Granted scopes
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {conn.scopes.map((s: string) => (
                    <span key={s} className="text-[11px] font-mono px-2 py-0.5 rounded-md bg-canvas border border-hairline text-muted">
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-hairline">
              <a className="btn-ghost text-sm" href={microsoftLoginUrl()}>
                <RefreshCw size={14} /> Reconnect
              </a>
              {connected && (
                <Button variant="danger" size="sm" leftIcon={<LogOut size={14} />} onClick={disconnect}>
                  Disconnect
                </Button>
              )}
            </div>
          </div>
        ) : (
          <EmptyState
            icon={<Plug size={20} />}
            title="No Microsoft 365 account connected"
            description="Connect with your work or school account to start scanning Outlook and Teams."
            action={
              <a className="btn-primary text-sm" href={microsoftLoginUrl()}>
                Connect Microsoft 365
              </a>
            }
          />
        )}
      </Card>
    </div>
  );
}

