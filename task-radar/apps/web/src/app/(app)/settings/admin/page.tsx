"use client";
/**
 * Admin → Microsoft credentials.
 *
 * Lets a tenant admin configure the Azure AD App Registration (tenant id,
 * client id, public-client flag) and rotate the client secret. The
 * plaintext secret is **write-only**: the API never returns it, and this
 * page never displays it after submission. Visibility is gated by the
 * authenticated user's `role`.
 */
import { useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { api, fetcher } from "@/lib/api";
import { useSession } from "@/lib/useSession";
import {
  PageHeader, Card, CardHeader, Button, Badge, StatusDot, EmptyState, LoadingState,
} from "@/components/ui";
import { ShieldCheck, Save, KeyRound, Trash2, AlertTriangle, CheckCircle2 } from "lucide-react";

type TenantConfig = {
  azure_tenant_id: string | null;
  azure_client_id: string | null;
  azure_public_client: boolean;
  has_client_secret: boolean;
  last_rotated_at: string | null;
  updated_by_user_id: string | null;
};

type TestResult = {
  tenant_id_set: boolean;
  client_id_set: boolean;
  secret_resolvable: boolean;
  public_client: boolean;
};

export default function AdminTenantPage() {
  const { user, status } = useSession();
  const router = useRouter();
  const isAdmin = user?.role === "admin";

  const { data, mutate, isLoading } = useSWR<TenantConfig>(
    isAdmin ? "/api/admin/tenant-config" : null,
    fetcher,
  );

  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [publicClient, setPublicClient] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);

  // Hydrate inputs once data arrives (only for non-secret fields).
  if (data && tenantId === "" && (data.azure_tenant_id || data.azure_client_id)) {
    if (data.azure_tenant_id) setTenantId(data.azure_tenant_id);
    if (data.azure_client_id) setClientId(data.azure_client_id);
    setPublicClient(!!data.azure_public_client);
  }

  if (status === "loading") return <LoadingState />;
  if (status === "unauthenticated") {
    if (typeof window !== "undefined") router.replace("/");
    return null;
  }
  if (!isAdmin) {
    return (
      <div className="max-w-3xl">
        <PageHeader eyebrow="Admin" title="Microsoft credentials" />
        <EmptyState
          icon={<ShieldCheck size={20} />}
          title="Admin role required"
          description="Only tenant administrators can manage Microsoft credentials."
        />
      </div>
    );
  }

  const onSave = async () => {
    setSaving(true); setError(null); setTest(null);
    try {
      const body: Record<string, unknown> = {
        azure_tenant_id: tenantId || null,
        azure_client_id: clientId || null,
        azure_public_client: publicClient,
      };
      // Only send the secret if the admin actually typed one. An empty
      // string is a sentinel to clear the secret — we never send it
      // implicitly to avoid accidental rotation on every save.
      if (clientSecret) body.azure_client_secret = clientSecret;
      const updated = await api<TenantConfig>("/api/admin/tenant-config", {
        method: "PUT", body: JSON.stringify(body),
      });
      await mutate(updated, { revalidate: false });
      setClientSecret("");
      setSavedAt(new Date().toISOString());
    } catch (e: any) {
      setError(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const onClearSecret = async () => {
    if (!confirm("Clear the saved client secret? Microsoft sign-in will fall back to public-client mode until you save a new one.")) return;
    setError(null); setTest(null);
    try {
      await api("/api/admin/tenant-config/secret", { method: "DELETE" });
      await mutate();
    } catch (e: any) {
      setError(e?.message || "Clear failed");
    }
  };

  const onTest = async () => {
    setError(null); setTest(null);
    try {
      const r = await api<TestResult>("/api/admin/tenant-config/test", { method: "POST" });
      setTest(r);
    } catch (e: any) {
      setError(e?.message || "Test failed");
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <PageHeader
        eyebrow="Admin"
        title="Microsoft credentials"
        description="Configure your tenant's Microsoft Entra App Registration. The client secret is stored in Azure Key Vault — never in the database, the audit log, or this page after you save."
        actions={
          <Badge tone={data?.has_client_secret ? "success" : "neutral"}>
            <StatusDot tone={data?.has_client_secret ? "success" : "neutral"} />
            {data?.has_client_secret ? "Secret stored" : "No secret"}
          </Badge>
        }
      />

      {error && (
        <Card>
          <div className="flex items-center gap-2 text-sm text-red-600">
            <AlertTriangle size={16} /> {error}
          </div>
        </Card>
      )}

      <Card>
        <CardHeader title="App registration" subtitle="Values from your Azure portal → App registrations entry." />
        {isLoading ? <LoadingState /> : (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium uppercase tracking-wider text-muted mb-1.5">
                Directory (tenant) ID
              </label>
              <input
                type="text" autoComplete="off" spellCheck={false}
                value={tenantId} onChange={(e) => setTenantId(e.target.value.trim())}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="w-full font-mono text-sm px-3 py-2 rounded-md bg-canvas border border-hairline focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium uppercase tracking-wider text-muted mb-1.5">
                Application (client) ID
              </label>
              <input
                type="text" autoComplete="off" spellCheck={false}
                value={clientId} onChange={(e) => setClientId(e.target.value.trim())}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="w-full font-mono text-sm px-3 py-2 rounded-md bg-canvas border border-hairline focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium uppercase tracking-wider text-muted mb-1.5">
                Client secret <span className="ml-2 text-muted normal-case tracking-normal">(write-only — leave blank to keep current)</span>
              </label>
              <input
                type="password" autoComplete="new-password" spellCheck={false}
                value={clientSecret} onChange={(e) => setClientSecret(e.target.value)}
                placeholder={data?.has_client_secret ? "•••••••• (stored in Key Vault)" : "Paste new client secret"}
                className="w-full font-mono text-sm px-3 py-2 rounded-md bg-canvas border border-hairline focus:outline-none focus:border-accent"
              />
              {data?.last_rotated_at && (
                <div className="mt-1 text-xs text-muted">
                  Last rotated {new Date(data.last_rotated_at).toLocaleString()}
                </div>
              )}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox" checked={publicClient}
                onChange={(e) => setPublicClient(e.target.checked)}
              />
              Public client (PKCE only — disable client secret use)
            </label>

            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-hairline">
              <Button onClick={onSave} disabled={saving} leftIcon={<Save size={14} />}>
                {saving ? "Saving…" : "Save"}
              </Button>
              <Button variant="ghost" onClick={onTest} leftIcon={<KeyRound size={14} />}>
                Test
              </Button>
              {data?.has_client_secret && (
                <Button variant="danger" onClick={onClearSecret} leftIcon={<Trash2 size={14} />}>
                  Clear secret
                </Button>
              )}
              {savedAt && (
                <span className="text-xs text-muted ml-auto inline-flex items-center gap-1">
                  <CheckCircle2 size={12} /> Saved {new Date(savedAt).toLocaleTimeString()}
                </span>
              )}
            </div>

            {test && (
              <div className="rounded-md border border-hairline bg-canvas p-3 text-sm">
                <div className="font-medium mb-2">Test result</div>
                <ul className="space-y-1">
                  <li>Tenant ID set: {test.tenant_id_set ? "✓" : "✗"}</li>
                  <li>Client ID set: {test.client_id_set ? "✓" : "✗"}</li>
                  <li>Secret resolvable from Key Vault: {test.secret_resolvable ? "✓" : "✗"}</li>
                  <li>Public client mode: {test.public_client ? "yes" : "no"}</li>
                </ul>
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
