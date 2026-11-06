"use client";

import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { fmtDateTime, labelize } from "@/lib/format";
import { PageHeader, useMe } from "@/components/app-shell";
import { FlexPreview } from "@/components/flex-preview";
import { Badge, Button, Card, CardContent, Empty, ErrorBox, Input, Select, Skeleton, TBody, TD, TH, THead, TR, Table } from "@/components/ui";

interface Item {
  id: number;
  display_name: string | null;
  line_user_id: string | null;
  stock_symbol: string | null;
  notification_type: string;
  status: string;
  attempt_count: number;
  http_status: number | null;
  line_request_id: string | null;
  error_message: string | null;
  created_at: string;
  sent_at: string | null;
  next_attempt_at: string | null;
  preview: string;
  message_payload: { type: string; [k: string]: unknown }[];
  api_response: unknown;
}

const STATUS_VARIANT: Record<string, "bull" | "bear" | "warn" | "default" | "info"> = { ACCEPTED: "bull", FAILED: "bear", UNKNOWN: "warn", QUEUED: "info", SENDING: "info", SKIPPED: "default" };
const PAGE = 50;

export default function NotificationsPage() {
  const me = useMe();
  const [status, setStatus] = useState("");
  const [symbol, setSymbol] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  if (status) qs.set("status", status);
  if (symbol) qs.set("symbol", symbol);
  if (type) qs.set("notification_type", type);
  const hist = useQuery({ queryKey: ["line-history", status, symbol, type, page], queryFn: () => get<{ total: number; items: Item[] }>(`/line/history?${qs}`), refetchInterval: 30_000 });

  return (
    <div className="space-y-4">
      <PageHeader title="Notification History" description='LINE delivery log. "Accepted" means the LINE API accepted the request — not proof the user saw it.' />
      <div className="flex flex-col gap-2 sm:flex-row">
        <Select value={status} onChange={(e) => (setStatus(e.target.value), setPage(0))} className="sm:w-40" aria-label="Status">
          <option value="">All statuses</option>
          {Object.keys(STATUS_VARIANT).map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Select value={type} onChange={(e) => (setType(e.target.value), setPage(0))} className="sm:w-52" aria-label="Type">
          <option value="">All types</option>
          {["ACCUMULATION", "STRONG_BULLISH", "TREND_WEAKENING", "PRICE_ALERT", "FUNDAMENTAL", "PORTFOLIO", "DAILY_SUMMARY", "WEEKLY_SUMMARY", "MONTHLY_SUMMARY", "TEST"].map((t) => (
            <option key={t} value={t}>
              {labelize(t)}
            </option>
          ))}
        </Select>
        <Input placeholder="Symbol" value={symbol} onChange={(e) => (setSymbol(e.target.value.toUpperCase()), setPage(0))} className="sm:w-32" />
      </div>
      <ErrorBox error={hist.error} />
      {hist.isLoading && <Skeleton className="h-64" />}
      {hist.data && hist.data.items.length === 0 && <Empty>No notifications yet.</Empty>}
      {hist.data && hist.data.items.length > 0 && (
        <Card>
          <CardContent className="pt-4">
            <Table>
              <THead>
                <TR>
                  <TH>Created</TH>
                  <TH>Recipient</TH>
                  <TH>Type</TH>
                  <TH>Symbol</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Attempts</TH>
                  <TH>Sent</TH>
                  <TH>Message</TH>
                  <TH>API / error</TH>
                </TR>
              </THead>
              <TBody>
                {hist.data.items.map((n) => (
                  <Fragment key={n.id}>
                    <TR className="cursor-pointer" onClick={() => setOpen(open === n.id ? null : n.id)}>
                      <TD className="text-xs">{fmtDateTime(n.created_at, me.data?.timezone)}</TD>
                      <TD className="text-xs">{n.display_name || (n.line_user_id ? n.line_user_id.slice(0, 8) + "…" : "—")}</TD>
                      <TD className="text-xs">{labelize(n.notification_type)}</TD>
                      <TD className="font-semibold">{n.stock_symbol ?? "—"}</TD>
                      <TD>
                        <Badge variant={STATUS_VARIANT[n.status] ?? "default"}>{n.status}</Badge>
                      </TD>
                      <TD className="text-right">{n.attempt_count}</TD>
                      <TD className="text-xs">{fmtDateTime(n.sent_at, me.data?.timezone)}</TD>
                      <TD className="max-w-[260px] truncate text-xs text-muted-foreground">{n.preview}</TD>
                      <TD className="max-w-[260px] truncate text-xs text-muted-foreground" title={n.error_message ?? ""}>
                        {n.http_status ? `HTTP ${n.http_status} ` : ""}
                        {n.error_message ?? (n.line_request_id ? `req ${n.line_request_id}` : "")}
                      </TD>
                    </TR>
                    {open === n.id && (
                      <TR>
                        <TD colSpan={9} className="whitespace-normal bg-muted/30">
                          <div className="grid gap-4 p-2 lg:grid-cols-2">
                            <div className="space-y-2">
                              {n.message_payload.map((m, i) => (
                                <FlexPreview key={i} message={m} />
                              ))}
                            </div>
                            <pre className="max-h-96 overflow-auto rounded bg-background p-2 text-[10px]">{JSON.stringify({ api_response: n.api_response, next_attempt_at: n.next_attempt_at, line_request_id: n.line_request_id }, null, 1)}</pre>
                          </div>
                        </TD>
                      </TR>
                    )}
                  </Fragment>
                ))}
              </TBody>
            </Table>
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {hist.data.total} total · page {page + 1} of {Math.max(1, Math.ceil(hist.data.total / PAGE))}
              </span>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Previous
                </Button>
                <Button size="sm" variant="outline" disabled={(page + 1) * PAGE >= hist.data.total} onClick={() => setPage(page + 1)}>
                  Next
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
