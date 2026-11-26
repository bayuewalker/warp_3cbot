"""Slim analysis client. NVIDIA screens, OpenRouter writes the final JSON, either can fail over."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from .config import DEFAULT_NVIDIA_MODEL
from .models import Signal
from .telemetry import log_event
from .vibe import ResearchError, extract_decisions

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

PRICE_KEYS = {
    "price",
    "entry",
    "stop",
    "stop_loss",
    "take_profit",
    "tp",
    "sl",
    "amount",
    "size",
    "quantity",
    "order",
    "limit",
}

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# 404 and 410 mean this model id is gone. Retrying every cycle only burns the cap.
GONE = frozenset({404, 410})
PROVIDER_COOLDOWN = timedelta(hours=1)


class LlmError(RuntimeError):
    pass


def strip_price_fields(raw: dict) -> dict:
    return {key: value for key, value in raw.items() if key not in PRICE_KEYS}


def _piece(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        chunks = [_piece(part) for part in value]
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(value, dict):
        for key in ("text", "content", "reasoning", "reasoning_content"):
            found = _piece(value.get(key))
            if found:
                return found
    return ""


def _response_texts(payload: object) -> list[str]:
    """Visible answer first, then DeepSeek thinking text.

    deepseek/deepseek-v4-pro thinks by default. OpenRouter can return HTTP 200
    with message.content null and the JSON only in reasoning or reasoning_content.
    Reading content alone turns that 200 into ResearchError and publishes no signals.
    """
    if not isinstance(payload, dict):
        raise LlmError("LLM response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LlmError("LLM response has no choices")
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise LlmError("LLM response content is empty")
    ordered = [
        _piece(message.get("content")),
        _piece(message.get("reasoning_content")),
        _piece(message.get("reasoning")),
        _piece(choices[0].get("reasoning_content")),
        _piece(choices[0].get("reasoning")),
    ]
    texts: list[str] = []
    for text in ordered:
        if text and text not in texts:
            texts.append(text)
    if not texts:
        raise LlmError("LLM response content is empty")
    return texts


class _Budget:
    """UTC-day call and USD caps. Usage is journaled so a restart does not reset it."""

    def __init__(self, calls: int, usd: Decimal, per_call: Decimal):
        self.calls = calls
        self.usd = usd
        self.per_call = per_call
        self._lock = threading.Lock()
        self._journal = None
        self._day = ""
        self._used_calls = 0
        self._used_usd = Decimal("0")

    def bind(self, journal) -> None:
        with self._lock:
            self._journal = journal

    def charge(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            day, used_calls, used_usd = self._load(today)
            if used_calls >= self.calls or used_usd + self.per_call > self.usd:
                raise ResearchError("LLM daily cap reached")
            used_calls += 1
            used_usd += self.per_call
            self._store(day, used_calls, used_usd)
            self._day = day
            self._used_calls = used_calls
            self._used_usd = used_usd

    def snapshot(self) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            day, used_calls, used_usd = self._load(today)
        return {
            "day": day,
            "calls_used": used_calls,
            "calls_cap": self.calls,
            "usd_used": format(used_usd, "f"),
            "usd_cap": format(self.usd, "f"),
        }

    def _load(self, today: str) -> tuple[str, int, Decimal]:
        if self._journal is not None:
            raw = self._journal.get_setting("llm_budget")
            if not raw:
                return today, 0, Decimal("0")
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError
                stored_day = str(data.get("day") or "")
                if stored_day != today:
                    return today, 0, Decimal("0")
                calls = data.get("calls")
                if type(calls) is not int or calls < 0:
                    raise ValueError
                usd = Decimal(str(data.get("usd")))
                if not usd.is_finite() or usd < 0:
                    raise ValueError
            except (ValueError, TypeError, json.JSONDecodeError, InvalidOperation, ArithmeticError) as exc:
                raise ResearchError("LLM budget unreadable") from exc
            return today, calls, usd
        if self._day != today:
            self._day = today
            self._used_calls = 0
            self._used_usd = Decimal("0")
        return self._day, self._used_calls, self._used_usd

    def _store(self, day: str, calls: int, usd: Decimal) -> None:
        if self._journal is None:
            return
        self._journal.set_setting(
            "llm_budget",
            json.dumps({"day": day, "calls": calls, "usd": format(usd, "f")}, separators=(",", ":")),
        )


def warn_budget_short(research_seconds: int, daily_calls: int) -> None:
    """Log when the daily call cap cannot cover a full UTC day of screen+final runs."""
    if getattr(warn_budget_short, "fired", False):
        return
    warn_budget_short.fired = True
    if research_seconds <= 0:
        return
    # A healthy run charges screen + final (2). Fallback can charge both providers (4).
    # The warning uses 2. It does not raise the cap.
    needed = (Decimal(86400) / Decimal(research_seconds)) * Decimal(2)
    if needed > Decimal(daily_calls):
        log_event(
            "llm_budget_short",
            level=logging.WARNING,
            research_seconds=research_seconds,
            calls_cap=daily_calls,
            calls_needed=format(needed, "f"),
            assumed_calls_per_run=2,
            fallback_calls_per_run=4,
        )


class LlmAnalyst:
    """BUY / WAIT / SKIP only. Entry, stop, and target are attached later from candles."""

    def __init__(
        self,
        *,
        nvidia_key: str = "",
        nvidia_model: str = DEFAULT_NVIDIA_MODEL,
        openrouter_key: str = "",
        openrouter_model: str = "deepseek/deepseek-v4-pro",
        timeout: float = 40,
        daily_calls: int = 48,
        daily_usd: Decimal = Decimal("2"),
        usd_per_call: Decimal = Decimal("0.02"),
        cooldown: timedelta = PROVIDER_COOLDOWN,
        clock=None,
        transport=None,
    ):
        self.nvidia_key = nvidia_key
        self.nvidia_model = nvidia_model
        self.openrouter_key = openrouter_key
        self.openrouter_model = openrouter_model
        self.timeout = timeout
        self.cooldown = cooldown
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.budget = _Budget(daily_calls, daily_usd, usd_per_call)
        self._lock = threading.Lock()
        self._until: dict[str, datetime] = {}
        self._reason: dict[str, str] = {}
        self.http = httpx.Client(timeout=timeout, transport=transport)

    def bind_journal(self, journal) -> None:
        self.budget.bind(journal)

    def close(self) -> None:
        self.http.close()

    def configured(self) -> bool:
        return bool(self.nvidia_key or self.openrouter_key)

    def health(self) -> dict:
        now = self.clock()
        return {
            "nvidia": self._health("nvidia", self.nvidia_key, self.nvidia_model, now),
            "openrouter": self._health("openrouter", self.openrouter_key, self.openrouter_model, now),
        }

    def _health(self, name: str, key: str, model: str, now: datetime) -> dict:
        with self._lock:
            until = self._until.get(name)
            reason = self._reason.get(name, "")
        if not key:
            return {
                "configured": False,
                "healthy": False,
                "state": "unconfigured",
                "model": model,
                "detail": "",
                "until": None,
            }
        if until is not None and now < until:
            return {
                "configured": True,
                "healthy": False,
                "state": "cooldown",
                "model": model,
                "detail": reason,
                "until": until.isoformat(),
            }
        return {
            "configured": True,
            "healthy": True,
            "state": "ok",
            "model": model,
            "detail": "",
            "until": None,
        }

    def _cooling(self, name: str, now: datetime) -> bool:
        with self._lock:
            until = self._until.get(name)
        return until is not None and now < until

    def _cool(self, name: str, model: str, status: int) -> None:
        now = self.clock()
        until = now + self.cooldown
        reason = f"HTTP {status}"
        with self._lock:
            self._until[name] = until
            self._reason[name] = reason
        log_event(
            "llm_provider_unhealthy",
            level=logging.WARNING,
            provider=name,
            model=model,
            http_status=status,
            cooldown_seconds=int(self.cooldown.total_seconds()),
            until=until.isoformat(),
            detail=(
                f"{name} model {model} returned HTTP {status}. "
                f"Skipping this provider until {until.isoformat()}."
            ),
        )

    def research(self, symbols: tuple[str, ...], evidence: dict, max_age_minutes: int) -> list[Signal]:
        if not self.configured():
            raise ResearchError("LLM providers are not configured")
        universe = list(symbols)[:12]
        shortlist = self._screen(universe, evidence)
        return self._decide(shortlist or universe[:8], evidence, max_age_minutes)

    def _providers(self, purpose: str) -> list[tuple[str, str, str]]:
        nvidia = ("nvidia", NVIDIA_URL, self.nvidia_model) if self.nvidia_key else None
        router = ("openrouter", OPENROUTER_URL, self.openrouter_model) if self.openrouter_key else None
        if purpose == "screen":
            ordered = [nvidia, router]
        else:
            ordered = [router, nvidia]
        return [item for item in ordered if item is not None]

    def _complete(self, purpose: str, messages: list[dict]) -> list[str]:
        last: Exception | None = None
        now = self.clock()
        for name, url, model in self._providers(purpose):
            if self._cooling(name, now):
                last = LlmError(f"{name} cooling down after {self._reason.get(name, 'HTTP error')}")
                last.provider = name
                reason = self._reason.get(name, "")
                status_text = reason.removeprefix("HTTP ").strip()
                last.http_status = int(status_text) if status_text.isdigit() else None
                continue
            self.budget.charge()
            key = self.nvidia_key if name == "nvidia" else self.openrouter_key
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            body = {
                "model": model,
                "temperature": 0,
                "max_tokens": 700,
                "messages": messages,
            }
            if name == "nvidia":
                # Lightning is a reasoning model. Screening needs the JSON in content.
                body["chat_template_kwargs"] = {"enable_thinking": False}
            else:
                # DeepSeek V4 thinks by default and can spend this whole budget before content.
                body["reasoning"] = {"effort": "none"}
            try:
                response = self.http.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last = LlmError(f"{name} timed out")
                last.provider = name
                last.http_status = None
                last.__cause__ = exc
                continue
            except httpx.HTTPError as exc:
                last = LlmError(f"{name} transport failed")
                last.provider = name
                last.http_status = None
                last.__cause__ = exc
                continue
            if response.status_code in GONE:
                self._cool(name, model, response.status_code)
                last = LlmError(f"{name} HTTP {response.status_code}")
                last.provider = name
                last.http_status = response.status_code
                continue
            if response.status_code >= 400:
                last = LlmError(f"{name} HTTP {response.status_code}")
                last.provider = name
                last.http_status = response.status_code
                continue
            try:
                return _response_texts(response.json())
            except (LlmError, ValueError) as exc:
                last = exc
                continue
        raise ResearchError("LLM providers failed") from last

    def _parsed(self, purpose: str, messages: list[dict], parse):
        last: Exception | None = None
        for text in self._complete(purpose, messages):
            try:
                return parse(text)
            except ResearchError as exc:
                last = exc
        raise ResearchError("LLM response was not the expected JSON") from last

    def _screen(self, symbols: list[str], evidence: dict) -> list[str]:
        if not symbols or not self.nvidia_key:
            return symbols[:8]
        prompt = (
            "Pick at most 8 symbols worth a closer spot-crypto look. "
            "Return one JSON object {\"symbols\": [\"BTCUSDT\"]}. "
            "Do not include prices, sizes, or orders. Universe: "
            + ", ".join(symbols)
            + ". Evidence: "
            + _evidence_lines(symbols, evidence)
        )
        try:
            raw = self._parsed("screen", _messages(prompt), extract_symbols)
        except (ResearchError, ValueError):
            return symbols[:8]
        allowed = set(symbols)
        return [symbol for symbol in raw if symbol in allowed][:8]

    def _decide(self, symbols: list[str], evidence: dict, max_age_minutes: int) -> list[Signal]:
        prompt = (
            "You are the analysis step. Use only the evidence lines below. "
            "Return one JSON object with a decisions array. Each item has symbol, "
            "action (BUY, WAIT, or SKIP), confidence (0..1), source, and thesis. "
            "Do not include prices, stops, targets, or sizes. If evidence is weak, use WAIT or SKIP. "
            "Universe: "
            + ", ".join(symbols)
            + ". Evidence: "
            + _evidence_lines(symbols, evidence)
        )
        parsed = self._parsed("final", _messages(prompt), extract_decisions)
        decisions = parsed.get("decisions")
        # The final model may answer for the 12-symbol universe after the screen
        # kept fewer. Extra symbols are dropped. More than the universe cap is not.
        if not isinstance(decisions, list) or len(decisions) > 12:
            raise ResearchError("LLM decisions are not a bounded list")
        allowed = set(symbols)
        signals: list[Signal] = []
        dropped = 0
        for item in decisions:
            if not isinstance(item, dict):
                raise ResearchError("LLM decision is not an object")
            cleaned = strip_price_fields(item)
            symbol = str(cleaned.get("symbol", "")).upper().strip()
            if symbol not in allowed:
                dropped += 1
                continue
            item_evidence = evidence.get(symbol)
            if item_evidence is None:
                continue
            cleaned["as_of"] = item_evidence.as_of.isoformat()
            if len(str(cleaned.get("source") or "")) < 3:
                cleaned["source"] = "llm evidence snapshot"
            try:
                signal = Signal.parse(cleaned, allowed, max_age_minutes=max_age_minutes)
            except ValueError:
                if symbol not in allowed:
                    dropped += 1
                    continue
                raise
            if signal.action == "BUY" and not item_evidence.passed:
                signal = replace(signal, action="WAIT")
            signals.append(signal)
        if dropped:
            log_event("llm_decisions_dropped", count=dropped)
        if len({item.symbol for item in signals}) != len(signals):
            raise ResearchError("Duplicate symbols in LLM response")
        return signals


def extract_symbols(answer: str) -> list[str]:
    text = _FENCE.sub("", answer.strip())
    start, end = text.find("{"), text.rfind("}")
    blob = text[start : end + 1] if start >= 0 and end > start else text
    try:
        loaded = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ResearchError("LLM screen was not JSON") from exc
    symbols = loaded.get("symbols") if isinstance(loaded, dict) else None
    if not isinstance(symbols, list):
        raise ResearchError("LLM screen missing symbols")
    return [str(item).upper().strip() for item in symbols if str(item).strip()]


def _messages(prompt: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Reply with one JSON object and no markdown. Never output a price, size, "
                "stop, or target. Those are computed outside the model."
            ),
        },
        {"role": "user", "content": prompt},
    ]


def _evidence_lines(symbols: list[str], evidence: dict) -> str:
    lines = []
    for symbol in symbols:
        item = evidence.get(symbol)
        if item is None:
            lines.append(f"{symbol}: no snapshot")
            continue
        lines.append(
            f"{symbol}: regime={item.regime} rsi={item.rsi} momentum={item.momentum} "
            f"volatility={item.volatility} passed={item.passed} reason={item.reason}"
        )
    return "; ".join(lines)
