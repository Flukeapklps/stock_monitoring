export const fmtMoney = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export const fmtNum = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const fmtPct = (v: number | null | undefined, digits = 2, signed = true) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${signed && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;

export const fmtCompact = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);

export const changeClass = (v: number | null | undefined) =>
  v === null || v === undefined ? "text-muted-foreground" : v >= 0 ? "text-bull" : "text-bear";

/** Format an ISO timestamp in the user's selected timezone (timestamps are stored in UTC). */
export function fmtDateTime(iso: string | null | undefined, tz?: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    timeZone: tz || undefined,
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export const labelize = (s: string | null | undefined) => (s ? s.replace(/_/g, " ") : "—");
