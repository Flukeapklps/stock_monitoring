"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { post } from "@/lib/api";
import { changeClass, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { PageHeader } from "@/components/app-shell";
import { EquityChart } from "@/components/charts/indicator-charts";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle, ErrorBox, Input, Label, Switch, TBody, TD, TH, THead, TR, Table } from "@/components/ui";

interface Strategy {
  require_price_above_sma200: boolean;
  require_sma50_above_sma200: boolean;
  rsi_min: number;
  rsi_max: number;
  require_macd_bull_cross: boolean;
  macd_cross_lookback: number;
  exit_weekly_below_sma40: boolean;
  exit_trend_reversal: boolean;
  stop_loss_pct: number | null;
  commission_bps: number;
  slippage_bps: number;
  initial_capital: number;
  risk_free_rate_pct: number;
}
interface Result {
  symbol: string;
  start: string;
  end: string;
  metrics: Record<string, number | null>;
  benchmarks: Record<string, Record<string, number | null>>;
  trades: { entry_date: string; entry_price: number; exit_date: string | null; exit_price: number | null; exit_reason: string | null; return_pct: number | null; holding_days: number | null; pnl: number | null }[];
  equity_curve: Record<string, number | string | null>[];
  assumptions: string[];
  data_providers: string[];
}

const DEFAULT: Strategy = {
  require_price_above_sma200: true,
  require_sma50_above_sma200: true,
  rsi_min: 40,
  rsi_max: 55,
  require_macd_bull_cross: true,
  macd_cross_lookback: 3,
  exit_weekly_below_sma40: true,
  exit_trend_reversal: true,
  stop_loss_pct: 15,
  commission_bps: 5,
  slippage_bps: 5,
  initial_capital: 10000,
  risk_free_rate_pct: 0,
};
const COLORS = ["#22c55e", "#94a3b8", "#38bdf8", "#f97316"];
const METRICS: [string, string, (v: number | null) => string][] = [
  ["total_return_pct", "Total return", (v) => fmtPct(v)],
  ["cagr_pct", "CAGR", (v) => fmtPct(v)],
  ["max_drawdown_pct", "Max drawdown", (v) => fmtPct(v)],
  ["sharpe_ratio", "Sharpe", (v) => fmtNum(v)],
  ["sortino_ratio", "Sortino", (v) => fmtNum(v)],
  ["annualized_volatility_pct", "Volatility", (v) => fmtPct(v, 2, false)],
];

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("AAPL");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [s, setS] = useState<Strategy>(DEFAULT);
  const run = useMutation({ mutationFn: () => post<Result>("/backtest/run", { symbol, start: start || null, end: end || null, strategy: s, benchmarks: ["SPY", "QQQ"] }) });
  const r = run.data;
  const num = (k: keyof Strategy, label: string, step = "1") => (
    <div>
      <Label>{label}</Label>
      <Input type="number" step={step} value={s[k] === null ? "" : String(s[k])} onChange={(e) => setS({ ...s, [k]: e.target.value === "" ? null : Number(e.target.value) })} />
    </div>
  );
  const sw = (k: keyof Strategy, label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <Switch checked={Boolean(s[k])} onCheckedChange={(v) => setS({ ...s, [k]: v })} /> {label}
    </label>
  );
  const curveKeys = r ? Object.keys(r.equity_curve[0] ?? {}).filter((k) => k !== "date") : [];

  return (
    <div className="space-y-4">
      <PageHeader title="Strategy Backtest" description="Signals at the close, fills at the next open, adjusted prices, costs and slippage included." />
      <Card>
        <CardContent className="space-y-4 pt-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <div>
              <Label>Symbol</Label>
              <Input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
            </div>
            <div>
              <Label>Start (optional)</Label>
              <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <Label>End (optional)</Label>
              <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            {num("initial_capital", "Initial capital", "100")}
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-muted-foreground">BUY rules</h4>
              {sw("require_price_above_sma200", "Price above SMA 200")}
              {sw("require_sma50_above_sma200", "SMA 50 above SMA 200")}
              {sw("require_macd_bull_cross", "MACD bullish crossover")}
              <div className="grid grid-cols-3 gap-2">
                {num("rsi_min", "RSI min")}
                {num("rsi_max", "RSI max")}
                {num("macd_cross_lookback", "Cross ≤ N days")}
              </div>
            </div>
            <div className="space-y-2">
              <h4 className="text-xs font-semibold text-muted-foreground">SELL / REVIEW rules</h4>
              {sw("exit_weekly_below_sma40", "Weekly close below SMA 40")}
              {sw("exit_trend_reversal", "Long-term trend reversal")}
              {num("stop_loss_pct", "Risk threshold (% below entry, empty = off)", "0.5")}
            </div>
            <div className="grid grid-cols-2 gap-2 self-start">
              {num("commission_bps", "Commission (bps)", "0.5")}
              {num("slippage_bps", "Slippage (bps)", "0.5")}
              {num("risk_free_rate_pct", "Risk-free rate %", "0.1")}
            </div>
          </div>
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? "Running…" : "Run backtest"}
          </Button>
        </CardContent>
      </Card>
      <ErrorBox error={run.error} />
      {r && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>
                {r.symbol} · {r.start} → {r.end}
              </CardTitle>
              <CardDescription>Data: {r.data_providers.join(", ")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Table>
                <THead>
                  <TR>
                    <TH>Metric</TH>
                    <TH className="text-right">Strategy</TH>
                    {Object.keys(r.benchmarks).map((b) => (
                      <TH key={b} className="text-right">
                        {b}
                      </TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {METRICS.map(([k, label, f]) => (
                    <TR key={k}>
                      <TD>{label}</TD>
                      <TD className="text-right font-semibold">{f(r.metrics[k])}</TD>
                      {Object.values(r.benchmarks).map((b, i) => (
                        <TD key={i} className="text-right text-muted-foreground">
                          {f(b[k] ?? null)}
                        </TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
              <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
                {[
                  ["Trades", fmtNum(r.metrics.number_of_trades, 0)],
                  ["Win rate", fmtPct(r.metrics.win_rate_pct, 1, false)],
                  ["Profit factor", fmtNum(r.metrics.profit_factor)],
                  ["Avg holding (days)", fmtNum(r.metrics.average_holding_days, 0)],
                  ["Final equity", fmtMoney(r.metrics.final_equity)],
                ].map(([k, v]) => (
                  <div key={k} className="rounded border p-2">
                    <div className="text-xs text-muted-foreground">{k}</div>
                    <div className="num font-semibold">{v}</div>
                  </div>
                ))}
              </div>
              <EquityChart rows={r.equity_curve as { date: string }[]} series={curveKeys.map((k, i) => ({ key: k, color: COLORS[i % COLORS.length] }))} />
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                {r.assumptions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Trades</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <THead>
                  <TR>
                    <TH>Entry</TH>
                    <TH className="text-right">Price</TH>
                    <TH>Exit</TH>
                    <TH className="text-right">Price</TH>
                    <TH className="text-right">Return</TH>
                    <TH className="text-right">Days</TH>
                    <TH>Exit reason</TH>
                  </TR>
                </THead>
                <TBody>
                  {r.trades.map((t, i) => (
                    <TR key={i}>
                      <TD>{t.entry_date}</TD>
                      <TD className="text-right">{fmtMoney(t.entry_price)}</TD>
                      <TD>{t.exit_date ?? "open"}</TD>
                      <TD className="text-right">{fmtMoney(t.exit_price)}</TD>
                      <TD className={`text-right ${changeClass(t.return_pct)}`}>{fmtPct(t.return_pct)}</TD>
                      <TD className="text-right">{t.holding_days ?? "—"}</TD>
                      <TD className="whitespace-normal text-xs text-muted-foreground">{t.exit_reason}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
