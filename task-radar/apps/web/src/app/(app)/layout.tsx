"use client";
/**
 * App shell: dark navy sidebar + premium topbar + main canvas.
 * Acts as the auth boundary for all authenticated routes — if /api/me
 * fails, we redirect to "/" so the user lands on the marketing page.
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { Topbar } from "@/components/TopBar";
import { useSession } from "@/lib/useSession";
import { Radar } from "lucide-react";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, status } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/");
  }, [status, router]);

  if (status !== "authenticated") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <div className="flex items-center gap-3 text-muted">
          <Radar size={20} className="animate-pulse text-brand" />
          <span className="text-sm">Loading your radar…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex bg-canvas">
      <Sidebar user={user} />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar user={user} />
        <main className="flex-1 px-6 sm:px-10 py-8 max-w-[1400px] w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
