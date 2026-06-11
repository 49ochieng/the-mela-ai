"use client";
/**
 * Landing / sign-in page. Premium marketing-style layout.
 * - No app shell (sits outside (app) route group)
 * - "Sign in with Microsoft" hits the backend OAuth start endpoint;
 *   the backend handles PKCE and sets an httpOnly session cookie.
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  Radar, Mail, MessageSquare, FileSpreadsheet, ListTodo, Sparkles,
  ShieldCheck, Lock, Server, Eye, ArrowRight,
} from "lucide-react";
import { microsoftLoginUrl } from "@/lib/api";
import { useSession } from "@/lib/useSession";

export default function Landing() {
  const router = useRouter();
  const { status } = useSession();
  useEffect(() => {
    if (status === "authenticated") router.replace("/dashboard");
  }, [status, router]);

  return (
    <div className="min-h-screen bg-navy-gradient text-white relative overflow-hidden">
      {/* glow */}
      <div className="absolute inset-0 bg-hero-glow pointer-events-none" />

      {/* Top nav */}
      <header className="relative z-10 max-w-7xl mx-auto flex items-center justify-between px-6 sm:px-10 py-5">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-brand-gradient flex items-center justify-center shadow-soft">
            <Radar size={18} />
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">Mela Task Radar</div>
            <div className="text-[11px] text-white/50">by Mela</div>
          </div>
        </div>
        <a
          href={microsoftLoginUrl()}
          className="text-sm text-white/80 hover:text-white inline-flex items-center gap-1.5"
        >
          Sign in <ArrowRight size={14} />
        </a>
      </header>

      {/* Hero */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 sm:px-10 pt-12 pb-20 grid lg:grid-cols-2 gap-12 items-center">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full bg-white/10 border border-white/15 px-3 py-1 text-xs text-white/80 mb-6">
            <Sparkles size={12} className="text-brand-bright" />
            AI-powered task discovery for Microsoft 365
          </div>
          <h1 className="text-4xl sm:text-5xl lg:text-display-2xl font-semibold tracking-tight leading-[1.05]">
            Find the work hiding in your inbox.
          </h1>
          <p className="mt-5 text-lg text-white/70 max-w-xl leading-relaxed">
            Mela Task Radar scans Outlook and Teams, extracts action items with
            AI, and quietly syncs them to Excel and Planner — so nothing
            important falls off your radar.
          </p>

          <div className="mt-8 flex flex-col sm:flex-row gap-3">
            <a
              href={microsoftLoginUrl()}
              className="inline-flex items-center justify-center gap-2.5 rounded-xl
                         bg-white text-navy-deep font-medium px-5 py-3.5 text-sm
                         hover:bg-white/95 shadow-lift transition"
            >
              <MicrosoftLogo />
              Sign in with Microsoft
            </a>
            <a
              href="#how-it-works"
              className="inline-flex items-center justify-center gap-2 rounded-xl
                         border border-white/20 px-5 py-3.5 text-sm text-white/85
                         hover:bg-white/5 transition"
            >
              See how it works
            </a>
          </div>

          <div className="mt-6 text-xs text-white/45 flex items-center gap-2">
            <Lock size={12} /> Read-only by default · Tenant-isolated · Your data stays in your Microsoft 365.
          </div>
        </div>

        {/* Preview card */}
        <div className="relative">
          <div className="absolute -inset-4 bg-brand/10 blur-2xl rounded-3xl" />
          <div className="relative rounded-3xl bg-white text-ink shadow-lift border border-white/10 overflow-hidden">
            <div className="px-5 py-3 border-b border-hairline flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Radar size={14} className="text-brand" />
                <span className="text-xs font-semibold">Today on your radar</span>
              </div>
              <span className="text-[11px] text-muted">Updated just now</span>
            </div>
            <div className="grid grid-cols-3 gap-px bg-hairline">
              <Stat label="Open tasks" value="8" tone="text-brand" />
              <Stat label="Due today"  value="3" tone="text-warning" />
              <Stat label="Overdue"    value="2" tone="text-danger" />
            </div>
            <div className="p-5 space-y-3">
              <PreviewTask
                title="Send updated SOW to Contoso"
                source="Outlook"
                priority="High"
                meta="From: alex@contoso.com · 2h ago"
              />
              <PreviewTask
                title="Approve Q2 marketing budget"
                source="Teams"
                priority="Medium"
                meta="Channel: #leadership · Yesterday"
              />
              <PreviewTask
                title="Reply to onboarding questions"
                source="Outlook"
                priority="Low"
                meta="From: sam@northwind.io · Yesterday"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="how-it-works" className="relative z-10 bg-canvas text-ink">
        <div className="max-w-7xl mx-auto px-6 sm:px-10 py-20">
          <div className="max-w-2xl">
            <div className="text-xs uppercase tracking-wider text-brand font-medium mb-2">How it works</div>
            <h2 className="text-3xl sm:text-display-lg font-semibold tracking-tight">
              Five quiet integrations, one calm radar.
            </h2>
            <p className="mt-3 text-muted">
              Connect once. Mela Task Radar handles the rest in the background — extracting,
              prioritizing, and syncing tasks across the tools you already use.
            </p>
          </div>

          <div className="mt-12 grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            <Feature icon={<Mail size={18} />} title="Outlook scanning"
              body="Scans recent mail with delegated, read-only Graph access. Folder filters, exclusions, and lookback windows are all in your control." />
            <Feature icon={<MessageSquare size={18} />} title="Teams channel scanning"
              body="Pick the channels that matter. Mela Task Radar reads recent messages and surfaces commitments, requests, and decisions." />
            <Feature icon={<Sparkles size={18} />} title="AI extraction"
              body="Azure OpenAI extracts each task: title, owner, due date, priority, and confidence. Every task is auditable back to its source." />
            <Feature icon={<FileSpreadsheet size={18} />} title="Excel sync"
              body="Append or upsert tasks into a workbook of your choice. Perfect for weekly reviews, status reports, and team standups." />
            <Feature icon={<ListTodo size={18} />} title="Planner sync"
              body="Push high-confidence tasks into a Planner plan and bucket. Keep collaborators aligned without leaving Microsoft 365." />
            <Feature icon={<Server size={18} />} title="MCP for Mela AI"
              body="A first-class Model Context Protocol server lets your Mela AI assistant scan, search, update, and sync — securely, by API key." />
          </div>
        </div>
      </section>

      {/* Trust */}
      <section className="relative z-10 bg-surface text-ink border-t border-hairline">
        <div className="max-w-7xl mx-auto px-6 sm:px-10 py-16 grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
          <Trust icon={<ShieldCheck size={18} />} title="Tenant-isolated"
            body="Each customer's data is strictly scoped. There is no cross-tenant access, ever." />
          <Trust icon={<Lock size={18} />} title="Least-privilege"
            body="Delegated, read-only by default. Write access (Excel, Planner) is opt-in per integration." />
          <Trust icon={<Eye size={18} />} title="Auditable"
            body="Every extracted task links back to the exact email or message it came from." />
          <Trust icon={<Server size={18} />} title="Your Microsoft 365"
            body="We never copy your mailbox. All scanning happens against your tenant via Microsoft Graph." />
        </div>
        <div className="border-t border-hairline">
          <div className="max-w-7xl mx-auto px-6 sm:px-10 py-6 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-muted">
            <div>© {new Date().getFullYear()} Mela. Mela Task Radar.</div>
            <div className="flex items-center gap-2">
              <Radar size={12} className="text-brand" /> Find what fell off your radar.
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

/* ── helpers (local) ───────────────────────────────────────────────── */

function MicrosoftLogo() {
  return (
    <span className="inline-grid grid-cols-2 gap-[2px] w-4 h-4">
      <span className="bg-[#F25022]" />
      <span className="bg-[#7FBA00]" />
      <span className="bg-[#00A4EF]" />
      <span className="bg-[#FFB900]" />
    </span>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="bg-surface px-4 py-4 text-center">
      <div className={`text-2xl font-semibold tabular-nums ${tone}`}>{value}</div>
      <div className="text-[11px] text-muted mt-0.5">{label}</div>
    </div>
  );
}

function PreviewTask({ title, source, priority, meta }:
  { title: string; source: string; priority: "High" | "Medium" | "Low"; meta: string }) {
  const pTone = priority === "High" ? "bg-red-50 text-red-700"
    : priority === "Medium" ? "bg-amber-50 text-amber-700"
    : "bg-emerald-50 text-emerald-700";
  return (
    <div className="flex items-start gap-3 p-3 rounded-xl border border-hairline hover:border-brand/30 transition-colors">
      <div className="mt-0.5 w-2 h-2 rounded-full bg-brand" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink truncate">{title}</div>
        <div className="text-xs text-muted mt-0.5">{meta}</div>
      </div>
      <div className="flex flex-col items-end gap-1 shrink-0">
        <span className={`badge ${pTone}`}>{priority}</span>
        <span className="text-[10px] text-muted">{source}</span>
      </div>
    </div>
  );
}

function Feature({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="card p-6 hover:shadow-card transition-shadow">
      <div className="w-10 h-10 rounded-xl bg-brand/10 text-brand flex items-center justify-center mb-4">
        {icon}
      </div>
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      <p className="text-sm text-muted mt-2 leading-relaxed">{body}</p>
    </div>
  );
}

function Trust({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div>
      <div className="w-9 h-9 rounded-xl bg-brand/10 text-brand flex items-center justify-center mb-3">
        {icon}
      </div>
      <h4 className="text-sm font-semibold tracking-tight">{title}</h4>
      <p className="text-xs text-muted mt-1.5 leading-relaxed">{body}</p>
    </div>
  );
}

