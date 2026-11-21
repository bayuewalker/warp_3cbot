export class AuthError extends Error {}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (response.status === 401) throw new AuthError();
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = String(payload.error || "");
    } catch {
      detail = "";
    }
    const error = new Error(detail || "request_failed");
    (error as Error & { status?: number }).status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}

export type Status = {
  mode: string;
  reason: string;
  market_data_fresh: boolean;
  risk_permitted: boolean;
  execution_permitted: boolean;
  halt: boolean;
  equity: string | null;
  pnl_today: string | null;
  universe: string[];
};

export type CardRow = {
  symbol: string;
  action: string;
  model_action: string;
  confidence: string;
  blended: string;
  why: string;
  why_id: string;
  entry: string | null;
  stop: string | null;
  take: string | null;
  regime: string | null;
  rsi: string | null;
  thesis: string;
  backtest_passed: boolean;
  backtest_reason: string;
};

export type Position = {
  symbol: string | null;
  status: string | null;
  strategy_id: string | null;
  client_id: string | null;
  paper: boolean | null;
};

export type Hit = {
  symbol: string;
  last: string | null;
  change_pct: string | null;
  quote_volume: string | null;
  recommendation: string | null;
  rsi: string | null;
  source: string;
};

export type Filters = {
  min_quote_volume: string;
  min_change_pct: string;
  max_change_pct: string;
  rsi_min: string;
  rsi_max: string;
  min_recommendation: string;
  limit: number;
};

export type Preview = {
  full_commitment: string;
  share_of_equity: string;
  fits_caps: boolean;
  equity_reference: string;
  equity_is_live: boolean;
  base_or_grid_quote: string;
  orders_or_grids: number;
  stop: string;
  take_profit: string;
};
