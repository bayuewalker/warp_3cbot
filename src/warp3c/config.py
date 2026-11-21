"""Strict configuration. Execution still requires credentials and an explicit mode."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlparse


def dec(name: str, fallback: str) -> Decimal:
    result = Decimal(os.getenv(name, fallback))
    if not result.is_finite():
        raise ValueError(f"{name}: non-finite number")
    return result


def _fraction(name: str, value: Decimal) -> None:
    if not value.is_finite() or not Decimal("0") < value <= Decimal("1"):
        raise ValueError(f"Invalid fraction: {name}")


def parse_groups(raw: str) -> tuple[tuple[str, str], ...]:
    """`majors:BTCUSDT|ETHUSDT,alts:SOLUSDT`. Empty means one shared book."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError("WARP_CORRELATION_GROUPS entries need group:SYMBOL|SYMBOL")
        name, symbols = chunk.split(":", 1)
        name = name.strip().lower()
        if not name.isidentifier():
            raise ValueError("Invalid correlation group name")
        for symbol in symbols.split("|"):
            symbol = symbol.strip().upper()
            if not symbol:
                continue
            if symbol in seen:
                raise ValueError(f"Symbol in two correlation groups: {symbol}")
            seen.add(symbol)
            pairs.append((symbol, name))
    return tuple(pairs)


@dataclass(frozen=True)
class Settings:
    mode: str = "observe"
    db_path: str = "/data/warp.db"
    cycle_seconds: int = 900
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    discovery: str = "static"
    discovery_allow: tuple[str, ...] = ()
    deny_symbols: tuple[str, ...] = ()
    max_candidates: int = 12
    max_spread: Decimal = Decimal("0.005")
    scan_seconds: int = 300
    research_seconds: int = 900
    monitor_seconds: int = 60
    reconcile_seconds: int = 120
    slippage_bps: Decimal = Decimal("0")
    operator_token: str = field(default="", repr=False)
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    exchange_code: str = "BINANCE"
    api_url: str = "https://trade.3commas.io"
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    profile_code: str = ""
    vibe_url: str = "http://127.0.0.1:8899"
    vibe_key: str = field(default="", repr=False)
    market_data_url: str = "https://data-api.binance.vision"
    max_committed: Decimal = Decimal("0.40")
    max_pair: Decimal = Decimal("0.15")
    max_group: Decimal = Decimal("0.25")
    min_reserve: Decimal = Decimal("0.25")
    max_positions: int = 4
    min_confidence: Decimal = Decimal("0.75")
    max_signal_age: int = 30
    max_account_age: int = 180
    min_base: Decimal = Decimal("10")
    max_base: Decimal = Decimal("100")
    dca_orders: int = 4
    dca_deviation: Decimal = Decimal("0.02")
    dca_multiplier: Decimal = Decimal("1.5")
    dca_step_multiplier: Decimal = Decimal("1.2")
    take_profit: Decimal = Decimal("0.025")
    stop_loss: Decimal = Decimal("0.20")
    fee_bps: Decimal = Decimal("10")
    max_daily_loss: Decimal = Decimal("0.05")
    max_drawdown: Decimal = Decimal("0.15")
    min_quote_volume: Decimal = Decimal("1000000")
    max_volatility: Decimal = Decimal("0.03")
    rsi_min: Decimal = Decimal("45")
    rsi_max: Decimal = Decimal("75")
    correlation: tuple[tuple[str, str], ...] = ()
    alert_webhook: str = field(default="", repr=False)
    live_ack: str = field(default="", repr=False)
    analysis: str = "auto"
    require_backtest: bool = False
    blend_confidence: bool = False
    backtest_min_trades: int = 3
    screener_url: str = "https://scanner.tradingview.com"
    nvidia_model: str = "nvidia/nemotron-3-nano-30b-a3b"
    openrouter_model: str = "deepseek/deepseek-v4-pro"
    llm_timeout: int = 40
    llm_daily_calls: int = 48
    llm_daily_usd: Decimal = Decimal("2")
    llm_usd_per_call: Decimal = Decimal("0.02")
    nvidia_api_key: str = field(default="", repr=False)
    openrouter_api_key: str = field(default="", repr=False)
    dashboard_password: str = field(default="", repr=False)
    session_secret: str = field(default="", repr=False)
    sleeve_usdt: Decimal = Decimal("0")
    sleeve_pct: Decimal = Decimal("0.10")
    allocation_ceiling: Decimal = Decimal("0.75")
    per_deal_ceiling: Decimal = Decimal("0.25")
    max_deals: int = 3
    deal_stop_risk: Decimal = Decimal("0.025")
    aggregate_stop_risk: Decimal = Decimal("0.065")
    trade_daily_loss: Decimal = Decimal("0.03")
    trade_drawdown: Decimal = Decimal("0.08")
    stop_slippage: Decimal = Decimal("0.005")
    stress_shock: Decimal = Decimal("0.30")
    stress_slippage: Decimal = Decimal("0.01")
    stress_cap: Decimal = Decimal("0.08")
    corr_min: Decimal = Decimal("0.85")
    max_alts: int = 2
    min_score: int = 80
    tp_min: Decimal = Decimal("0.012")
    tp_fee_multiple: Decimal = Decimal("3")
    trade_min_volume: Decimal = Decimal("10000000")
    trade_max_spread: Decimal = Decimal("0.0025")
    trade_max_surge: Decimal = Decimal("0.20")
    cooldown_stop_hours: int = 24
    cooldown_tp_hours: int = 2
    streak_pause_hours: int = 12
    stop_streak: int = 2
    review_days: int = 10
    paper_min_days: int = 30
    paper_min_deals: int = 30
    virtual_drop: Decimal = Decimal("0.35")

    def __post_init__(self) -> None:
        if self.mode not in {"observe", "paper", "live"}:
            raise ValueError("Invalid TRADING_MODE")
        if self.mode == "live" and self.live_ack != "I_UNDERSTAND_LIVE_ORDERS":
            raise ValueError("Live trading requires WARP_LIVE_EXECUTION_ACK")
        if self.mode != "observe" and not all((self.api_key, self.api_secret, self.profile_code)):
            raise ValueError("3Commas credentials and API profile are required")
        if self.sleeve_usdt < 0 or self.sleeve_pct < 0 or self.sleeve_pct > 1:
            raise ValueError("Invalid strategy sleeve")
        if self.sleeve_usdt == 0 and self.sleeve_pct == 0:
            raise ValueError("Strategy sleeve is unset")
        for name in (
            "allocation_ceiling",
            "per_deal_ceiling",
            "deal_stop_risk",
            "aggregate_stop_risk",
            "trade_daily_loss",
            "trade_drawdown",
            "stop_slippage",
            "stress_shock",
            "stress_slippage",
            "stress_cap",
            "corr_min",
            "tp_min",
            "trade_max_spread",
            "trade_max_surge",
            "virtual_drop",
        ):
            _fraction(name, getattr(self, name))
        if type(self.max_deals) is not int or not 1 <= self.max_deals <= 3:
            raise ValueError("WARP_MAX_DEALS must be 1, 2, or 3")
        if type(self.max_alts) is not int or self.max_alts < 0:
            raise ValueError("Invalid alt cap")
        if type(self.min_score) is not int or not 1 <= self.min_score <= 100:
            raise ValueError("Invalid score threshold")
        if self.tp_fee_multiple < 1 or self.trade_min_volume <= 0:
            raise ValueError("Invalid take-profit or volume gate")
        if min(self.cooldown_stop_hours, self.cooldown_tp_hours, self.streak_pause_hours, self.review_days) < 1:
            raise ValueError("Invalid cooldown")
        if self.paper_min_days < 1 or self.paper_min_deals < 1 or self.stop_streak < 1:
            raise ValueError("Invalid go/no-go window")
        if self.cycle_seconds < 60 or self.max_signal_age < 1 or self.max_account_age < 30:
            raise ValueError("Invalid cadence or freshness window")
        if self.discovery not in {"static", "auto", "screener"}:
            raise ValueError("Invalid WARP_DISCOVERY")
        if self.analysis not in {"vibe", "llm", "auto"}:
            raise ValueError("Invalid WARP_ANALYSIS")
        if not 5 <= self.llm_timeout <= 120 or self.llm_daily_calls < 1 or self.backtest_min_trades < 1:
            raise ValueError("Invalid analysis limits")
        if self.llm_daily_usd <= 0 or self.llm_usd_per_call <= 0:
            raise ValueError("Invalid LLM budget")
        if self.dashboard_password and len(self.dashboard_password) < 12:
            raise ValueError("Dashboard password must be at least 12 characters")
        if self.session_secret and len(self.session_secret) < 16:
            raise ValueError("Session secret must be at least 16 characters")
        scanner = urlparse(self.screener_url)
        if scanner.scheme != "https" or not scanner.netloc:
            raise ValueError("Screener URL must be https")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Symbol universe empty or duplicated")
        if any(not (s.isalnum() and s.endswith("USDT")) for s in self.symbols):
            raise ValueError("Only allowlisted USDT pairs are supported")
        if any(not (s.isalnum() and s.endswith("USDT")) for s in self.discovery_allow):
            raise ValueError("Discovery allowlist must be USDT pairs")
        if len(set(self.discovery_allow)) != len(self.discovery_allow):
            raise ValueError("Discovery allowlist contains a duplicate")
        if any(not (s.isalnum() and s.endswith("USDT")) for s in self.deny_symbols):
            raise ValueError("Denylist must be USDT pairs")
        if len(set(self.deny_symbols)) != len(self.deny_symbols):
            raise ValueError("Denylist contains a duplicate")
        if not 1 <= self.max_candidates <= 50:
            raise ValueError("Invalid candidate cap")
        if min(self.scan_seconds, self.research_seconds, self.monitor_seconds, self.reconcile_seconds) < 30:
            raise ValueError("Invalid cadence")
        if self.operator_token and len(self.operator_token) < 16:
            raise ValueError("Operator token must be at least 16 characters")
        if bool(self.telegram_bot_token) != bool(self.telegram_chat_id):
            raise ValueError("Telegram needs both a bot token and a chat id")
        if self.telegram_chat_id and not self.telegram_chat_id.lstrip("-").isdigit():
            raise ValueError("Telegram chat id must be numeric")
        if self.max_positions < 1 or not 1 <= self.dca_orders <= 30:
            raise ValueError("Invalid position/order count")
        if self.min_base <= 0 or self.max_base < self.min_base:
            raise ValueError("Invalid base order range")
        for name in (
            "max_committed",
            "max_pair",
            "max_group",
            "min_reserve",
            "min_confidence",
            "dca_deviation",
            "take_profit",
            "stop_loss",
            "max_daily_loss",
            "max_drawdown",
            "max_volatility",
            "max_spread",
        ):
            _fraction(name, getattr(self, name))
        if not Decimal("0") <= self.slippage_bps <= Decimal("100"):
            raise ValueError("Invalid slippage buffer")
        if self.max_committed + self.min_reserve > 1:
            raise ValueError("Committed + reserve fractions cannot exceed one")
        if not Decimal("0.001") <= self.dca_deviation <= Decimal("0.2"):
            raise ValueError("DCA deviation outside the 3Commas fraction range")
        if not Decimal("0.1") <= self.dca_multiplier <= Decimal("10"):
            raise ValueError("Invalid DCA amount multiplier")
        if not Decimal("0.1") <= self.dca_step_multiplier <= Decimal("10"):
            raise ValueError("Invalid DCA deviation multiplier")
        if not Decimal("0") <= self.fee_bps <= Decimal("100"):
            raise ValueError("Invalid fee buffer")
        if self.rsi_min < 0 or self.rsi_max > 100 or self.rsi_min >= self.rsi_max:
            raise ValueError("Invalid RSI band")
        if self.min_quote_volume <= 0:
            raise ValueError("Invalid minimum quote volume")
        if self.alert_webhook:
            parsed = urlparse(self.alert_webhook)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Alert webhook must be an https URL")
        known = {symbol for symbol, _group in self.correlation}
        if self.discovery in {"auto", "screener"} and not self.discovery_allow:
            invalid = [symbol for symbol in known if not (symbol.isalnum() and symbol.endswith("USDT"))]
            if invalid:
                raise ValueError("Correlation group contains a symbol that is not a USDT pair")
        else:
            universe = set(self.symbols) | set(self.discovery_allow)
            unknown = known.difference(universe)
            if unknown:
                raise ValueError("Correlation group contains a symbol outside the universe")

    def group_for(self, symbol: str) -> str:
        if not self.correlation:
            return "book"
        return dict(self.correlation).get(symbol, symbol)

    @property
    def fee_rate(self) -> Decimal:
        return self.fee_bps / Decimal("10000")

    @property
    def slippage_rate(self) -> Decimal:
        return self.slippage_bps / Decimal("10000")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mode=os.getenv("TRADING_MODE", "observe").lower(),
            db_path=os.getenv("WARP_DB_PATH", "/data/warp.db"),
            cycle_seconds=int(os.getenv("WARP_CYCLE_SECONDS", "900")),
            symbols=tuple(
                s.strip().upper()
                for s in os.getenv("WARP_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
                if s.strip()
            ),
            discovery=os.getenv("WARP_DISCOVERY", "static").strip().lower(),
            discovery_allow=tuple(
                s.strip().upper() for s in os.getenv("WARP_DISCOVERY_ALLOWLIST", "").split(",") if s.strip()
            ),
            deny_symbols=tuple(
                s.strip().upper() for s in os.getenv("WARP_DENY_SYMBOLS", "").split(",") if s.strip()
            ),
            max_candidates=int(os.getenv("WARP_MAX_CANDIDATES", "12")),
            max_spread=dec("WARP_MAX_SPREAD", "0.005"),
            scan_seconds=int(os.getenv("WARP_SCAN_SECONDS", "300")),
            research_seconds=int(os.getenv("WARP_RESEARCH_SECONDS", "900")),
            monitor_seconds=int(os.getenv("WARP_MONITOR_SECONDS", "60")),
            reconcile_seconds=int(os.getenv("WARP_RECONCILE_SECONDS", "120")),
            slippage_bps=dec("WARP_SLIPPAGE_BPS", "0"),
            operator_token=os.getenv("WARP_OPERATOR_TOKEN", ""),
            telegram_bot_token=os.getenv("WARP_TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("WARP_TELEGRAM_CHAT_ID", "").strip(),
            exchange_code=os.getenv("WARP_EXCHANGE_CODE", "BINANCE").upper(),
            api_url=os.getenv("THREECOMMAS_API_URL", "https://trade.3commas.io"),
            api_key=os.getenv("THREECOMMAS_API_KEY", ""),
            api_secret=os.getenv("THREECOMMAS_API_SECRET", ""),
            profile_code=os.getenv("THREECOMMAS_PROFILE_CODE", ""),
            vibe_url=os.getenv("VIBE_API_URL", "http://127.0.0.1:8899"),
            vibe_key=os.getenv("API_AUTH_KEY", ""),
            market_data_url=os.getenv("WARP_MARKET_DATA_URL", "https://data-api.binance.vision"),
            max_committed=dec("WARP_MAX_COMMITTED_FRACTION", "0.40"),
            max_pair=dec("WARP_MAX_PER_PAIR_FRACTION", "0.15"),
            max_group=dec("WARP_MAX_GROUP_FRACTION", "0.25"),
            min_reserve=dec("WARP_MIN_FREE_RESERVE_FRACTION", "0.25"),
            max_positions=int(os.getenv("WARP_MAX_ACTIVE_STRATEGIES", "4")),
            min_confidence=dec("WARP_MIN_CONFIDENCE", "0.75"),
            max_signal_age=int(os.getenv("WARP_MAX_SIGNAL_AGE_MINUTES", "30")),
            max_account_age=int(os.getenv("WARP_MAX_ACCOUNT_AGE_SECONDS", "180")),
            min_base=dec("WARP_MIN_BASE_ORDER_USDT", "10"),
            max_base=dec("WARP_MAX_BASE_ORDER_USDT", "100"),
            dca_orders=int(os.getenv("WARP_DCA_TOTAL_ORDERS", "4")),
            dca_deviation=dec("WARP_DCA_PRICE_DEVIATION", "0.02"),
            dca_multiplier=dec("WARP_DCA_VOLUME_MULTIPLIER", "1.5"),
            dca_step_multiplier=dec("WARP_DCA_DEVIATION_MULTIPLIER", "1.2"),
            take_profit=dec("WARP_TAKE_PROFIT_DEVIATION", "0.025"),
            stop_loss=dec("WARP_STOP_LOSS_DEVIATION", "0.20"),
            fee_bps=dec("WARP_FEE_BPS", "10"),
            max_daily_loss=dec("WARP_MAX_DAILY_LOSS_FRACTION", "0.05"),
            max_drawdown=dec("WARP_MAX_DRAWDOWN_FRACTION", "0.15"),
            min_quote_volume=dec("WARP_MIN_QUOTE_VOLUME_USDT", "1000000"),
            max_volatility=dec("WARP_MAX_VOLATILITY", "0.03"),
            rsi_min=dec("WARP_RSI_MIN", "45"),
            rsi_max=dec("WARP_RSI_MAX", "75"),
            correlation=parse_groups(os.getenv("WARP_CORRELATION_GROUPS", "")),
            alert_webhook=os.getenv("WARP_ALERT_WEBHOOK_URL", ""),
            live_ack=os.getenv("WARP_LIVE_EXECUTION_ACK", ""),
            analysis=os.getenv("WARP_ANALYSIS", "auto").strip().lower(),
            require_backtest=os.getenv("WARP_REQUIRE_BACKTEST", "0") == "1",
            blend_confidence=os.getenv("WARP_BLEND_CONFIDENCE", "0") == "1",
            backtest_min_trades=int(os.getenv("WARP_BACKTEST_MIN_TRADES", "3")),
            screener_url=os.getenv("WARP_SCREENER_URL", "https://scanner.tradingview.com"),
            nvidia_model=os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-nano-30b-a3b"),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-pro"),
            llm_timeout=int(os.getenv("WARP_LLM_TIMEOUT_SECONDS", "40")),
            llm_daily_calls=int(os.getenv("WARP_LLM_DAILY_CALLS", "48")),
            llm_daily_usd=dec("WARP_LLM_DAILY_USD", "2"),
            llm_usd_per_call=dec("WARP_LLM_USD_PER_CALL", "0.02"),
            nvidia_api_key=os.getenv("NVIDIA_API_KEY", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            dashboard_password=os.getenv("WARP_DASHBOARD_PASSWORD", ""),
            session_secret=os.getenv("WARP_SESSION_SECRET", ""),
            sleeve_usdt=dec("WARP_SLEEVE_USDT", "0"),
            sleeve_pct=dec("WARP_SLEEVE_PCT", "0.10"),
            allocation_ceiling=dec("WARP_ALLOCATION_CEILING", "0.75"),
            per_deal_ceiling=dec("WARP_PER_DEAL_CEILING", "0.25"),
            max_deals=int(os.getenv("WARP_MAX_DEALS", "3")),
            deal_stop_risk=dec("WARP_DEAL_STOP_RISK", "0.025"),
            aggregate_stop_risk=dec("WARP_AGGREGATE_STOP_RISK", "0.065"),
            trade_daily_loss=dec("WARP_TRADE_DAILY_LOSS", "0.03"),
            trade_drawdown=dec("WARP_TRADE_DRAWDOWN", "0.08"),
            stop_slippage=dec("WARP_STOP_SLIPPAGE", "0.005"),
            stress_shock=dec("WARP_STRESS_SHOCK", "0.30"),
            stress_slippage=dec("WARP_STRESS_SLIPPAGE", "0.01"),
            stress_cap=dec("WARP_STRESS_CAP", "0.08"),
            corr_min=dec("WARP_CORR_MIN", "0.85"),
            max_alts=int(os.getenv("WARP_MAX_ALTS", "2")),
            min_score=int(os.getenv("WARP_MIN_SCORE", "80")),
            tp_min=dec("WARP_TP_MIN", "0.012"),
            tp_fee_multiple=dec("WARP_TP_FEE_MULTIPLE", "3"),
            trade_min_volume=dec("WARP_TRADE_MIN_VOLUME", "10000000"),
            trade_max_spread=dec("WARP_TRADE_MAX_SPREAD", "0.0025"),
            trade_max_surge=dec("WARP_TRADE_MAX_SURGE", "0.20"),
            cooldown_stop_hours=int(os.getenv("WARP_COOLDOWN_STOP_HOURS", "24")),
            cooldown_tp_hours=int(os.getenv("WARP_COOLDOWN_TP_HOURS", "2")),
            streak_pause_hours=int(os.getenv("WARP_STREAK_PAUSE_HOURS", "12")),
            stop_streak=int(os.getenv("WARP_STOP_STREAK", "2")),
            review_days=int(os.getenv("WARP_REVIEW_DAYS", "10")),
            paper_min_days=int(os.getenv("WARP_PAPER_MIN_DAYS", "30")),
            paper_min_deals=int(os.getenv("WARP_PAPER_MIN_DEALS", "30")),
            virtual_drop=dec("WARP_VIRTUAL_STOP", "0.35"),
        )
