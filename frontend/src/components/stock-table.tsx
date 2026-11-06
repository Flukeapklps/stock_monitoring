"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { DataStatusBadge, ScorePill, SignalBadge, TrendBadge } from "@/components/badges";
import { Button, Input, Select, TBody, TD, TH, THead, TR, Table } from "@/components/ui";
import { changeClass, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import type { StockRow } from "@/lib/types";

type SortKey = "symbol" | "price" | "change_pct" | "return_1y" | "rsi_14" | "sma_50" | "sma_200" | "score" | "trend" | "latest_signal";

export function filterAndSort(rows: StockRow[], q: string, trend: string, sort: SortKey, dir: "asc" | "desc"): StockRow[] {
  const needle = q.trim().toLowerCase();
  const filtered = rows.filter(
    (r) => (!needle || r.symbol.toLowerCase().includes(needle) || (r.name || "").toLowerCase().includes(needle)) && (!trend || r.trend === trend),
  );
  const mult = dir === "asc" ? 1 : -1;
  return [...filtered].sort((a, b) => {
    const av = a[sort] as string | number | null;
    const bv = b[sort] as string | number | null;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * mult;
    return String(av).localeCompare(String(bv)) * mult;
  });
}

export function StockTable({ rows, onRemove, compact = false }: { rows: StockRow[]; onRemove?: (symbol: string) => void; compact?: boolean }) {
  const [q, setQ] = useState("");
  const [trend, setTrend] = useState("");
  const [sort, setSort] = useState<SortKey>("score");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const view = useMemo(() => filterAndSort(rows, q, trend, sort, dir), [rows, q, trend, sort, dir]);

  const header = (key: SortKey, label: string, cls = "") => (
    <TH className={cls}>
      <button
        className="inline-flex items-center gap-1 hover:text-foreground"
        onClick={() => {
          if (sort === key) setDir(dir === "asc" ? "desc" : "asc");
          else {
            setSort(key);
            setDir(key === "symbol" ? "asc" : "desc");
          }
        }}
      >
        {label}
        {sort === key && (dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </TH>
  );

  return (
    <div className="space-y-2">
      {!compact && (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input placeholder="Filter by symbol or company…" value={q} onChange={(e) => setQ(e.target.value)} className="sm:max-w-xs" aria-label="Filter stocks" />
          <Select value={trend} onChange={(e) => setTrend(e.target.value)} className="sm:max-w-[160px]" aria-label="Filter by trend">
            <option value="">All trends</option>
            <option value="BULLISH">Bullish</option>
            <option value="NEUTRAL">Neutral</option>
            <option value="BEARISH">Bearish</option>
          </Select>
        </div>
      )}
      <Table data-testid="stock-table">
        <THead>
          <TR>
            {header("symbol", "Symbol")}
            <TH className="hidden lg:table-cell">Company</TH>
            {header("price", "Price", "text-right")}
            {header("change_pct", "Day", "text-right")}
            {header("return_1y", "1Y", "text-right")}
            {header("rsi_14", "RSI", "text-right")}
            {header("sma_50", "SMA 50", "text-right hidden md:table-cell")}
            {header("sma_200", "SMA 200", "text-right hidden md:table-cell")}
            {header("trend", "Trend")}
            {header("score", "Score", "text-right")}
            {header("latest_signal", "Signal")}
            <TH className="hidden xl:table-cell">Data</TH>
            {onRemove && <TH />}
          </TR>
        </THead>
        <TBody>
          {view.map((r) => (
            <TR key={r.symbol}>
              <TD className="font-semibold">
                <Link href={`/stocks/${encodeURIComponent(r.symbol)}`} className="hover:text-primary">
                  {r.symbol}
                </Link>
              </TD>
              <TD className="hidden max-w-[220px] truncate text-muted-foreground lg:table-cell">{r.display_name || r.name || "—"}</TD>
              <TD className="text-right">{r.asset_type === "INDEX" ? fmtNum(r.price) : fmtMoney(r.price)}</TD>
              <TD className={`text-right ${changeClass(r.change_pct)}`}>{fmtPct(r.change_pct)}</TD>
              <TD className={`text-right ${changeClass(r.return_1y)}`}>{fmtPct(r.return_1y, 1)}</TD>
              <TD className="text-right">{fmtNum(r.rsi_14, 1)}</TD>
              <TD className="hidden text-right md:table-cell">{fmtMoney(r.sma_50)}</TD>
              <TD className="hidden text-right md:table-cell">{fmtMoney(r.sma_200)}</TD>
              <TD>
                <TrendBadge trend={r.trend} />
              </TD>
              <TD className="text-right">
                <ScorePill score={r.score} coverage={r.score_coverage} />
              </TD>
              <TD>
                <SignalBadge signal={r.latest_signal} />
              </TD>
              <TD className="hidden text-xs text-muted-foreground xl:table-cell">
                <DataStatusBadge status={r.data_status} title={`${r.provider_label ?? "?"} · ${r.trade_date ?? "no data"}`} />
                <span className="ml-1">{r.provider_label}</span>
              </TD>
              {onRemove && (
                <TD>
                  <Button variant="ghost" size="icon" aria-label={`Remove ${r.symbol}`} onClick={() => onRemove(r.symbol)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TD>
              )}
            </TR>
          ))}
        </TBody>
      </Table>
      {rows.length > 0 && <p className="text-[11px] text-muted-foreground">* Score computed with less than 60% data coverage. Prices are end-of-day closes from the provider shown; not real-time.</p>}
    </div>
  );
}
