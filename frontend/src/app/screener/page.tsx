"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Plus, Trash2 } from "lucide-react";
import { download, get, post } from "@/lib/api";
import { changeClass, fmtCompact, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import type { StockRow } from "@/lib/types";
import { ScorePill, SignalBadge, TrendBadge } from "@/components/badges";
import { PageHeader } from "@/components/app-shell";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, ErrorBox, Input, Select, TBody, TD, TH, THead, TR, Table } from "@/components/ui";

interface Filter {
  field: string;
  op: string;
  value?: number | string | null;
  value2?: number | string | null;
}
interface Presets {
  presets: Record<string, { label: string; description: string; filters: Filter[] }>;
  fields: Record<string, string>;
  operators: string[];
}
type Row = StockRow & { revenue_growth_yoy?: number | null; free_cash_flow?: number | null; rel_6m?: number | null };

export default function ScreenerPage() {
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => get<Presets>("/screener/presets") });
  const [filters, setFilters] = useState<Filter[]>([]);
  const [benchmark, setBenchmark] = useState("SPY");
  const [sortBy, setSortBy] = useState("score");
  useEffect(() => {
    if (presets.data && filters.length === 0) setFilters(presets.data.presets.long_term_leaders.filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets.data]);
  const req = { filters: filters.map((f) => ({ ...f, value: f.value === "" ? null : f.value, value2: f.value2 === "" ? null : f.value2 })), benchmark, sort_by: sortBy, descending: true, limit: 300 };
  const run = useMutation({ mutationFn: () => post<{ universe_size: number; count: number; results: Row[] }>("/screener/run", req) });
  const exportCsv = useMutation({ mutationFn: () => download("/screener/export", req, `screener-${new Date().toISOString().slice(0, 10)}.csv`) });

  const update = (i: number, patch: Partial<Filter>) => setFilters(filters.map((f, k) => (k === i ? { ...f, ...patch } : f)));
  const noValue = (op: string) => op === "is_true" || op === "is_false";

  return (
    <div className="space-y-4">
      <PageHeader title="Long-Term Stock Screener" description="Screens every tracked symbol in the database. Missing data never passes a numeric filter." />
      <Card>
        <CardHeader>
          <CardTitle>Strategy</CardTitle>
          <div className="flex flex-wrap gap-2 pt-1">
            {presets.data &&
              Object.entries(presets.data.presets).map(([k, p]) => (
                <Button key={k} size="sm" variant="outline" onClick={() => setFilters(p.filters)} title={p.description}>
                  {p.label}
                </Button>
              ))}
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {filters.map((f, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Select value={f.field} onChange={(e) => update(i, { field: e.target.value })} className="w-64" aria-label="Field">
                {presets.data &&
                  Object.entries(presets.data.fields).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
              </Select>
              <Select value={f.op} onChange={(e) => update(i, { op: e.target.value })} className="w-32" aria-label="Operator">
                {presets.data?.operators.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </Select>
              {!noValue(f.op) && <Input className="w-28" value={f.value ?? ""} onChange={(e) => update(i, { value: e.target.value === "" ? "" : Number(e.target.value) })} type="number" step="any" aria-label="Value" />}
              {f.op === "between" && <Input className="w-28" value={f.value2 ?? ""} onChange={(e) => update(i, { value2: e.target.value === "" ? "" : Number(e.target.value) })} type="number" step="any" aria-label="Upper value" />}
              <Button variant="ghost" size="icon" onClick={() => setFilters(filters.filter((_, k) => k !== i))} aria-label="Remove filter">
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={() => setFilters([...filters, { field: "score", op: "gt", value: 60 }])}>
              <Plus className="h-4 w-4" /> Add filter
            </Button>
            <Select value={benchmark} onChange={(e) => setBenchmark(e.target.value)} className="w-28" aria-label="Benchmark">
              {["SPY", "QQQ", "VTI"].map((b) => (
                <option key={b}>{b}</option>
              ))}
            </Select>
            <Select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className="w-40" aria-label="Sort by">
              <option value="score">Rank by score</option>
              <option value="return_1y">Rank by 1Y return</option>
              <option value="rel_6m">Rank by 6M relative</option>
            </Select>
            <Button onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? "Screening…" : "Run screen"}
            </Button>
            <Button variant="secondary" onClick={() => exportCsv.mutate()} disabled={exportCsv.isPending}>
              <Download className="h-4 w-4" /> Export CSV
            </Button>
          </div>
        </CardContent>
      </Card>
      <ErrorBox error={run.error || exportCsv.error} />
      {run.data && (
        <Card>
          <CardHeader>
            <CardTitle>Results</CardTitle>
            <CardDescription>
              {run.data.count} of {run.data.universe_size} tracked symbols passed · relative performance vs {benchmark}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {run.data.results.length === 0 ? (
              <Empty>No stocks match all filters.</Empty>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>#</TH>
                    <TH>Symbol</TH>
                    <TH className="text-right">Price</TH>
                    <TH className="text-right">Score</TH>
                    <TH>Trend</TH>
                    <TH className="text-right">RSI</TH>
                    <TH className="text-right">1Y</TH>
                    <TH className="text-right">6M vs {benchmark}</TH>
                    <TH className="text-right">From 52W high</TH>
                    <TH className="text-right">Rev growth</TH>
                    <TH className="text-right">FCF</TH>
                    <TH>Signal</TH>
                  </TR>
                </THead>
                <TBody>
                  {run.data.results.map((r) => (
                    <TR key={r.symbol}>
                      <TD>{r.rank}</TD>
                      <TD className="font-semibold">
                        <Link href={`/stocks/${encodeURIComponent(r.symbol)}`} className="hover:text-primary">
                          {r.symbol}
                        </Link>
                      </TD>
                      <TD className="text-right">{fmtMoney(r.price)}</TD>
                      <TD className="text-right">
                        <ScorePill score={r.score} coverage={r.score_coverage} />
                      </TD>
                      <TD>
                        <TrendBadge trend={r.trend} />
                      </TD>
                      <TD className="text-right">{fmtNum(r.rsi_14, 1)}</TD>
                      <TD className={`text-right ${changeClass(r.return_1y)}`}>{fmtPct(r.return_1y, 1)}</TD>
                      <TD className={`text-right ${changeClass(r.rel_6m)}`}>{fmtPct(r.rel_6m ?? null, 1)}</TD>
                      <TD className="text-right">{fmtPct(r.dist_52w_high_pct, 1)}</TD>
                      <TD className="text-right">{fmtPct(r.revenue_growth_yoy ?? null, 1)}</TD>
                      <TD className="text-right">{r.free_cash_flow === null || r.free_cash_flow === undefined ? "—" : `$${fmtCompact(r.free_cash_flow)}`}</TD>
                      <TD>
                        <SignalBadge signal={r.latest_signal} />
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
