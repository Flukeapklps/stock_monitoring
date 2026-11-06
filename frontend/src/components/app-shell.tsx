"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Activity, BarChart3, Bell, Briefcase, FlaskConical, Gauge, LineChart, ListFilter, LogOut, Menu, MessageCircle, Search, Server, X } from "lucide-react";
import { get, post } from "@/lib/api";
import type { User } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui";

const NAV = [
  { href: "/", label: "Dashboard", icon: Gauge },
  { href: "/watchlist", label: "Watchlist", icon: LineChart },
  { href: "/signals", label: "Signal Center", icon: Activity },
  { href: "/screener", label: "Screener", icon: ListFilter },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/backtest", label: "Backtest", icon: FlaskConical },
  { href: "/notifications", label: "Notifications", icon: Bell },
  { href: "/settings/line", label: "LINE Integration", icon: MessageCircle },
  { href: "/system", label: "Data Health", icon: Server },
];

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => get<User>("/auth/me"), staleTime: 5 * 60_000 });
}

function SymbolSearch({ onDone }: { onDone?: () => void }) {
  const [q, setQ] = useState("");
  const router = useRouter();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const s = q.trim().toUpperCase();
        if (s) {
          router.push(`/stocks/${encodeURIComponent(s)}`);
          setQ("");
          onDone?.();
        }
      }}
      className="relative"
    >
      <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
      <Input aria-label="Search symbol" placeholder="Symbol (e.g. AAPL)" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
    </form>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const me = useMe();
  if (pathname.startsWith("/login")) return <>{children}</>;

  const nav = (
    <nav className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={() => setOpen(false)}
            className={cn("flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground", active && "bg-accent text-foreground")}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen md:grid md:grid-cols-[220px_1fr]">
      <aside className="hidden border-r bg-card/40 p-3 md:flex md:flex-col md:gap-4">
        <Link href="/" className="flex items-center gap-2 px-2 pt-1 font-semibold">
          <BarChart3 className="h-5 w-5 text-primary" /> Stock Monitor
        </Link>
        <SymbolSearch />
        {nav}
        <div className="mt-auto space-y-2 px-2 text-xs text-muted-foreground">
          <div className="truncate" title={me.data?.email}>
            {me.data?.email} {me.data?.role === "admin" && <span className="text-primary">(admin)</span>}
          </div>
          <button
            className="flex items-center gap-1 hover:text-foreground"
            onClick={async () => {
              await post("/auth/logout");
              window.location.href = "/login";
            }}
          >
            <LogOut className="h-3 w-3" /> Sign out
          </button>
          <p className="leading-snug">Research tool. End-of-day data. Not investment advice; never executes trades.</p>
        </div>
      </aside>

      <header className="sticky top-0 z-30 flex items-center justify-between border-b bg-background/95 px-4 py-2 backdrop-blur md:hidden">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <BarChart3 className="h-5 w-5 text-primary" /> Stock Monitor
        </Link>
        <button aria-label="Open menu" onClick={() => setOpen(!open)} className="rounded p-1 hover:bg-accent">
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </header>
      {open && (
        <div className="space-y-3 border-b bg-card p-3 md:hidden" data-testid="mobile-nav">
          <SymbolSearch onDone={() => setOpen(false)} />
          {nav}
        </div>
      )}

      <main className="min-w-0 p-4 md:p-6">{children}</main>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
