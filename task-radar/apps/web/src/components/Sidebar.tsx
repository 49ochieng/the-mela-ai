"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, ListTodo, Radar, Settings, Plug, FileSpreadsheet,
  Users, Calendar, Zap, LogOut, ShieldCheck,
} from "lucide-react";
import clsx from "clsx";
import { logout } from "@/lib/api";
import type { Me } from "@/lib/useSession";

type NavItem = { href: string; label: string; icon: any };
type NavGroup = { label: string; items: NavItem[] };

const groups: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/tasks",     label: "Tasks",     icon: ListTodo },
      { href: "/scans",     label: "Scans",     icon: Radar },
    ],
  },
  {
    label: "Sources & sync",
    items: [
      { href: "/settings/connections", label: "Connections", icon: Plug },
      { href: "/settings/excel",       label: "Excel",       icon: FileSpreadsheet },
      { href: "/settings/planner",     label: "Planner",     icon: Calendar },
      { href: "/settings/teams",       label: "Teams",       icon: Users },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/settings/scan", label: "Scan settings", icon: Settings },
      { href: "/settings/mcp",  label: "Mela connection", icon: Zap },
    ],
  },
];

const adminItem: NavItem = {
  href: "/settings/admin", label: "Admin · Microsoft credentials", icon: ShieldCheck,
};

export function Sidebar({ user }: { user: Me | null }) {
  const path = usePathname() || "";
  const initials = (user?.display_name || user?.email || "?")
    .split(/\s|@/).filter(Boolean).slice(0, 2)
    .map((p) => p[0]?.toUpperCase()).join("");
  return (
    <aside className="hidden md:flex flex-col w-[280px] shrink-0 bg-navy-gradient text-white">
      {/* Brand */}
      <div className="px-6 pt-6 pb-5">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-brand-gradient flex items-center justify-center shadow-soft">
            <Radar size={18} className="text-white" />
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-semibold tracking-tight">Mela Task Radar</div>
            <div className="text-[11px] text-white/50">by Mela</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 overflow-y-auto">
        {groups.map((g) => (
          <div key={g.label} className="mb-5">
            <div className="px-3 mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-white/40">
              {g.label}
            </div>
            <div className="space-y-0.5">
              {g.items.map(({ href, label, icon: Icon }) => {
                const active = path === href || (href !== "/dashboard" && path.startsWith(href));
                return (
                  <Link
                    key={href}
                    href={href}
                    className={clsx(
                      "group flex items-center gap-2.5 px-3 py-2 rounded-xl text-[13.5px] transition-colors",
                      active
                        ? "bg-white/10 text-white"
                        : "text-white/65 hover:bg-white/5 hover:text-white",
                    )}
                  >
                    <Icon size={16} className={clsx(active ? "text-brand-bright" : "text-white/55 group-hover:text-white/85")} />
                    <span className="font-medium">{label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
        {user?.role === "admin" && (
          <div className="mb-5">
            <div className="px-3 mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-white/40">
              Administration
            </div>
            <div className="space-y-0.5">
              {(() => {
                const { href, label, icon: Icon } = adminItem;
                const active = path === href || path.startsWith(href);
                return (
                  <Link
                    href={href}
                    className={clsx(
                      "group flex items-center gap-2.5 px-3 py-2 rounded-xl text-[13.5px] transition-colors",
                      active ? "bg-white/10 text-white" : "text-white/65 hover:bg-white/5 hover:text-white",
                    )}
                  >
                    <Icon size={16} className={clsx(active ? "text-brand-bright" : "text-white/55 group-hover:text-white/85")} />
                    <span className="font-medium">{label}</span>
                  </Link>
                );
              })()}
            </div>
          </div>
        )}
      </nav>

      {/* User card */}
      <div className="px-3 pb-4">
        <div className="rounded-2xl bg-white/5 border border-white/10 p-3 flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-brand-gradient flex items-center justify-center text-sm font-semibold shrink-0">
            {initials || "?"}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-medium truncate">{user?.display_name || "Signed in"}</div>
            <div className="text-[11px] text-white/55 truncate">{user?.email || ""}</div>
          </div>
          <button
            onClick={() => logout()}
            title="Sign out"
            className="text-white/55 hover:text-white p-1.5 rounded-lg hover:bg-white/10 transition-colors"
          >
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </aside>
  );
}

