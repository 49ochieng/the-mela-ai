"use client";
import { useState } from "react";
import { usePathname } from "next/navigation";
import { Search, Radar, ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/useSession";

const titles: Record<string, string> = {
  "/dashboard":            "Dashboard",
  "/tasks":                "Tasks",
  "/scans":                "Scans",
  "/settings/connections": "Connections",
  "/settings/scan":        "Scan settings",
  "/settings/excel":       "Excel sync",
  "/settings/planner":     "Planner sync",
  "/settings/teams":       "Teams",
  "/settings/mcp":         "Mela connection",
  "/settings/admin":       "Admin · Microsoft credentials",
};

function pageTitle(path: string): string {
  if (path?.startsWith("/tasks/")) return "Task detail";
  return titles[path] || "Mela Task Radar";
}

export function Topbar({ user }: { user: Me | null }) {
  const path = usePathname() || "/";
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);

  async function runScan() {
    setScanning(true);
    setScanMsg(null);
    try {
      await api("/api/scans/run", { method: "POST", body: JSON.stringify({ source: "all" }) });
      setScanMsg("Scan started");
      setTimeout(() => setScanMsg(null), 3000);
    } catch (e: any) {
      setScanMsg(e?.message || "Scan failed to start");
      setTimeout(() => setScanMsg(null), 4000);
    } finally {
      setScanning(false);
    }
  }

  return (
    <header className="h-16 bg-surface border-b border-hairline px-6 flex items-center justify-between gap-4 sticky top-0 z-20">
      <div className="flex items-center gap-3 min-w-0">
        <h2 className="text-[15px] font-semibold text-ink truncate">{pageTitle(path)}</h2>
      </div>

      <div className="flex-1 max-w-md hidden lg:block">
        <div className="relative">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-subtle" />
          <input
            placeholder="Search tasks, senders, threads…"
            className="input pl-9 py-2 text-sm bg-canvas border-canvas focus:bg-surface"
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        {scanMsg && (
          <span className="text-xs text-muted hidden sm:inline">{scanMsg}</span>
        )}
        <button
          onClick={runScan}
          disabled={scanning}
          className="btn-primary text-sm"
        >
          <Radar size={14} className={scanning ? "animate-pulse" : ""} />
          {scanning ? "Scanning…" : "Run scan"}
        </button>
        <div className="hidden md:flex items-center gap-2 px-2.5 py-1.5 rounded-xl border border-hairline">
          <div className="w-7 h-7 rounded-full bg-brand-gradient text-white text-xs font-semibold flex items-center justify-center">
            {(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}
          </div>
          <span className="text-xs text-ink font-medium max-w-[140px] truncate">
            {user?.display_name || "—"}
          </span>
          <ChevronDown size={14} className="text-subtle" />
        </div>
      </div>
    </header>
  );
}

// Back-compat default
export { Topbar as TopBar };

