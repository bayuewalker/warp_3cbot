export class AuthError extends Error {}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (response.status === 401) {
    if (path !== "/api/login") window.dispatchEvent(new Event("warp:session-expired"));
    throw new AuthError();
  }
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
  const payload = await response.json();
  // The operator dispatcher returns {} before its first published snapshot.
  if ((!options.method || options.method === "GET") && (!payload || !Object.keys(payload).length)) {
    throw new Error("snapshot_unavailable");
  }
  return payload as T;
}

export type ProviderHealth = {
  configured: boolean;
  healthy: boolean;
  state: "ok" | "cooldown" | "unconfigured";
  model: string;
  detail: string;
  until: string | null;
};

export type Status = {
  alive?: boolean;
  dependencies_healthy?: boolean;
  providers?: Record<string, { state: string; model: string; detail?: string; until?: string | null }>;
  mode: string;
  reason: string;
  market_data_fresh: boolean;
  risk_permitted: boolean;
  execution_permitted: boolean;
  halt: boolean;
  gating?: "halt" | "awaiting_worker" | "current";
  published_at?: string | null;
  equity: string | null;
  pnl_today: string | null;
  universe: string[];
  pipeline?: {
    screening: { state: string; source: string; count: number };
    analysis: { state: string; count: number; mode: string };
    risk: { state: string; reason: string };
    opportunities: { state: string; count: number };
  };
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
  as_of?: string;
  evidence_as_of?: string | null;
  source?: string;
  fresh?: boolean;
  backtest_passed: boolean;
  backtest_reason: string;
  score?: {
    trend: number;
    reversal: number;
    liquidity: number;
    structure: number;
    event: number;
    total: number;
  };
  min_score?: number;
  score_warning?: boolean;
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

export type BrokerAccount = {
  code: string;
  name: string;
  demo: boolean;
  badge: "DEMO" | "REAL";
  status: string;
  exchange_code: string;
  exchange_name: string;
  market: string;
  quote: string;
  free: string;
  used: string;
  total: string;
  selectable: boolean;
  reason: string;
};

export type WarpBot = {
  warp_id: string;
  name: string;
  external_code: string | null;
  kind: string;
  product: string;
  account_code: string;
  market: string;
  symbol: string;
  status: string;
  commitment: string;
  created_at: string;
};

export type BudgetView = {
  sleeve_usdt?: string;
  sleeve_pct?: string;
  used?: string;
  remaining?: string;
  open_bots?: number;
  max_bots?: number;
  absolute?: string;
  fraction?: string;
};

export type HorizonStat = {
  n: number;
  hits: number;
  hit_rate: string | null;
  avg_move: string | null;
};

export type ForwardStats = {
  hit_rule: string;
  horizons: string[];
  by_verdict: Record<string, Record<string, HorizonStat>>;
  scored: number;
  pending: number;
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
