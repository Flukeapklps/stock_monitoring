export type DataStatus = "LIVE" | "DELAYED" | "END_OF_DAY" | "HISTORICAL" | "STALE" | "UNAVAILABLE";

export interface Freshness {
  status: "FRESH" | "STALE" | "UNAVAILABLE";
  expected_session: string;
  latest_bar: string | null;
  sessions_behind: number | null;
}

export interface StockRow {
  symbol: string;
  name: string | null;
  asset_type: string;
  exchange: string | null;
  sector: string | null;
  price: number | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  trade_date: string | null;
  return_1y: number | null;
  return_3m: number | null;
  return_6m: number | null;
  rsi_14: number | null;
  sma_50: number | null;
  sma_200: number | null;
  dist_sma_200_pct: number | null;
  dist_52w_high_pct: number | null;
  trend: "BULLISH" | "BEARISH" | "NEUTRAL" | null;
  score: number | null;
  score_classification: string | null;
  score_coverage: number | null;
  latest_signal: string | null;
  latest_signal_date: string | null;
  provider: string | null;
  provider_label: string | null;
  data_status: DataStatus;
  freshness?: Freshness;
  display_name?: string;
  rank?: number;
  [key: string]: unknown;
}

export interface Signal {
  id: number;
  symbol: string;
  name: string | null;
  signal_date: string;
  timeframe: string;
  signal_type: string;
  is_primary: boolean;
  score: number | null;
  confidence: number | null;
  confirmation_status: string;
  indicators: Record<string, number | string | boolean | null>;
  reasons: string[];
  provider: string;
  provider_label: string;
  data_status: DataStatus;
  created_at: string;
}

export interface ScoreComponent {
  key: string;
  label: string;
  weight: number;
  score: number | null;
  coverage: number;
  sub_metrics: { key: string; label: string; value: number | null; score: number | null; weight: number; detail: string }[];
}

export interface Score {
  score_date?: string;
  total: number | null;
  classification: string | null;
  coverage: number;
  low_coverage: boolean;
  components: Record<string, ScoreComponent>;
  weights: Record<string, number>;
  explanation: string[];
  missing: string[];
  disclaimer: string;
}

export interface User {
  id: number;
  email: string;
  display_name: string | null;
  role: "admin" | "user";
  timezone: string;
  score_weights: Record<string, number>;
  default_weights: Record<string, number>;
}
