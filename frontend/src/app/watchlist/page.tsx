"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { del, get, post } from "@/lib/api";
import type { StockRow } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { StockTable } from "@/components/stock-table";
import { Button, Card, CardContent, Empty, ErrorBox, Input, Select, Skeleton } from "@/components/ui";

interface WL {
  id: number;
  name: string;
  count: number;
  is_default: boolean;
}

export default function WatchlistPage() {
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["watchlists"], queryFn: () => get<WL[]>("/watchlists") });
  const [selected, setSelected] = useState<number | null>(null);
  const wlId = selected ?? lists.data?.[0]?.id;
  const detail = useQuery({ queryKey: ["watchlist", wlId], queryFn: () => get<{ name: string; rows: StockRow[] }>(`/watchlists/${wlId}`), enabled: !!wlId });
  const [symbol, setSymbol] = useState("");
  const [newName, setNewName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["watchlist", wlId] });
    qc.invalidateQueries({ queryKey: ["watchlists"] });
  };
  const add = useMutation({
    mutationFn: (s: string) => post<{ symbol: string; added: boolean; loading: boolean }>(`/watchlists/${wlId}/items`, { symbol: s }),
    onSuccess: (r) => {
      setSymbol("");
      setMsg(r.loading ? `${r.symbol} added — historical data is loading in the background.` : `${r.symbol} ${r.added ? "added" : "already in this watchlist"}.`);
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (s: string) => del(`/watchlists/${wlId}/items/${encodeURIComponent(s)}`), onSuccess: refresh });
  const create = useMutation({
    mutationFn: (name: string) => post<{ id: number }>("/watchlists", { name }),
    onSuccess: (r) => {
      setNewName("");
      setSelected(r.id);
      qc.invalidateQueries({ queryKey: ["watchlists"] });
    },
  });
  const drop = useMutation({
    mutationFn: (id: number) => del(`/watchlists/${id}`),
    onSuccess: () => {
      setSelected(null);
      qc.invalidateQueries({ queryKey: ["watchlists"] });
    },
  });

  return (
    <div className="space-y-4">
      <PageHeader title="Watchlist" description="Track stocks and ETFs. Data: end-of-day closes with source labels." />
      <Card>
        <CardContent className="flex flex-col gap-3 pt-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Select watchlist" value={wlId ?? ""} onChange={(e) => setSelected(Number(e.target.value))} className="w-48">
              {lists.data?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.count})
                </option>
              ))}
            </Select>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (newName.trim()) create.mutate(newName.trim());
              }}
            >
              <Input placeholder="New watchlist" value={newName} onChange={(e) => setNewName(e.target.value)} className="w-40" />
              <Button type="submit" variant="outline" size="sm">
                Create
              </Button>
            </form>
            {lists.data && lists.data.length > 1 && wlId && (
              <Button variant="ghost" size="sm" onClick={() => confirm("Delete this watchlist?") && drop.mutate(wlId)}>
                Delete list
              </Button>
            )}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setMsg(null);
              if (symbol.trim()) add.mutate(symbol.trim());
            }}
          >
            <Input aria-label="Add symbol" placeholder="Add symbol (e.g. MSFT)" value={symbol} onChange={(e) => setSymbol(e.target.value)} className="w-48" />
            <Button type="submit" disabled={add.isPending || !wlId}>
              <Plus className="h-4 w-4" /> Add
            </Button>
          </form>
        </CardContent>
      </Card>
      <ErrorBox error={add.error || remove.error || create.error || detail.error} />
      {msg && <div className="text-sm text-muted-foreground">{msg}</div>}
      {detail.isLoading && <Skeleton className="h-64" />}
      {detail.data && (detail.data.rows.length ? <StockTable rows={detail.data.rows} onRemove={(s) => remove.mutate(s)} /> : <Empty>No stocks yet. Add a symbol above.</Empty>)}
    </div>
  );
}
