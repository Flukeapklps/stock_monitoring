"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Link2, Send, XCircle } from "lucide-react";
import { ApiError, get, post, put, del } from "@/lib/api";
import { fmtDateTime, labelize } from "@/lib/format";
import { PageHeader, useMe } from "@/components/app-shell";
import { FlexPreview } from "@/components/flex-preview";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Empty, ErrorBox, Input, Label, Select, Switch, TBody, TD, TH, THead, TR, Table } from "@/components/ui";

interface Status {
  enabled: boolean;
  configured: boolean;
  connection_status: string;
  access_token: string | null;
  channel_secret_configured: boolean;
  admin_recipient_configured: boolean;
  webhook_url: string | null;
  webhook_status: string;
  last_webhook_event_at: string | null;
  last_webhook_event_type: string | null;
  last_successful_message_at: string | null;
  api_error_count_24h: number;
  webhook_error_count_24h: number;
  queued: number;
  followers: number;
  daily_report_time: string;
  weekly_report_time: string;
  cooldown_minutes: number;
  max_retry_attempts: number;
}
interface Prefs {
  line_user_id: string;
  display_name: string | null;
  is_following: boolean;
  preferences: { type: string; label: string; enabled: boolean }[];
  general: {
    enabled: boolean;
    min_score: number;
    rsi_low: number;
    rsi_high: number;
    cooldown_minutes: number;
    max_per_day: number;
    quiet_hours: { start: string; end: string } | null;
    timezone: string;
    symbols: string[];
    daily_report_time: string;
    weekly_report_time: string;
  };
  alert_rules: { id: number; stock_symbol: string; rule_type: string; conditions: Record<string, number>; enabled: boolean; last_triggered_at: string | null }[];
}
interface LineUserRow {
  id: number;
  line_user_id: string;
  display_name: string | null;
  is_following: boolean;
  linked_user: string | null;
  followed_at: string | null;
}

function StatusRow({ label, value, ok }: { label: string; value: React.ReactNode; ok?: boolean | null }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/50 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1 text-right">
        {ok === true && <CheckCircle2 className="h-4 w-4 text-bull" />}
        {ok === false && <XCircle className="h-4 w-4 text-bear" />}
        {value}
      </span>
    </div>
  );
}

export default function LineSettingsPage() {
  const me = useMe();
  const tz = me.data?.timezone;
  const isAdmin = me.data?.role === "admin";
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["line-status"], queryFn: () => get<Status>("/line/status"), refetchInterval: 30_000 });
  const users = useQuery({ queryKey: ["line-users"], queryFn: () => get<LineUserRow[]>("/line/users"), enabled: isAdmin });
  const [recipient, setRecipient] = useState<string>("");
  const prefs = useQuery({
    queryKey: ["line-prefs", recipient],
    queryFn: () => get<Prefs>(`/line/preferences${recipient ? `?line_user_id=${encodeURIComponent(recipient)}` : ""}`),
    retry: false,
  });
  const [general, setGeneral] = useState<Prefs["general"] | null>(null);
  useEffect(() => {
    if (prefs.data) setGeneral(prefs.data.general);
  }, [prefs.data]);
  const target = recipient || prefs.data?.line_user_id || undefined;

  const savePrefs = useMutation({
    mutationFn: (body: { preferences?: Record<string, boolean>; general?: Partial<Prefs["general"]> }) => put<Prefs>("/line/preferences", { line_user_id: target, ...body }),
    onSuccess: (d) => qc.setQueryData(["line-prefs", recipient], d),
  });
  const quota = useMutation({ mutationFn: () => get<Record<string, unknown>>("/line/quota?refresh=true") });
  const conn = useMutation({ mutationFn: () => post<Record<string, unknown>>("/line/connection-test") });
  const test = useMutation({ mutationFn: () => post<{ queued: number; dispatch: Record<string, number>; server_validation: string }>("/line/test", { line_user_id: target }), onSuccess: () => qc.invalidateQueries({ queryKey: ["line-status"] }) });
  const report = useMutation({ mutationFn: (kind: string) => post<{ queued: number }>(`/line/reports/${kind}`, { line_user_id: target }) });
  const linkCode = useMutation({ mutationFn: () => post<{ code: string; instructions: string }>("/line/link-code") });
  const [previewSymbol, setPreviewSymbol] = useState("AAPL");
  const [previewSignal, setPreviewSignal] = useState("ACCUMULATION_WATCH");
  const preview = useMutation({ mutationFn: () => get<{ messages: { type: string; [k: string]: unknown }[] }>(`/line/preview/${encodeURIComponent(previewSymbol)}?signal=${previewSignal}`) });
  const alertTest = useMutation({ mutationFn: () => post<{ queued: number; dispatch: Record<string, unknown> }>("/line/alerts/test", { symbol: previewSymbol, signal: previewSignal, line_user_id: target }) });
  const [rule, setRule] = useState({ stock_symbol: "", rule_type: "PRICE_ABOVE", price: "", low: "", high: "" });
  const addRule = useMutation({
    mutationFn: () =>
      post("/line/alert-rules", {
        line_user_id: target,
        stock_symbol: rule.stock_symbol,
        rule_type: rule.rule_type,
        conditions: rule.rule_type === "ACCUMULATION_ZONE" ? { low: Number(rule.low), high: Number(rule.high) } : { price: Number(rule.price) },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["line-prefs"] }),
  });
  const delRule = useMutation({ mutationFn: (id: number) => del(`/line/alert-rules/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["line-prefs"] }) });

  const s = status.data;
  const noLinked = prefs.error instanceof ApiError && prefs.error.status === 404;

  return (
    <div className="space-y-4">
      <PageHeader title="LINE Integration" description="LINE Official Account via the Messaging API. Tokens stay on the server and are never shown in full." />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Connection <Badge variant={s?.connection_status === "CONNECTED" ? "bull" : s?.connection_status === "DEGRADED" ? "warn" : "bear"}>{s?.connection_status ?? "…"}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {s && (
              <>
                <StatusRow label="LINE enabled" value={s.enabled ? "Yes" : "No"} ok={s.enabled} />
                <StatusRow label="Channel access token" value={<span className="font-mono text-xs">{s.access_token ?? "not set"}</span>} ok={s.configured} />
                <StatusRow label="Channel secret" value={s.channel_secret_configured ? "configured" : "not set"} ok={s.channel_secret_configured} />
                <StatusRow label="Admin recipient (LINE_USER_ID)" value={s.admin_recipient_configured ? "configured" : "not set"} ok={s.admin_recipient_configured} />
                <StatusRow label="Webhook" value={labelize(s.webhook_status)} ok={s.webhook_status === "RECEIVING" ? true : null} />
                <StatusRow label="Webhook URL" value={<span className="break-all text-xs">{s.webhook_url ?? "—"}</span>} />
                <StatusRow label="Last webhook event" value={`${fmtDateTime(s.last_webhook_event_at, tz)}${s.last_webhook_event_type ? ` (${s.last_webhook_event_type})` : ""}`} />
                <StatusRow label="Last successful message" value={fmtDateTime(s.last_successful_message_at, tz)} />
                <StatusRow label="API errors (24h)" value={s.api_error_count_24h} ok={s.api_error_count_24h === 0} />
                <StatusRow label="Webhook errors (24h)" value={s.webhook_error_count_24h} ok={s.webhook_error_count_24h === 0} />
                <StatusRow label="Queued notifications" value={s.queued} />
                <StatusRow label="Followers" value={s.followers} />
                <StatusRow label="Reports (ET)" value={`daily ${s.daily_report_time} · weekly ${s.weekly_report_time}`} />
              </>
            )}
            {isAdmin && (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => conn.mutate()} disabled={conn.isPending}>
                  Test connection
                </Button>
                <Button size="sm" variant="outline" onClick={() => quota.mutate()} disabled={quota.isPending}>
                  Check quota
                </Button>
                <Button size="sm" onClick={() => test.mutate()} disabled={test.isPending}>
                  <Send className="h-4 w-4" /> Send test message
                </Button>
              </div>
            )}
            {conn.data && <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 text-[11px]">{JSON.stringify(conn.data, null, 1)}</pre>}
            {quota.data && <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 text-[11px]">{JSON.stringify(quota.data, null, 1)}</pre>}
            {test.data && (
              <p className="mt-2 text-xs text-muted-foreground">
                Queued {test.data.queued} · accepted {test.data.dispatch.accepted ?? 0} · failed {test.data.dispatch.failed ?? 0} · LINE validation: {test.data.server_validation}. &quot;Accepted&quot; means the API accepted the request; it does not prove the message was displayed.
              </p>
            )}
            <ErrorBox error={conn.error || quota.error || test.error} />
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Recipients & notification rules</CardTitle>
            <CardDescription>Users register by adding the Official Account as a friend; their LINE user ID arrives through a verified webhook event.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-end gap-2">
              {isAdmin && users.data && (
                <div>
                  <Label>Recipient</Label>
                  <Select value={recipient} onChange={(e) => setRecipient(e.target.value)} className="w-72">
                    <option value="">My linked LINE account</option>
                    {users.data.map((u) => (
                      <option key={u.id} value={u.line_user_id}>
                        {u.display_name || u.line_user_id.slice(0, 10) + "…"} {u.is_following ? "" : "(unfollowed)"} {u.linked_user ? `→ ${u.linked_user}` : ""}
                      </option>
                    ))}
                  </Select>
                </div>
              )}
              <Button size="sm" variant="outline" onClick={() => linkCode.mutate()}>
                <Link2 className="h-4 w-4" /> Link my LINE account
              </Button>
              {linkCode.data && (
                <span className="text-sm">
                  Send <code className="rounded bg-muted px-1">/link {linkCode.data.code}</code> to the bot.
                </span>
              )}
            </div>
            {noLinked && <Empty>No LINE account is linked to your user yet. Add the Official Account as a friend, then generate a link code above.</Empty>}
            <ErrorBox error={!noLinked ? prefs.error : null} />
            {prefs.data && general && (
              <>
                <div className="text-xs text-muted-foreground">
                  {prefs.data.display_name ?? "LINE user"} · <span className="font-mono">{prefs.data.line_user_id.slice(0, 8)}…</span> · {prefs.data.is_following ? <span className="text-bull">following</span> : <span className="text-bear">not following (no messages are sent)</span>}
                </div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  <label className="flex items-center justify-between rounded border p-2 text-sm">
                    <span className="font-semibold">All LINE notifications</span>
                    <Switch checked={general.enabled} onCheckedChange={(v) => savePrefs.mutate({ general: { enabled: v } })} />
                  </label>
                  {prefs.data.preferences.map((p) => (
                    <label key={p.type} className="flex items-center justify-between rounded border p-2 text-sm">
                      {p.label}
                      <Switch checked={p.enabled} onCheckedChange={(v) => savePrefs.mutate({ preferences: { [p.type]: v } })} aria-label={p.label} />
                    </label>
                  ))}
                </div>
                <form
                  className="grid gap-2 sm:grid-cols-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const { enabled: _e, ...rest } = general;
                    void _e;
                    savePrefs.mutate({ general: rest });
                  }}
                >
                  <div>
                    <Label>Minimum score</Label>
                    <Input type="number" min={0} max={100} value={general.min_score} onChange={(e) => setGeneral({ ...general, min_score: Number(e.target.value) })} />
                  </div>
                  <div>
                    <Label>RSI accumulation low</Label>
                    <Input type="number" min={0} max={100} value={general.rsi_low} onChange={(e) => setGeneral({ ...general, rsi_low: Number(e.target.value) })} />
                  </div>
                  <div>
                    <Label>RSI accumulation high</Label>
                    <Input type="number" min={0} max={100} value={general.rsi_high} onChange={(e) => setGeneral({ ...general, rsi_high: Number(e.target.value) })} />
                  </div>
                  <div>
                    <Label>Cooldown (minutes)</Label>
                    <Input type="number" min={0} value={general.cooldown_minutes} onChange={(e) => setGeneral({ ...general, cooldown_minutes: Number(e.target.value) })} />
                  </div>
                  <div>
                    <Label>Max alerts / 24h</Label>
                    <Input type="number" min={1} value={general.max_per_day} onChange={(e) => setGeneral({ ...general, max_per_day: Number(e.target.value) })} />
                  </div>
                  <div>
                    <Label>Timezone</Label>
                    <Input value={general.timezone} onChange={(e) => setGeneral({ ...general, timezone: e.target.value })} />
                  </div>
                  <div>
                    <Label>Quiet hours start</Label>
                    <Input type="time" value={general.quiet_hours?.start ?? ""} onChange={(e) => setGeneral({ ...general, quiet_hours: e.target.value ? { start: e.target.value, end: general.quiet_hours?.end ?? "07:00" } : null })} />
                  </div>
                  <div>
                    <Label>Quiet hours end</Label>
                    <Input type="time" value={general.quiet_hours?.end ?? ""} onChange={(e) => setGeneral({ ...general, quiet_hours: general.quiet_hours ? { ...general.quiet_hours, end: e.target.value } : null })} />
                  </div>
                  <div className="sm:col-span-3">
                    <Label>Extra symbols to follow (besides watchlist & portfolio), comma separated</Label>
                    <Input value={general.symbols.join(", ")} onChange={(e) => setGeneral({ ...general, symbols: e.target.value.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) })} />
                  </div>
                  <div className="flex items-end">
                    <Button type="submit" className="w-full" disabled={savePrefs.isPending}>
                      Save settings
                    </Button>
                  </div>
                </form>
                <ErrorBox error={savePrefs.error} />

                <div className="space-y-2">
                  <h4 className="text-xs font-semibold text-muted-foreground">Price alert rules</h4>
                  <form
                    className="flex flex-wrap items-end gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      addRule.mutate();
                    }}
                  >
                    <Input required placeholder="Symbol" className="w-28" value={rule.stock_symbol} onChange={(e) => setRule({ ...rule, stock_symbol: e.target.value.toUpperCase() })} />
                    <Select className="w-48" value={rule.rule_type} onChange={(e) => setRule({ ...rule, rule_type: e.target.value })} aria-label="Rule type">
                      <option value="PRICE_ABOVE">Crosses above target</option>
                      <option value="PRICE_BELOW">Crosses below support</option>
                      <option value="RISK_THRESHOLD">Falls below risk threshold</option>
                      <option value="ACCUMULATION_ZONE">Enters accumulation zone</option>
                    </Select>
                    {rule.rule_type === "ACCUMULATION_ZONE" ? (
                      <>
                        <Input required type="number" step="any" placeholder="Low" className="w-24" value={rule.low} onChange={(e) => setRule({ ...rule, low: e.target.value })} />
                        <Input required type="number" step="any" placeholder="High" className="w-24" value={rule.high} onChange={(e) => setRule({ ...rule, high: e.target.value })} />
                      </>
                    ) : (
                      <Input required type="number" step="any" placeholder="Price" className="w-28" value={rule.price} onChange={(e) => setRule({ ...rule, price: e.target.value })} />
                    )}
                    <Button type="submit" size="sm">
                      Add rule
                    </Button>
                  </form>
                  <ErrorBox error={addRule.error} />
                  {prefs.data.alert_rules.length > 0 && (
                    <Table>
                      <THead>
                        <TR>
                          <TH>Symbol</TH>
                          <TH>Rule</TH>
                          <TH>Conditions</TH>
                          <TH>Last triggered</TH>
                          <TH />
                        </TR>
                      </THead>
                      <TBody>
                        {prefs.data.alert_rules.map((r) => (
                          <TR key={r.id}>
                            <TD className="font-semibold">{r.stock_symbol}</TD>
                            <TD>{labelize(r.rule_type)}</TD>
                            <TD className="text-xs">{JSON.stringify(r.conditions)}</TD>
                            <TD className="text-xs">{fmtDateTime(r.last_triggered_at, tz)}</TD>
                            <TD>
                              <Button size="sm" variant="ghost" onClick={() => delRule.mutate(r.id)}>
                                Remove
                              </Button>
                            </TD>
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                  )}
                  {!prefs.data.preferences.find((p) => p.type === "PRICE_ALERT")?.enabled && <p className="text-xs text-yellow-500">Price alerts are disabled above; rules will not notify until enabled.</p>}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Alert card preview & test</CardTitle>
          <CardDescription>Renders the exact Flex payload the bot sends (approximate rendering; LINE&apos;s client is authoritative).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <Label>Symbol</Label>
              <Input className="w-28" value={previewSymbol} onChange={(e) => setPreviewSymbol(e.target.value.toUpperCase())} />
            </div>
            <div>
              <Label>Signal</Label>
              <Select className="w-56" value={previewSignal} onChange={(e) => setPreviewSignal(e.target.value)}>
                {["ACCUMULATION_WATCH", "STRONG_LONG_TERM_TREND", "BULLISH", "NEUTRAL", "WEAKENING", "TREND_WEAKENING", "BEARISH", "OVERBOUGHT"].map((x) => (
                  <option key={x} value={x}>
                    {labelize(x)}
                  </option>
                ))}
              </Select>
            </div>
            <Button variant="outline" onClick={() => preview.mutate()} disabled={preview.isPending}>
              Preview
            </Button>
            {isAdmin && (
              <Button onClick={() => alertTest.mutate()} disabled={alertTest.isPending}>
                <Send className="h-4 w-4" /> Send as test alert
              </Button>
            )}
            {isAdmin &&
              ["daily", "weekly", "monthly"].map((k) => (
                <Button key={k} variant="secondary" size="sm" onClick={() => report.mutate(k)} disabled={report.isPending}>
                  Send {k} report
                </Button>
              ))}
          </div>
          <ErrorBox error={preview.error || alertTest.error || report.error} />
          {report.data && <p className="text-xs text-muted-foreground">Report queued for {report.data.queued} recipient(s).</p>}
          {alertTest.data && <p className="text-xs text-muted-foreground">Queued {alertTest.data.queued}; dispatch {JSON.stringify(alertTest.data.dispatch)}</p>}
          {preview.data && (
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl bg-[#8cabd9]/20 p-4" data-testid="flex-preview">
                {preview.data.messages.map((m, i) => (
                  <FlexPreview key={i} message={m} />
                ))}
              </div>
              <pre className="max-h-[520px] overflow-auto rounded bg-muted p-2 text-[10px]">{JSON.stringify(preview.data.messages, null, 1)}</pre>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
