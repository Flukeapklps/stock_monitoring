"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { changeClass, fmtDateTime, fmtNum, fmtPct, labelize } from "@/lib/format";
import type { Signal, StockRow } from "@/lib/types";
import { DataStatusBadge, ScorePill, SignalBadge } from "@/components/badges";
import { PageHeader, useMe } from "@/components/app-shell";
import { StockTable } from "@/components/stock-table";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, ErrorBox, Skeleton } from "@/components/ui";

interface Overview {
  market: { status: string; latest_completed_session: string; session_close?: string; early_close?: boolean; reason?: string };
  items: (StockRow & { display_name: string; note?: string })[];
}

interface SystemStatus {
  last_successful_update: string | null;
  latest_completed_session: string;
  freshness: Record<string, number>;
  providers: { provider: string; label: string; configured: boolean; is_healthy: boolean | null; last_success_at: string | null; last_error: string | null }[];
}

export default function Dashboard() {
  const me = useMe();
  const tz = me.data?.timezone;
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => get<Overview>("/market/overview") });
  const watchlists = useQuery({ queryKey: ["watchlists"], queryFn: () => get<{ id: number; name: string }[]>("/watchlists") });
  const wlId = watchlists.data?.[0]?.id;
  const wl = useQuery({ queryKey: ["watchlist", wlId], queryFn: () => get<{ rows: StockRow[] }>(`/watchlists/${wlId}`), enabled: !!wlId });
  const signals = useQuery({ queryKey: ["signals", "recent"], queryFn: () => get<Signal[]>("/signals?primary_only=true&limit=12") });
  const status = useQuery({ queryKey: ["system-status"], queryFn: () => get<SystemStatus>("/system/status") });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Market Dashboard"
        description="End-of-day US market overview, watchlist and long-term signals."
        actions={
          overview.data && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={overview.data.market.status === "OPEN" ? "bull" : "default"}>Market {labelize(overview.data.market.status)}</Badge>
              <span>Latest completed session: {overview.data.market.latest_completed_session}</span>
            </div>
          )
        }
      />

      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted-foreground">Market overview</h2>
        <ErrorBox error={overview.error} />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7" data-testid="market-overview">
          {overview.isLoading && Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
          {overview.data?.items.map((it) => (
            <Link key={it.symbol} href={`/stocks/${encodeURIComponent(it.symbol)}`}>
              <Card className="h-full p-3 transition-colors hover:border-primary/50">
                <div className="truncate text-xs text-muted-foreground" title={it.display_name}>
                  {it.display_name}
                </div>
                <div className="num mt-1 text-lg font-semibold">{fmtNum(it.price)}</div>
                <div className={`num text-sm ${changeClass(it.change_pct)}`}>{fmtPct(it.change_pct)}</div>
                <div className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground">
                  <DataStatusBadge status={it.data_status} />
                  <span className="truncate">{it.trade_date ?? it.note}</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Watchlist</CardTitle>
            <CardDescription>
              <Link href="/watchlist" className="hover:text-primary">
                Manage watchlists →
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ErrorBox error={wl.error} />
            {wl.isLoading && <Skeleton className="h-40" />}
            {wl.data && (wl.data.rows.length ? <StockTable rows={wl.data.rows} compact /> : <Empty>Your watchlist is empty. Add stocks on the Watchlist page.</Empty>)}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Latest signals</CardTitle>
              <CardDescription>
                <Link href="/signals" className="hover:text-primary">
                  Signal Center →
                </Link>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <ErrorBox error={signals.error} />
              {signals.data?.length === 0 && <Empty>No signals yet. Signals are generated after the daily update.</Empty>}
              {signals.data?.map((s) => (
                <Link key={s.id} href={`/stocks/${encodeURIComponent(s.symbol)}`} className="flex items-center justify-between gap-2 rounded-md p-1 hover:bg-accent">
                  <div className="min-w-0">
                    <div className="font-semibold">{s.symbol}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {s.signal_date} · {labelize(s.confirmation_status)}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <ScorePill score={s.score} />
                    <SignalBadge signal={s.signal_type} />
                  </div>
                </Link>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Data health</CardTitle>
              <CardDescription>
                <Link href="/system" className="hover:text-primary">
                  Details →
                </Link>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {status.data && (
                <>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Last successful update</span>
                    <span>{fmtDateTime(status.data.last_successful_update, tz)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Latest completed session</span>
                    <span>{status.data.latest_completed_session}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Fresh / stale / unavailable</span>
                    <span className="num">
                      <span className="text-bull">{status.data.freshness.FRESH}</span> / <span className="text-warn">{status.data.freshness.STALE}</span> /{" "}
                      <span className="text-bear">{status.data.freshness.UNAVAILABLE}</span>
                    </span>
                  </div>
                  {status.data.providers
                    .filter((p) => p.configured)
                    .map((p) => (
                      <div key={p.provider} className="flex justify-between">
                        <span className="text-muted-foreground">{p.label}</span>
                        <Badge variant={p.is_healthy === false ? "bear" : p.is_healthy ? "bull" : "default"}>{p.is_healthy === false ? "Degraded" : p.is_healthy ? "Healthy" : "Unused"}</Badge>
                      </div>
                    ))}
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
