import { Badge } from "@/components/ui";
import { labelize } from "@/lib/format";

const SIGNAL_VARIANT: Record<string, "bull" | "bear" | "warn" | "default" | "info"> = {
  STRONG_LONG_TERM_TREND: "bull",
  BULLISH: "bull",
  ACCUMULATION_WATCH: "info",
  NEUTRAL: "default",
  WEAKENING: "warn",
  OVERBOUGHT: "warn",
  TREND_WEAKENING: "bear",
  BEARISH: "bear",
  GOLDEN_CROSS: "bull",
  DEATH_CROSS: "bear",
  MACD_BULL_CROSS: "bull",
  MACD_BEAR_CROSS: "bear",
};

export function SignalBadge({ signal }: { signal: string | null | undefined }) {
  if (!signal) return <span className="text-muted-foreground">—</span>;
  return <Badge variant={SIGNAL_VARIANT[signal] ?? "default"}>{labelize(signal)}</Badge>;
}

export function TrendBadge({ trend }: { trend: string | null | undefined }) {
  if (!trend) return <span className="text-muted-foreground">—</span>;
  return <Badge variant={trend === "BULLISH" ? "bull" : trend === "BEARISH" ? "bear" : "default"}>{trend}</Badge>;
}

/** Data status is always shown explicitly; END_OF_DAY is never presented as LIVE. */
export function DataStatusBadge({ status, title }: { status: string | null | undefined; title?: string }) {
  const s = status || "UNAVAILABLE";
  const variant = s === "LIVE" ? "bull" : s === "END_OF_DAY" || s === "HISTORICAL" ? "info" : s === "DELAYED" ? "warn" : "bear";
  return (
    <Badge variant={variant} title={title} data-testid="data-status">
      {labelize(s)}
    </Badge>
  );
}

export function ScorePill({ score, coverage }: { score: number | null | undefined; coverage?: number | null }) {
  if (score === null || score === undefined) return <span className="text-muted-foreground">—</span>;
  const color = score >= 80 ? "text-green-400" : score >= 65 ? "text-green-500" : score >= 50 ? "text-muted-foreground" : score >= 35 ? "text-yellow-400" : "text-red-400";
  const low = coverage !== null && coverage !== undefined && coverage < 0.6;
  return (
    <span className={`num font-semibold ${color}`} title={coverage !== null && coverage !== undefined ? `Data coverage ${(coverage * 100).toFixed(0)}%` : undefined}>
      {score.toFixed(0)}
      {low && <span className="ml-1 text-[10px] text-yellow-500">*</span>}
    </span>
  );
}
