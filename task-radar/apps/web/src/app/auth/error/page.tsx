"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import { microsoftLoginUrl } from "@/lib/api";

function ErrorContent() {
  const params = useSearchParams();
  const reasonRaw = params.get("reason") || "Sign-in could not be completed.";
  const reason = decodeURIComponent(reasonRaw);

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas px-6 py-16">
      <div className="w-full max-w-xl card p-10 shadow-card">
        <div className="flex items-center gap-3 mb-6">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-danger/10 text-danger">
            <AlertTriangle className="h-6 w-6" />
          </span>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-danger">
              Sign-in error
            </p>
            <h1 className="text-2xl font-semibold text-ink">
              We couldn&apos;t sign you in
            </h1>
          </div>
        </div>

        <p className="text-sm text-muted leading-relaxed mb-2">
          Microsoft returned the following message:
        </p>
        <pre className="text-xs text-ink bg-canvas border border-hairline rounded-xl p-4 mb-6 whitespace-pre-wrap break-words font-mono">
          {reason}
        </pre>

        <div className="text-sm text-muted leading-relaxed mb-6 space-y-2">
          <p className="font-medium text-ink">Common fixes:</p>
          <ul className="list-disc list-inside space-y-1">
            <li>
              Confirm the redirect URI{" "}
              <code className="text-xs bg-canvas px-1.5 py-0.5 rounded">
                http://localhost:8012/api/auth/microsoft/callback
              </code>{" "}
              is registered in your Azure App Registration.
            </li>
            <li>
              If your app is a <strong>public client</strong>, set{" "}
              <code className="text-xs bg-canvas px-1.5 py-0.5 rounded">
                AZURE_PUBLIC_CLIENT=true
              </code>{" "}
              in <code>.env.local</code> and restart the API.
            </li>
            <li>
              Make sure the requested Graph scopes are granted by an admin.
            </li>
          </ul>
        </div>

        <div className="flex flex-wrap gap-3">
          <a
            href={microsoftLoginUrl()}
            className="btn-primary inline-flex items-center gap-2"
          >
            <RefreshCw className="h-4 w-4" />
            Try sign-in again
          </a>
          <Link href="/" className="btn-ghost inline-flex items-center gap-2">
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}

export default function AuthErrorPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <ErrorContent />
    </Suspense>
  );
}
