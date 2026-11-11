"""Read-only research bridge to Vibe-Trading HTTP sessions."""

from __future__ import annotations

import json
import re
import time

import httpx

from .models import Signal

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class ResearchError(RuntimeError):
    pass


def extract_decisions(answer: str) -> dict:
    """Accept a raw JSON object or a fenced object. Ignore any surrounding prose."""
    text = _FENCE.sub("", answer.strip())
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("decisions"), list):
            return parsed
    raise ResearchError("Vibe response not a decisions object")


class VibeResearch:
    def __init__(self, url: str, key: str = "", *, transport=None):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.http = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=20, transport=transport)

    def close(self) -> None:
        self.http.close()

    def research(self, symbols: tuple[str, ...], max_age_minutes: int, *, timeout_seconds=240) -> list[Signal]:
        prompt = (
            "Research current spot crypto markets using actual data tools. Do not place orders, "
            "call broker execution tools, or invent missing observations. Universe: "
            + ", ".join(symbols)
            + ". Return exactly one JSON object with a decisions array. Each item has "
            "symbol, action (BUY, WAIT, or SKIP), confidence (0..1), as_of (UTC ISO8601 "
            "timestamp of the latest tool observation), source (tool name and observation time), "
            "and thesis (short evidence-backed explanation). Do not include prices, sizes, or "
            "order instructions. If data is unavailable, return {\"decisions\":[]}."
        )
        try:
            session = self.http.post("/sessions", json={"title": "WARP scheduled research"})
            session.raise_for_status()
            session_id = session.json()["session_id"]
            sent = self.http.post(f"/sessions/{session_id}/messages", json={"content": prompt})
            sent.raise_for_status()
            attempt_id = sent.json()["attempt_id"]
            deadline = time.monotonic() + timeout_seconds
            answer = None
            evidence_tools = []
            while time.monotonic() < deadline:
                messages = self.http.get(f"/sessions/{session_id}/messages")
                messages.raise_for_status()
                for m in messages.json():
                    if m.get("role") == "assistant" and m.get("linked_attempt_id") == attempt_id:
                        answer = m.get("content")
                        evidence_tools = [e.get("tool") for e in m.get("tool_trail", [])
                                          if isinstance(e, dict) and e.get("status") == "ok"]
                if answer is not None:
                    break
                time.sleep(2)
            if answer is None:
                raise ResearchError("Vibe research timed out")
            result = extract_decisions(answer)
            if not isinstance(result, dict) or not isinstance(result.get("decisions"), list):
                raise ResearchError("Vibe response not a decisions object")
            if len(result["decisions"]) > len(symbols):
                raise ResearchError("Excess number of AI decisions")
            decisions = [Signal.parse(raw, set(symbols), max_age_minutes=max_age_minutes)
                         for raw in result["decisions"]]
            if any(s.action == "BUY" for s in decisions) and not set(evidence_tools).intersection({
                "get_market_data", "technical_indicators", "screen_market", "orderbook_depth",
            }):
                raise ResearchError("Actionable decision lacks successful market-data tool evidence")
            if len({s.symbol for s in decisions}) != len(decisions):
                raise ResearchError("Duplicate symbols in AI response")
            return decisions
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise ResearchError("Vibe response invalid or unavailable") from exc
