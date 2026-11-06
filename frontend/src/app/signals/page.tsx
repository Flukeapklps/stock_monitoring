"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { fmtMoney, fmtNum, labelize } from "@/lib/format";
import type { Signal } from "@/lib/types";
import { DataStatusBadge, ScorePill, SignalBadge } from "@/components/badges";
import { PageHeader } from "@/components/app-shell";
import { Card, CardContent, CardHeader, CardTitle, Empty, ErrorBox, Input, Select, Skeleton, TBody, TD, TH, THead, TR, Table } from "@/components/ui";

const TYPES = ["STRONG_LONG_TERM_TREND", "BULLISH", "ACCUMULATION_WATCH", "NEUTRAL", "WEAKENING", "BEARISH"];
const FLAGS = ["TREND_WEAKENING", "OVERBOUGHT", "GOLDEN_CROSS", "DEATH_CROSS", "MACD_BULL_CROSS", "MACD_BEAR_CROSS"];

export default function SignalsPage() {
  const summary = useQuery({ queryKey: ["signal-summary"], queryFn: () => get<Record<string, Signal[]>>("/signals/summary") });
  const [type, setType] = useState("");
  const [symbol, setSymbol] = useState("");
  const [since, setSince] = useState("");
  const qs = new URLSearchParams({ limit: "300" });
  if (type) qs.set("signal_type", type);
  if (symbol) qs.set("symbol", symbol);
  if (since) qs.set("since", since);
  const list = useQuery({ queryKey: ["signals", type, symbol, since], queryFn: () => get<Signal[]>(`/signals?${qs}`) });

  return (
    <div className="space-y-4">
      <PageHeader title="Investment Signal Center" description="Rule-based long-term signals. Each signal records its indicator values, reasons, data provider and confirmation." />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6" data-testid="signal-groups">
        {TYPES.map((t) => (
          <Card key={t}>
            <CardHeader className="flex-row items-center justify-between">
              <SignalBadge signal={t} />
              <span className="num text-lg font-semibold">{summary.data?.[t]?.length ?? (summary.isLoading ? "…" : 0)}</span>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-1 text-xs">
              {summary.data?.[t]?.slice(0, 12).map((s) => (
                <Link key={s.symbol} href={`/stocks/${encodeURIComponent(s.symbol)}`} className="rounded bg-muted px-1.5 py-0.5 hover:text-primary" title={`score ${s.score ?? "—"}`}>
                  {s.symbol}
                </Link>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Signal history</CardTitle>
          <div className="flex flex-col gap-2 pt-2 sm:flex-row">
            <Select value={type} onChange={(e) => setType(e.target.value)} className="sm:w-56" aria-label="Signal type">
              <option value="">All signal types</option>
              {[...TYPES, ...FLAGS].map((t) => (
                <option key={t} value={t}>
                  {labelize(t)}
                </option>
              ))}
            </Select>
            <Input placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} className="sm:w-32" />
            <Input type="date" value={since} onChange={(e) => setSince(e.target.value)} className="sm:w-44" aria-label="Since" />
          </div>
        </CardHeader>
        <CardContent>
          <ErrorBox error={list.error} />
          {list.isLoading && <Skeleton className="h-48" />}
          {list.data && list.data.length === 0 && <Empty>No signals match.</Empty>}
          {list.data && list.data.length > 0 && (
            <Table>
              <THead>
                <TR>
                  <TH>Date</TH>
                  <TH>Symbol</TH>
                  <TH>Signal</TH>
                  <TH>TF</TH>
                  <TH className="text-right">Score</TH>
                  <TH className="text-right">Close</TH>
                  <TH className="text-right">RSI</TH>
                  <TH>Confirmation</TH>
                  <TH className="min-w-[280px]">Reasons</TH>
                  <TH>Data</TH>
                </TR>
              </THead>
              <TBody>
                {list.data.map((s) => (
                  <TR key={s.id}>
                    <TD className="text-xs">{s.signal_date}</TD>
                    <TD className="font-semibold">
                      <Link href={`/stocks/${encodeURIComponent(s.symbol)}`} className="hover:text-primary">
                        {s.symbol}
                      </Link>
                    </TD>
                    <TD>
                      <SignalBadge signal={s.signal_type} />
                    </TD>
                    <TD className="text-xs">{s.timeframe}</TD>
                    <TD className="text-right">
                      <ScorePill score={s.score} />
                    </TD>
                    <TD className="text-right">{fmtMoney(s.indicators.close as number)}</TD>
                    <TD className="text-right">{fmtNum(s.indicators.rsi_14 as number, 1)}</TD>
                    <TD className="text-xs">{labelize(s.confirmation_status)}</TD>
                    <TD className="whitespace-normal text-xs text-muted-foreground">{s.reasons.slice(0, 3).join(" · ")}</TD>
                    <TD className="text-xs">
                      <DataStatusBadge status={s.data_status} /> <span className="text-muted-foreground">{s.provider_label}</span>
                    </TD>
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
