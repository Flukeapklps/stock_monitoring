"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "@/lib/api";
import { fmtDateTime, labelize } from "@/lib/format";
import { PageHeader, useMe } from "@/components/app-shell";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, ErrorBox, Skeleton, TBody, TD, TH, THead, TR, Table } from "@/components/ui";

interface Provider {
  provider: string;
  label: string;
  configured: boolean;
  capabilities: string[];
  is_healthy: boolean | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  requests_today: number;
  daily_quota: number | null;
}
interface Status {
  market: { status: string; session_open?: string; session_close?: string; early_close?: boolean; reason?: string };
  latest_completed_session: string;
  last_successful_update: string | null;
  providers: Provider[];
  freshness: Record<string, number>;
  stale_symbols: { symbol: string; status: string; latest_bar: string | null; sessions_behind: number | null }[];
  tracked_symbols: number;
  jobs: Record<string, { status: string; at: string; details: Record<string, unknown> }>;
  scheduler_enabled: boolean;
}
interface DQ {
  id: number;
  symbol: string | null;
  check_type: string;
  severity: string;
  trade_date: string | null;
  message: string;
  provider: string | null;
  created_at: string;
}

const JOBS = ["daily_update", "fundamentals_update", "daily_report", "weekly_report", "monthly_report", "dispatch_notifications"];

export default function SystemPage() {
  const me = useMe();
  const tz = me.data?.timezone;
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["system-status"], queryFn: () => get<Status>("/system/status"), refetchInterval: 60_000 });
  const dq = useQuery({ queryKey: ["dq"], queryFn: () => get<DQ[]>("/system/data-quality?limit=200") });
  const runJob = useMutation({ mutationFn: (job: string) => post<{ status: string }>(`/system/jobs/${job}/run`), onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["system-status"] }), 3000) });
  const s = status.data;

  return (
    <div className="space-y-4">
      <PageHeader title="Data Health" description="Provider health, data freshness against the NYSE calendar, data-quality checks and scheduled jobs." />
      <ErrorBox error={status.error} />
      {status.isLoading && <Skeleton className="h-48" />}
      {s && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Card className="p-3">
              <div className="text-xs text-muted-foreground">Market</div>
              <div className="mt-1 font-semibold">{labelize(s.market.status)}</div>
              <div className="text-xs text-muted-foreground">{s.market.reason ?? (s.market.early_close ? "Early close today" : "")}</div>
            </Card>
            <Card className="p-3">
              <div className="text-xs text-muted-foreground">Latest completed session</div>
              <div className="mt-1 font-semibold">{s.latest_completed_session}</div>
            </Card>
            <Card className="p-3">
              <div className="text-xs text-muted-foreground">Last successful update</div>
              <div className="mt-1 font-semibold">{fmtDateTime(s.last_successful_update, tz)}</div>
            </Card>
            <Card className="p-3">
              <div className="text-xs text-muted-foreground">Freshness ({s.tracked_symbols} tracked)</div>
              <div className="num mt-1 font-semibold">
                <span className="text-bull">{s.freshness.FRESH} fresh</span> · <span className="text-warn">{s.freshness.STALE} stale</span> · <span className="text-bear">{s.freshness.UNAVAILABLE} n/a</span>
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Providers</CardTitle>
              <CardDescription>Yahoo Finance is best-effort (unofficial). Alpha Vantage free tier: 25 requests/day, no real-time US quotes.</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <THead>
                  <TR>
                    <TH>Provider</TH>
                    <TH>Configured</TH>
                    <TH>Health</TH>
                    <TH>Capabilities</TH>
                    <TH className="text-right">Requests today</TH>
                    <TH>Last success</TH>
                    <TH>Last error</TH>
                  </TR>
                </THead>
                <TBody>
                  {s.providers.map((p) => (
                    <TR key={p.provider}>
                      <TD className="font-semibold">{p.label}</TD>
                      <TD>{p.configured ? <Badge variant="bull">yes</Badge> : <Badge>no</Badge>}</TD>
                      <TD>{p.is_healthy === null ? <Badge>unused</Badge> : p.is_healthy ? <Badge variant="bull">healthy</Badge> : <Badge variant="bear">degraded ({p.consecutive_failures})</Badge>}</TD>
                      <TD className="whitespace-normal text-xs text-muted-foreground">{p.capabilities.join(", ")}</TD>
                      <TD className="text-right">
                        {p.requests_today}
                        {p.daily_quota ? ` / ${p.daily_quota}` : ""}
                      </TD>
                      <TD className="text-xs">{fmtDateTime(p.last_success_at, tz)}</TD>
                      <TD className="max-w-[300px] truncate text-xs text-muted-foreground" title={p.last_error ?? ""}>
                        {p.last_error ?? "—"}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Scheduled jobs</CardTitle>
                <CardDescription>Scheduler {s.scheduler_enabled ? "enabled" : "disabled"} · NYSE-calendar aware (America/New_York)</CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <THead>
                    <TR>
                      <TH>Job</TH>
                      <TH>Last run</TH>
                      <TH>Status</TH>
                      <TH />
                    </TR>
                  </THead>
                  <TBody>
                    {JOBS.map((j) => (
                      <TR key={j}>
                        <TD>{labelize(j)}</TD>
                        <TD className="text-xs">{fmtDateTime(s.jobs[j]?.at, tz)}</TD>
                        <TD>{s.jobs[j] ? <Badge variant={s.jobs[j].status === "OK" ? "bull" : "bear"}>{s.jobs[j].status}</Badge> : "—"}</TD>
                        <TD>
                          {me.data?.role === "admin" && (
                            <Button size="sm" variant="outline" onClick={() => runJob.mutate(j)} disabled={runJob.isPending}>
                              Run now
                            </Button>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
                <ErrorBox error={runJob.error} />
                {runJob.data && <p className="mt-2 text-xs text-muted-foreground">Job {runJob.data.status}.</p>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Stale / unavailable symbols</CardTitle>
                <CardDescription>Signals are not generated for stale data.</CardDescription>
              </CardHeader>
              <CardContent>
                {s.stale_symbols.length === 0 ? (
                  <Empty>All symbols are up to date.</Empty>
                ) : (
                  <Table>
                    <THead>
                      <TR>
                        <TH>Symbol</TH>
                        <TH>Status</TH>
                        <TH>Latest bar</TH>
                        <TH className="text-right">Behind</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {s.stale_symbols.map((x) => (
                        <TR key={x.symbol}>
                          <TD className="font-semibold">{x.symbol}</TD>
                          <TD>
                            <Badge variant="warn">{x.status}</Badge>
                          </TD>
                          <TD>{x.latest_bar ?? "—"}</TD>
                          <TD className="text-right">{x.sessions_behind ?? "—"}</TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Data quality log</CardTitle>
          <CardDescription>Duplicates, invalid OHLC, missing sessions, abnormal moves, zero volume, provider failures.</CardDescription>
        </CardHeader>
        <CardContent>
          <ErrorBox error={dq.error} />
          {dq.data?.length === 0 && <Empty>No data-quality issues recorded.</Empty>}
          {dq.data && dq.data.length > 0 && (
            <Table>
              <THead>
                <TR>
                  <TH>Time</TH>
                  <TH>Symbol</TH>
                  <TH>Check</TH>
                  <TH>Severity</TH>
                  <TH>Session</TH>
                  <TH>Message</TH>
                  <TH>Provider</TH>
                </TR>
              </THead>
              <TBody>
                {dq.data.map((r) => (
                  <TR key={r.id}>
                    <TD className="text-xs">{fmtDateTime(r.created_at, tz)}</TD>
                    <TD className="font-semibold">{r.symbol ?? "—"}</TD>
                    <TD className="text-xs">{labelize(r.check_type)}</TD>
                    <TD>
                      <Badge variant={r.severity === "ERROR" ? "bear" : r.severity === "WARNING" ? "warn" : "default"}>{r.severity}</Badge>
                    </TD>
                    <TD className="text-xs">{r.trade_date ?? "—"}</TD>
                    <TD className="whitespace-normal text-xs text-muted-foreground">{r.message}</TD>
                    <TD className="text-xs">{r.provider ?? "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
