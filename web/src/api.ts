export class AuthError extends Error {}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (response.status === 401) {
    if (path !== "/api/login" && !options.signal?.aborted) window.dispatchEvent(new Event("warp:session-expired"));
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
  if (!options.method || options.method === "GET") {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("malformed_payload");
    if (!Object.keys(payload).length) throw new Error("snapshot_unavailable");
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
  lanes?: {
    primary?: string;
    spot?: { state?: "postponed" | "active" | string; since?: string; flag?: string };
  };
  xau?: {
    enabled?: boolean;
    state?: string;
    ready?: boolean;
    ready_reason?: string;
    feed?: string | null;
    basis?: string | null;
    basis_std?: string | null;
    spot_last?: string | null;
    spot_at?: string | null;
    perp_close?: string | null;
    perp_bar_at?: string | null;
    cycle_seconds?: number;
    window?: { name?: string; start_wib?: string; end_wib?: string };
    engine?: string | null;
    next_window?: { name?: string; start_wib?: string } | null;
    open_signal?: {
      direction?: string; entry?: string; sl?: string; tp1?: string; tp2?: string | null;
      grade?: string; valid_until?: string | null;
    } | null;
    today?: { signals_sent?: number; journal_only?: number; realized_r?: string };
    funnel?: { today?: Record<string, number>; window?: Record<string, number>; recent?: { reason?: string; wib?: string }[] };
    last_signal_at?: string | null;
    heartbeat_at?: string | null;
    basis_healthy?: boolean;
    warming?: boolean;
    basis_samples?: number | null;
    basis_value?: string | null;
    last_bar_age_s?: number | null;
    halt?: { global?: boolean; xau?: boolean };
    recent?: Record<string, unknown>[];
    playbook?: Record<string, unknown> | null;
  } | null;
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
  expires_at?: string | null;
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
  quote: string | null;
  free: string | null;
  used: string | null;
  total: string | null;
  error?: string | null;
  assets?: {
    asset: string;
    total: string;
    usd: string | null;
    usd_reason?: string | null;
    quote_age_seconds?: number | null;
  }[];
  selectable: boolean;
  reason: string;
};

export type AccountPosition = {
  id: string | null;
  account_code: string | null;
  account_name: string | null;
  kind: "dca" | "grid" | "strategy";
  bot_name: string | null;
  bot_code: string | null;
  owner: "warp" | "external";
  pair: string | null;
  symbol: string | null;
  base_asset: string | null;
  quote_asset: string | null;
  side: "long" | "short" | null;
  status: string | null;
  paper: boolean | null;
  average_price: string | null;
  size: string | null;
  cost: string | null;
  current_price: string | null;
  current_price_source: "3commas" | "binance" | null;
  pnl_usd: string | null;
  pnl_usd_source: "3commas" | "computed" | null;
  pnl_pct: string | null;
  pnl_pct_source: "3commas" | "computed" | null;
  safety_used: number | null;
  safety_max: number | null;
  take_profits: string[];
  stop_price: string | null;
  opened_at: string | null;
  stale?: boolean;
};

export type AccountPositionsPayload = {
  positions: AccountPosition[];
  by_account: Record<string, number>;
  as_of: string | null;
  error: string | null;
  errors: string[];
  bots_checked?: number | null;
  stale?: boolean;
  cadence_seconds: number;
  readonly: boolean;
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

/** Real /api/warp-bots budget on main: configuration only. used, remaining, open_bots and max_bots are never published. */
export type BudgetView = {
  sleeve_usdt?: string | null;
  sleeve_pct?: string | null;
  allocation_ceiling?: string | null;
  reserve_floor?: string | null;
  per_deal_ceiling?: string | null;
  max_deals?: number | null;
  deal_stop_risk?: string | null;
  aggregate_stop_risk?: string | null;
  daily_loss_halt?: string | null;
  drawdown_halt?: string | null;
  min_score?: number | null;
  review_days?: number | null;
  used?: string | null;
  remaining?: string | null;
  open_bots?: number | null;
  max_bots?: number | null;
  absolute?: string | null;
  fraction?: string | null;
};

export type AccountsPayload = {
  accounts: BrokerAccount[];
  selected: { code?: string } | null;
  as_of?: string | null;
  error?: string | null;
  sleeve?: { eligible: string | null; sleeve: string | null; reason: string | null; basis?: string };
  readonly?: boolean;
  places_orders?: boolean;
  cadence_seconds?: number;
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
