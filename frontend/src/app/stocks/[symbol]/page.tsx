"use client";

import { use, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { get, post } from "@/lib/api";
import { changeClass, fmtCompact, fmtMoney, fmtNum, fmtPct, labelize } from "@/lib/format";
import type { Freshness, Score, Signal, StockRow } from "@/lib/types";
import { DataStatusBadge, ScorePill, SignalBadge, TrendBadge } from "@/components/badges";
import { PageHeader, useMe } from "@/components/app-shell";
import { PriceChart, type Marker, type Overlay } from "@/components/charts/price-chart";
import { MacdChart, RsiChart } from "@/components/charts/indicator-charts";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, ErrorBox, Skeleton, TBody, TD, TH, THead, TR, Table, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

type Ind = Record<string, number | boolean | null>;
interface Condition {
  key: string;
  label: string;
  passed: boolean | null;
  detail: string;
}
interface Detail {
  stock: { symbol: string; name: string | null; asset_type: string; exchange: string | null; sector: string | null; industry: string | null; metadata_source: string | null };
  quote: StockRow;
  indicators: Ind | null;
  indicators_date: string | null;
  weekly_indicators: Ind | null;
  support_resistance: { support: { level: number; touches: number }[]; resistance: { level: number; touches: number }[] };
  score: Score | null;
  personal_score: Score | null;
  rules: {
    long_term_trend: string;
    weekly_trend: string | null;
    trend_conditions: Condition[];
    accumulation: boolean;
    accumulation_conditions: Condition[];
    overbought: boolean;
    overbought_conditions: Condition[];
    risk: boolean;
    risk_conditions: Condition[];
  } | null;
  fundamentals: {
    as_of: string | null;
    values: Record<string, { value: number; unit: string | null; period_type: string; period_end: string; is_calculated: boolean; form: string | null; filed_date: string | null; source: string | null }>;
    missing: string[];
    summary: string[];
    valuation: Record<string, { value: number | null; basis?: string | null; note?: string | null }>;
  };
  freshness: Freshness;
}
interface Bars {
  bars: { date: string; open: number; high: number; low: number; close: number; volume: number | null; provider: string; data_status: string }[];
  providers: string[];
}

const TF = [
  { key: "daily", label: "Daily" },
  { key: "weekly", label: "Weekly" },
  { key: "monthly", label: "Monthly" },
];
const OVERLAYS: Record<string, { label: string; color: string; tf: string[] }> = {
  sma_20: { label: "SMA 20", color: "#94a3b8", tf: ["daily"] },
  sma_50: { label: "SMA 50", color: "#38bdf8", tf: ["daily"] },
  sma_200: { label: "SMA 200", color: "#f97316", tf: ["daily"] },
  ema_50: { label: "EMA 50", color: "#a78bfa", tf: ["daily"] },
  ema_200: { label: "EMA 200", color: "#f472b6", tf: ["daily"] },
  sma_10: { label: "SMA 10", color: "#38bdf8", tf: ["weekly", "monthly"] },
  sma_40: { label: "SMA 40", color: "#f97316", tf: ["weekly"] },
  sma_12: { label: "SMA 12", color: "#f97316", tf: ["monthly"] },
};
const METRIC_LABELS: Record<string, string> = {
  revenue: "Revenue",
  revenue_growth_yoy: "Revenue growth YoY",
  gross_margin: "Gross margin",
  operating_margin: "Operating margin",
  net_margin: "Net margin",
  roe: "ROE",
  roa: "ROA",
  total_debt: "Total debt",
  cash: "Cash & equivalents",
  debt_to_equity: "Debt-to-equity",
  current_ratio: "Current ratio",
  interest_coverage: "Interest coverage",
  operating_cash_flow: "Operating cash flow",
  free_cash_flow: "Free cash flow",
  fcf_margin: "FCF margin",
  eps_diluted: "EPS (diluted)",
  eps_growth_yoy: "EPS growth YoY",
  fcf_growth_yoy: "FCF growth YoY",
};

function fmtMetric(v: number, unit: string | null) {
  if (unit === "%") return fmtPct(v, 1, false);
  if (unit === "ratio" || unit === "x") return fmtNum(v, 2);
  if (unit === "USD/shares") return fmtMoney(v);
  return `$${fmtCompact(v)}`;
}

function Conditions({ items }: { items: Condition[] }) {
  return (
    <ul className="space-y-1 text-sm">
      {items.map((c) => (
        <li key={c.key} className="flex items-start gap-2">
          <span className={c.passed === null ? "text-muted-foreground" : c.passed ? "text-bull" : "text-bear"}>{c.passed === null ? "○" : c.passed ? "✔" : "✘"}</span>
          <span>
            {c.label} <span className="text-xs text-muted-foreground">({c.detail})</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function ScoreCard({ score, title }: { score: Score; title: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          {score.score_date && `As of ${score.score_date} · `}coverage {(score.coverage * 100).toFixed(0)}%{score.low_coverage && " — low coverage, use caution"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-3">
          <div className="num text-4xl font-bold">
            <ScorePill score={score.total} />
            <span className="text-base text-muted-foreground">/100</span>
          </div>
          <SignalBadge signal={score.classification} />
        </div>
        <div className="space-y-2">
          {Object.values(score.components).map((c) => (
            <div key={c.key}>
              <div className="flex justify-between text-xs">
                <span>
                  {c.label} <span className="text-muted-foreground">({c.weight.toFixed(0)}%)</span>
                </span>
                <span className="num">{c.score === null ? <span className="text-muted-foreground">no data</span> : c.score.toFixed(0)}</span>
              </div>
              <div className="mt-1 h-1.5 rounded bg-muted">
                <div className="h-1.5 rounded bg-primary" style={{ width: `${c.score ?? 0}%`, opacity: c.score === null ? 0 : 1 }} />
              </div>
            </div>
          ))}
        </div>
        <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {score.explanation.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
        {score.missing.length > 0 && <p className="text-[11px] text-yellow-500/80">Missing: {score.missing.slice(0, 8).join("; ")}{score.missing.length > 8 ? "…" : ""}</p>}
        <p className="text-[11px] text-muted-foreground">{score.disclaimer}</p>
      </CardContent>
    </Card>
  );
}

export default function StockPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol: raw } = use(params);
  const symbol = decodeURIComponent(raw).toUpperCase();
  const me = useMe();
  const qc = useQueryClient();
  const [tf, setTf] = useState("daily");
  const [overlays, setOverlays] = useState<string[]>(["sma_50", "sma_200"]);

  const detail = useQuery({ queryKey: ["stock", symbol], queryFn: () => get<Detail>(`/stocks/${encodeURIComponent(symbol)}`), retry: false });
  const bars = useQuery({ queryKey: ["bars", symbol, tf], queryFn: () => get<Bars>(`/stocks/${encodeURIComponent(symbol)}/bars?timeframe=${tf}`), enabled: detail.isSuccess });
  const ind = useQuery({
    queryKey: ["ind", symbol, tf],
    queryFn: () => get<{ rows: { date: string; [k: string]: number | string | boolean | null }[] }>(`/stocks/${encodeURIComponent(symbol)}/indicators?timeframe=${tf}`),
    enabled: detail.isSuccess,
  });
  const signals = useQuery({ queryKey: ["stock-signals", symbol], queryFn: () => get<Signal[]>(`/stocks/${encodeURIComponent(symbol)}/signals?limit=200`), enabled: detail.isSuccess });
  const track = useMutation({ mutationFn: () => post("/stocks", { symbol }), onSuccess: () => qc.invalidateQueries({ queryKey: ["stock", symbol] }) });
  const refresh = useMutation({ mutationFn: () => post(`/stocks/${encodeURIComponent(symbol)}/refresh`), onSuccess: () => qc.invalidateQueries() });

  const overlayData: Overlay[] = useMemo(() => {
    if (!ind.data) return [];
    return overlays
      .filter((k) => OVERLAYS[k]?.tf.includes(tf))
      .map((k) => ({ key: k, label: OVERLAYS[k].label, color: OVERLAYS[k].color, points: ind.data!.rows.map((r) => ({ date: r.date, value: (r[k] as number | null) ?? null })) }));
  }, [ind.data, overlays, tf]);

  const markers: Marker[] = useMemo(() => {
    if (!signals.data || tf !== "daily") return [];
    const out: Marker[] = [];
    let prev: string | null = null;
    for (const s of [...signals.data].filter((x) => x.is_primary).reverse()) {
      if (s.signal_type !== prev) {
        const bull = ["STRONG_LONG_TERM_TREND", "BULLISH", "ACCUMULATION_WATCH"].includes(s.signal_type);
        const bear = ["BEARISH", "WEAKENING"].includes(s.signal_type);
        out.push({ date: s.signal_date, text: labelize(s.signal_type), color: bull ? "#22c55e" : bear ? "#ef4444" : "#9ca3af", position: bull ? "belowBar" : "aboveBar", shape: bull ? "arrowUp" : bear ? "arrowDown" : "circle" });
      }
      prev = s.signal_type;
    }
    return out.slice(-30);
  }, [signals.data, tf]);

  const levels = useMemo(() => {
    const sr = detail.data?.support_resistance;
    if (!sr || tf !== "daily") return [];
    return [...sr.support.slice(0, 2).map((s) => ({ price: s.level, color: "#22c55e", title: `S ${s.touches}x` })), ...sr.resistance.slice(0, 2).map((s) => ({ price: s.level, color: "#ef4444", title: `R ${s.touches}x` }))];
  }, [detail.data, tf]);

  const subRows = useMemo(() => (ind.data?.rows ?? []).slice(tf === "daily" ? -260 : -156), [ind.data, tf]);

  if (detail.isError) {
    const status = (detail.error as { status?: number }).status;
    return (
      <div className="space-y-4">
        <PageHeader title={symbol} />
        {status === 404 ? (
          <Empty>
            <p className="mb-3">{symbol} is not tracked yet.</p>
            <Button onClick={() => track.mutate()} disabled={track.isPending}>
              Start tracking {symbol}
            </Button>
            <ErrorBox error={track.error} />
            {track.isSuccess && <p className="mt-2">Added. Historical data is loading — refresh in a minute.</p>}
          </Empty>
        ) : (
          <ErrorBox error={detail.error} />
        )}
      </div>
    );
  }
  if (detail.isLoading || !detail.data) return <Skeleton className="h-96" />;
  const d = detail.data;
  const q = d.quote;
  const i = d.indicators || {};

  return (
    <div className="space-y-4">
      <PageHeader
        title={`${d.stock.symbol}${d.stock.name ? ` · ${d.stock.name}` : ""}`}
        description={[d.stock.exchange, d.stock.asset_type, d.stock.sector, d.stock.industry].filter(Boolean).join(" · ")}
        actions={
          me.data?.role === "admin" && (
            <Button variant="outline" size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              <RefreshCw className={`h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`} /> Refresh data
            </Button>
          )
        }
      />
      <ErrorBox error={refresh.error} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <Card className="p-3 lg:col-span-2">
          <div className="flex items-baseline gap-3">
            <span className="num text-3xl font-bold">{d.stock.asset_type === "INDEX" ? fmtNum(q.price) : fmtMoney(q.price)}</span>
            <span className={`num text-lg ${changeClass(q.change_pct)}`}>
              {fmtNum(q.change)} ({fmtPct(q.change_pct)})
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <DataStatusBadge status={q.data_status} />
            <span>Close {q.trade_date ?? "—"}</span>
            <span>· {q.provider_label ?? "—"}</span>
            {d.freshness.status === "STALE" && <Badge variant="warn">{d.freshness.sessions_behind} session(s) behind</Badge>}
          </div>
        </Card>
        {[
          ["52W range", `${fmtMoney(i.low_52w as number)} – ${fmtMoney(i.high_52w as number)}`],
          ["From 52W high", fmtPct(i.dist_52w_high_pct as number, 1)],
          ["1Y return", fmtPct(i.return_1y as number, 1)],
          ["Volatility (1Y)", fmtPct(i.volatility_1y as number, 1, false)],
        ].map(([k, v]) => (
          <Card key={k} className="p-3">
            <div className="text-xs text-muted-foreground">{k}</div>
            <div className="num mt-1 font-semibold">{v}</div>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Tabs value={tf} onValueChange={setTf}>
              <TabsList>
                {TF.map((t) => (
                  <TabsTrigger key={t.key} value={t.key}>
                    {t.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <div className="flex flex-wrap gap-1">
              {Object.entries(OVERLAYS)
                .filter(([, o]) => o.tf.includes(tf))
                .map(([k, o]) => (
                  <button
                    key={k}
                    onClick={() => setOverlays(overlays.includes(k) ? overlays.filter((x) => x !== k) : [...overlays, k])}
                    className={`rounded border px-2 py-0.5 text-[11px] ${overlays.includes(k) ? "border-transparent text-background" : "text-muted-foreground"}`}
                    style={overlays.includes(k) ? { backgroundColor: o.color } : undefined}
                  >
                    {o.label}
                  </button>
                ))}
            </div>
          </div>
          {bars.isLoading ? <Skeleton className="h-[420px]" /> : bars.data?.bars.length ? <PriceChart bars={bars.data.bars} overlays={overlayData} markers={markers} levels={levels} /> : <Empty>No price history stored.</Empty>}
          <p className="text-[11px] text-muted-foreground">
            Source: {bars.data?.providers.join(", ") || "—"} · split-adjusted OHLC (returns use dividend-adjusted closes) · weekly/monthly bars built from daily sessions.
          </p>
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <div className="text-xs text-muted-foreground">RSI (14)</div>
              <RsiChart rows={subRows} />
            </div>
            <div>
              <div className="text-xs text-muted-foreground">MACD (12, 26, 9)</div>
              <MacdChart rows={subRows} />
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          {d.rules && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  Long-term analysis <TrendBadge trend={d.rules.long_term_trend} />
                  {d.rules.accumulation && <SignalBadge signal="ACCUMULATION_WATCH" />}
                  {d.rules.overbought && <SignalBadge signal="OVERBOUGHT" />}
                  {d.rules.risk && <SignalBadge signal="TREND_WEAKENING" />}
                </CardTitle>
                <CardDescription>
                  Indicators as of {d.indicators_date} · weekly trend {d.rules.weekly_trend ?? "n/a"}
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div>
                  <h4 className="mb-1 text-xs font-semibold text-muted-foreground">Trend</h4>
                  <Conditions items={d.rules.trend_conditions} />
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold text-muted-foreground">Accumulation</h4>
                  <Conditions items={d.rules.accumulation_conditions} />
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold text-muted-foreground">Overbought (monitor only — never a sell signal by itself)</h4>
                  <Conditions items={d.rules.overbought_conditions} />
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold text-muted-foreground">Long-term risk</h4>
                  <Conditions items={d.rules.risk_conditions} />
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Key indicators</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
              {[
                ["SMA 20", fmtMoney(i.sma_20 as number)],
                ["SMA 50", fmtMoney(i.sma_50 as number)],
                ["SMA 100", fmtMoney(i.sma_100 as number)],
                ["SMA 200", fmtMoney(i.sma_200 as number)],
                ["EMA 20", fmtMoney(i.ema_20 as number)],
                ["EMA 50", fmtMoney(i.ema_50 as number)],
                ["EMA 200", fmtMoney(i.ema_200 as number)],
                ["RSI (14)", fmtNum(i.rsi_14 as number, 1)],
                ["MACD hist", fmtNum(i.macd_hist as number, 3)],
                ["ADX (14)", fmtNum(i.adx as number, 1)],
                ["ROC (12)", fmtPct(i.roc_12 as number)],
                ["Stoch RSI %K", fmtNum(i.stoch_rsi_k as number, 1)],
                ["ATR (14)", fmtMoney(i.atr_14 as number)],
                ["BB upper", fmtMoney(i.bb_upper as number)],
                ["BB lower", fmtMoney(i.bb_lower as number)],
                ["HV (20d)", fmtPct(i.hv_20 as number, 1, false)],
                ["Supertrend", i.supertrend_dir === 1 ? "Up" : i.supertrend_dir === -1 ? "Down" : "—"],
                ["OBV", fmtCompact(i.obv as number)],
                ["Vol SMA 20", fmtCompact(i.volume_sma_20 as number)],
                ["1M / 3M", `${fmtPct(i.return_1m as number, 1)} / ${fmtPct(i.return_3m as number, 1)}`],
                ["6M / 3Y", `${fmtPct(i.return_6m as number, 1)} / ${fmtPct(i.return_3y as number, 1)}`],
                ["5Y return", fmtPct(i.return_5y as number, 1)],
                ["Max DD (1Y)", fmtPct(i.max_drawdown_1y as number, 1)],
                ["Weekly SMA 40", fmtMoney(d.weekly_indicators?.sma_40 as number)],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-border/50 py-1">
                  <span className="text-muted-foreground">{k}</span>
                  <span className="num">{v}</span>
                </div>
              ))}
              <div className="col-span-full mt-2 text-xs text-muted-foreground">
                Support: {d.support_resistance.support.map((s) => fmtMoney(s.level)).join(", ") || "—"} · Resistance: {d.support_resistance.resistance.map((s) => fmtMoney(s.level)).join(", ") || "—"}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Fundamentals</CardTitle>
              <CardDescription>SEC EDGAR XBRL · reported vs calculated metrics are labelled · as of {d.fundamentals.as_of ?? "n/a"}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {d.fundamentals.summary.length > 0 && (
                <ul className="list-disc space-y-1 pl-4 text-sm">
                  {d.fundamentals.summary.map((s, k) => (
                    <li key={k}>{s}</li>
                  ))}
                </ul>
              )}
              <Table>
                <THead>
                  <TR>
                    <TH>Metric</TH>
                    <TH className="text-right">Value</TH>
                    <TH>Period</TH>
                    <TH>Type</TH>
                  </TR>
                </THead>
                <TBody>
                  {Object.entries(METRIC_LABELS).map(([k, label]) => {
                    const m = d.fundamentals.values[k];
                    return (
                      <TR key={k}>
                        <TD>{label}</TD>
                        <TD className="text-right">{m ? fmtMetric(m.value, m.unit) : <span className="text-muted-foreground">unavailable</span>}</TD>
                        <TD className="text-xs text-muted-foreground">{m ? `${m.period_type} ${m.period_end}` : ""}</TD>
                        <TD className="text-xs text-muted-foreground">{m ? (m.is_calculated ? "calculated" : `reported${m.form ? ` (${m.form})` : ""}`) : ""}</TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
              <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
                {Object.entries(d.fundamentals.valuation).map(([k, v]) => (
                  <div key={k} className="rounded border p-2" title={v.basis || v.note || ""}>
                    <div className="text-xs uppercase text-muted-foreground">{k.replace("_", " ")}</div>
                    <div className="num">{v.value === null ? <span className="text-xs text-muted-foreground">{v.note || "n/a"}</span> : k === "market_cap" ? `$${fmtCompact(v.value)}` : fmtNum(v.value, 1)}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          {d.score ? <ScoreCard score={d.score} title="Long-Term Investment Score" /> : <Empty>No investment score yet.</Empty>}
          {d.personal_score && <ScoreCard score={d.personal_score} title="Score with your custom weights" />}
          <Card>
            <CardHeader>
              <CardTitle>Signal history</CardTitle>
            </CardHeader>
            <CardContent className="max-h-[480px] space-y-2 overflow-y-auto">
              {signals.data?.length === 0 && <Empty>No signals yet.</Empty>}
              {signals.data?.slice(0, 60).map((s) => (
                <details key={s.id} className="rounded border p-2 text-sm">
                  <summary className="flex cursor-pointer items-center justify-between gap-2">
                    <span className="text-xs text-muted-foreground">{s.signal_date}</span>
                    <span className="flex items-center gap-2">
                      <SignalBadge signal={s.signal_type} />
                      <span className="text-[10px] text-muted-foreground">{labelize(s.confirmation_status)}</span>
                    </span>
                  </summary>
                  <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                    {s.reasons.map((r, k) => (
                      <li key={k}>{r}</li>
                    ))}
                  </ul>
                  <div className="mt-1 text-[10px] text-muted-foreground">
                    {s.timeframe} · {s.data_status} · {s.provider_label} · score {s.score ?? "—"} · confidence {s.confidence ?? "—"}
                  </div>
                </details>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
