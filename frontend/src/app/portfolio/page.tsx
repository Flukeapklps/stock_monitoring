"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { Trash2 } from "lucide-react";
import { del, get, post } from "@/lib/api";
import { changeClass, fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { DataStatusBadge } from "@/components/badges";
import { PageHeader } from "@/components/app-shell";
import { EquityChart } from "@/components/charts/indicator-charts";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, ErrorBox, Input, Label, Select, TBody, TD, TH, THead, TR, Table, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";

interface Totals {
  market_value: number;
  cost_basis: number;
  unrealized_pl: number | null;
  return_pct: number | null;
  realized_pl: number;
  dividend_income: number;
  total_pl: number | null;
}
interface Holding {
  holding_id: number;
  symbol: string;
  name: string | null;
  quantity: number;
  average_cost: number;
  purchase_date: string | null;
  price: number | null;
  price_date: string | null;
  data_status: string;
  provider: string | null;
  market_value: number | null;
  unrealized_pl: number | null;
  return_pct: number | null;
  realized_pl: number;
  dividend_income: number;
  allocation_pct: number | null;
  from_transactions: boolean;
  notes: string | null;
}
interface PDetail {
  name: string;
  holdings: Holding[];
  totals: Totals;
  incomplete_prices: boolean;
}
const COLORS = ["#22c55e", "#38bdf8", "#a78bfa", "#f97316", "#eab308", "#f472b6", "#14b8a6", "#94a3b8", "#ef4444", "#84cc16"];

export default function PortfolioPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["portfolios"], queryFn: () => get<{ id: number; name: string; totals: Totals }[]>("/portfolios") });
  const [sel, setSel] = useState<number | null>(null);
  const pid = sel ?? list.data?.[0]?.id;
  const detail = useQuery({ queryKey: ["portfolio", pid], queryFn: () => get<PDetail>(`/portfolios/${pid}`), enabled: !!pid });
  const perf = useQuery({
    queryKey: ["portfolio-perf", pid],
    queryFn: () => get<{ series: { date: string; value: number; index: number; benchmark_index?: number }[]; metrics: Record<string, number | string | null>; note?: string }>(`/portfolios/${pid}/performance`),
    enabled: !!pid,
  });
  const txs = useQuery({
    queryKey: ["portfolio-tx", pid],
    queryFn: () => get<{ id: number; symbol: string; tx_type: string; trade_date: string; quantity: number; price: number; fees: number }[]>(`/portfolios/${pid}/transactions`),
    enabled: !!pid,
  });
  const invalidate = () => ["portfolios", "portfolio", "portfolio-perf", "portfolio-tx"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));

  const [newName, setNewName] = useState("");
  const create = useMutation({ mutationFn: () => post<{ id: number }>("/portfolios", { name: newName }), onSuccess: (r) => (setSel(r.id), setNewName(""), invalidate()) });
  const [h, setH] = useState({ symbol: "", quantity: "", average_cost: "", purchase_date: "", notes: "" });
  const addHolding = useMutation({
    mutationFn: () => post(`/portfolios/${pid}/holdings`, { symbol: h.symbol, quantity: Number(h.quantity), average_cost: Number(h.average_cost), purchase_date: h.purchase_date || null, notes: h.notes || null }),
    onSuccess: () => (setH({ symbol: "", quantity: "", average_cost: "", purchase_date: "", notes: "" }), invalidate()),
  });
  const [t, setT] = useState({ symbol: "", tx_type: "BUY", trade_date: "", quantity: "", price: "", fees: "0" });
  const addTx = useMutation({
    mutationFn: () => post(`/portfolios/${pid}/transactions`, { ...t, quantity: Number(t.quantity), price: Number(t.price), fees: Number(t.fees || 0) }),
    onSuccess: () => (setT({ ...t, symbol: "", quantity: "", price: "" }), invalidate()),
  });
  const delHolding = useMutation({ mutationFn: (id: number) => del(`/portfolios/${pid}/holdings/${id}`), onSuccess: invalidate });
  const delTx = useMutation({ mutationFn: (id: number) => del(`/portfolios/${pid}/transactions/${id}`), onSuccess: invalidate });

  const tot = detail.data?.totals;
  const pie = (detail.data?.holdings ?? []).filter((x) => x.market_value).map((x) => ({ name: x.symbol, value: x.market_value! }));

  return (
    <div className="space-y-4">
      <PageHeader
        title="Portfolio"
        description="Manual portfolio tracking (average-cost basis). Valued at the latest end-of-day close."
        actions={
          <div className="flex gap-2">
            <Select value={pid ?? ""} onChange={(e) => setSel(Number(e.target.value))} className="w-44" aria-label="Portfolio">
              {list.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (newName.trim()) create.mutate();
              }}
            >
              <Input placeholder="New portfolio" value={newName} onChange={(e) => setNewName(e.target.value)} className="w-36" />
              <Button type="submit" variant="outline">
                Create
              </Button>
            </form>
          </div>
        }
      />
      <ErrorBox error={list.error || create.error || addHolding.error || addTx.error || delTx.error} />
      {list.data && list.data.length === 0 && <Empty>Create a portfolio to start tracking holdings.</Empty>}

      {tot && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ["Market value", fmtMoney(tot.market_value), ""],
            ["Unrealized P/L", `${fmtMoney(tot.unrealized_pl)} (${fmtPct(tot.return_pct)})`, changeClass(tot.unrealized_pl)],
            ["Realized P/L", fmtMoney(tot.realized_pl), changeClass(tot.realized_pl)],
            ["Dividend income", fmtMoney(tot.dividend_income), ""],
            ["Time-weighted return", fmtPct(perf.data?.metrics.time_weighted_return_pct as number), changeClass(perf.data?.metrics.time_weighted_return_pct as number)],
          ].map(([k, v, c]) => (
            <Card key={k} className="p-3">
              <div className="text-xs text-muted-foreground">{k}</div>
              <div className={`num mt-1 text-lg font-semibold ${c}`}>{v}</div>
            </Card>
          ))}
        </div>
      )}
      {detail.data?.incomplete_prices && <p className="text-xs text-yellow-500">Some holdings have no stored price yet; totals are incomplete.</p>}

      {pid && (
        <Tabs defaultValue="holdings">
          <TabsList>
            <TabsTrigger value="holdings">Holdings</TabsTrigger>
            <TabsTrigger value="performance">Performance</TabsTrigger>
            <TabsTrigger value="transactions">Transactions</TabsTrigger>
          </TabsList>
          <TabsContent value="holdings" className="space-y-4">
            <div className="grid gap-4 xl:grid-cols-3">
              <Card className="xl:col-span-2">
                <CardContent className="pt-4">
                  {detail.data?.holdings.length ? (
                    <Table>
                      <THead>
                        <TR>
                          <TH>Symbol</TH>
                          <TH className="text-right">Qty</TH>
                          <TH className="text-right">Avg cost</TH>
                          <TH className="text-right">Price</TH>
                          <TH className="text-right">Value</TH>
                          <TH className="text-right">Unrealized</TH>
                          <TH className="text-right">Return</TH>
                          <TH className="text-right">Alloc.</TH>
                          <TH>Data</TH>
                          <TH />
                        </TR>
                      </THead>
                      <TBody>
                        {detail.data.holdings.map((x) => (
                          <TR key={x.holding_id}>
                            <TD className="font-semibold">
                              <Link href={`/stocks/${encodeURIComponent(x.symbol)}`} className="hover:text-primary">
                                {x.symbol}
                              </Link>
                            </TD>
                            <TD className="text-right">{fmtNum(x.quantity, 4)}</TD>
                            <TD className="text-right">{fmtMoney(x.average_cost)}</TD>
                            <TD className="text-right">{fmtMoney(x.price)}</TD>
                            <TD className="text-right">{fmtMoney(x.market_value)}</TD>
                            <TD className={`text-right ${changeClass(x.unrealized_pl)}`}>{fmtMoney(x.unrealized_pl)}</TD>
                            <TD className={`text-right ${changeClass(x.return_pct)}`}>{fmtPct(x.return_pct)}</TD>
                            <TD className="text-right">{fmtPct(x.allocation_pct, 1, false)}</TD>
                            <TD className="text-xs">
                              <DataStatusBadge status={x.data_status} title={`${x.provider} · ${x.price_date}`} />
                            </TD>
                            <TD>
                              <Button variant="ghost" size="icon" aria-label={`Delete ${x.symbol}`} onClick={() => confirm(`Delete ${x.symbol} and its transactions?`) && delHolding.mutate(x.holding_id)}>
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  ) : (
                    <Empty>No holdings yet.</Empty>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Allocation</CardTitle>
                </CardHeader>
                <CardContent>
                  {pie.length ? (
                    <ResponsiveContainer width="100%" height={220}>
                      <PieChart>
                        <Pie data={pie} dataKey="value" nameKey="name" innerRadius={50} outerRadius={85} isAnimationActive={false} label={({ name }) => String(name)}>
                          {pie.map((_, i) => (
                            <Cell key={i} fill={COLORS[i % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(v) => fmtMoney(Number(v))} contentStyle={{ backgroundColor: "#0b0f14", border: "1px solid #1f2937" }} />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : (
                    <Empty>—</Empty>
                  )}
                </CardContent>
              </Card>
            </div>
            <Card>
              <CardHeader>
                <CardTitle>Add holding (manual)</CardTitle>
                <CardDescription>Use transactions instead for realized P/L tracking.</CardDescription>
              </CardHeader>
              <CardContent>
                <form
                  className="grid gap-2 sm:grid-cols-6"
                  onSubmit={(e) => {
                    e.preventDefault();
                    addHolding.mutate();
                  }}
                >
                  <div>
                    <Label>Symbol</Label>
                    <Input required value={h.symbol} onChange={(e) => setH({ ...h, symbol: e.target.value.toUpperCase() })} />
                  </div>
                  <div>
                    <Label>Quantity</Label>
                    <Input required type="number" step="any" min="0" value={h.quantity} onChange={(e) => setH({ ...h, quantity: e.target.value })} />
                  </div>
                  <div>
                    <Label>Average cost</Label>
                    <Input required type="number" step="any" min="0" value={h.average_cost} onChange={(e) => setH({ ...h, average_cost: e.target.value })} />
                  </div>
                  <div>
                    <Label>Purchase date</Label>
                    <Input type="date" value={h.purchase_date} onChange={(e) => setH({ ...h, purchase_date: e.target.value })} />
                  </div>
                  <div>
                    <Label>Notes</Label>
                    <Input value={h.notes} onChange={(e) => setH({ ...h, notes: e.target.value })} />
                  </div>
                  <div className="flex items-end">
                    <Button type="submit" className="w-full" disabled={addHolding.isPending}>
                      Save
                    </Button>
                  </div>
                </form>
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="performance">
            <Card>
              <CardHeader>
                <CardTitle>Portfolio vs SPY (indexed to 100, time-weighted)</CardTitle>
                <CardDescription>
                  Max drawdown {fmtPct(perf.data?.metrics.max_drawdown_pct as number)} · SPY {fmtPct(perf.data?.metrics.benchmark_return_pct as number)} since {perf.data?.metrics.start_date ?? "—"}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {perf.data?.series.length ? (
                  <EquityChart rows={perf.data.series} series={[{ key: "index", color: "#22c55e", label: "Portfolio" }, { key: "benchmark_index", color: "#94a3b8", label: "SPY" }]} />
                ) : (
                  <Empty>{perf.data?.note ?? "No performance data yet."}</Empty>
                )}
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="transactions" className="space-y-4">
            <Card>
              <CardContent className="pt-4">
                <form
                  className="grid gap-2 sm:grid-cols-7"
                  onSubmit={(e) => {
                    e.preventDefault();
                    addTx.mutate();
                  }}
                >
                  <Input required placeholder="Symbol" value={t.symbol} onChange={(e) => setT({ ...t, symbol: e.target.value.toUpperCase() })} />
                  <Select value={t.tx_type} onChange={(e) => setT({ ...t, tx_type: e.target.value })} aria-label="Type">
                    <option>BUY</option>
                    <option>SELL</option>
                    <option>DIVIDEND</option>
                  </Select>
                  <Input required type="date" value={t.trade_date} onChange={(e) => setT({ ...t, trade_date: e.target.value })} aria-label="Trade date" />
                  <Input required type="number" step="any" min="0" placeholder="Quantity" value={t.quantity} onChange={(e) => setT({ ...t, quantity: e.target.value })} />
                  <Input required type="number" step="any" min="0" placeholder="Price / amount" value={t.price} onChange={(e) => setT({ ...t, price: e.target.value })} />
                  <Input type="number" step="any" min="0" placeholder="Fees" value={t.fees} onChange={(e) => setT({ ...t, fees: e.target.value })} />
                  <Button type="submit" disabled={addTx.isPending}>
                    Record
                  </Button>
                </form>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                {txs.data?.length ? (
                  <Table>
                    <THead>
                      <TR>
                        <TH>Date</TH>
                        <TH>Symbol</TH>
                        <TH>Type</TH>
                        <TH className="text-right">Qty</TH>
                        <TH className="text-right">Price</TH>
                        <TH className="text-right">Fees</TH>
                        <TH />
                      </TR>
                    </THead>
                    <TBody>
                      {txs.data.map((x) => (
                        <TR key={x.id}>
                          <TD>{x.trade_date}</TD>
                          <TD className="font-semibold">{x.symbol}</TD>
                          <TD>{x.tx_type}</TD>
                          <TD className="text-right">{fmtNum(x.quantity, 4)}</TD>
                          <TD className="text-right">{fmtMoney(x.price)}</TD>
                          <TD className="text-right">{fmtMoney(x.fees)}</TD>
                          <TD>
                            <Button variant="ghost" size="icon" aria-label="Delete transaction" onClick={() => delTx.mutate(x.id)}>
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                ) : (
                  <Empty>No transactions recorded.</Empty>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
