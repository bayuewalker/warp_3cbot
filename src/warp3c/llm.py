"""Slim analysis client. NVIDIA screens, OpenRouter writes the final JSON, either can fail over."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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


def _content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise LlmError("LLM response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LlmError("LLM response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        texts = [part.get("text", "") for part in content if isinstance(part, dict)]
        content = "".join(texts)
    if not isinstance(content, str) or not content.strip():
        raise LlmError("LLM response content is empty")
    return content


class _Budget:
    def __init__(self, calls: int, usd: Decimal, per_call: Decimal):
        self.calls = calls
        self.usd = usd
        self.per_call = per_call
        self._lock = threading.Lock()
        self._day = ""
        self._used_calls = 0
        self._used_usd = Decimal("0")

    def charge(self) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            if day != self._day:
                self._day = day
                self._used_calls = 0
                self._used_usd = Decimal("0")
            if self._used_calls >= self.calls or self._used_usd + self.per_call > self.usd:
                raise ResearchError("LLM daily cap reached")
            self._used_calls += 1
            self._used_usd += self.per_call


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

    def _complete(self, purpose: str, messages: list[dict]) -> str:
        last: Exception | None = None
        now = self.clock()
        for name, url, model in self._providers(purpose):
            if self._cooling(name, now):
                last = LlmError(f"{name} cooling down after {self._reason.get(name, 'HTTP error')}")
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
            try:
                response = self.http.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last = LlmError(f"{name} timed out")
                last.__cause__ = exc
                continue
            except httpx.HTTPError as exc:
                last = LlmError(f"{name} transport failed")
                last.__cause__ = exc
                continue
            if response.status_code in GONE:
                self._cool(name, model, response.status_code)
                last = LlmError(f"{name} HTTP {response.status_code}")
                continue
            if response.status_code >= 400:
                last = LlmError(f"{name} HTTP {response.status_code}")
                continue
            try:
                return _content(response.json())
            except (LlmError, ValueError) as exc:
                last = exc
                continue
        raise ResearchError("LLM providers failed") from last

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
            raw = extract_symbols(self._complete("screen", _messages(prompt)))
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
        parsed = extract_decisions(self._complete("final", _messages(prompt)))
        decisions = parsed.get("decisions")
        if not isinstance(decisions, list) or len(decisions) > len(symbols):
            raise ResearchError("LLM decisions are not a bounded list")
        signals: list[Signal] = []
        for item in decisions:
            if not isinstance(item, dict):
                raise ResearchError("LLM decision is not an object")
            cleaned = strip_price_fields(item)
            symbol = str(cleaned.get("symbol", "")).upper().strip()
            item_evidence = evidence.get(symbol)
            if item_evidence is None:
                continue
            cleaned["as_of"] = item_evidence.as_of.isoformat()
            if len(str(cleaned.get("source") or "")) < 3:
                cleaned["source"] = "llm evidence snapshot"
            signal = Signal.parse(cleaned, set(symbols), max_age_minutes=max_age_minutes)
            if signal.action == "BUY" and not item_evidence.passed:
                signal = replace(signal, action="WAIT")
            signals.append(signal)
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
