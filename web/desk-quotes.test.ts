import assert from "node:assert/strict";
import { test } from "node:test";
import { deskPrices, retainQuotes } from "./src/tradingdesk/quotes.ts";
import type { ReadInput } from "./src/tradingdesk/types.ts";

const now = Date.parse("2026-11-23T13:00:00Z");
const quote = { BTCUSDT: { last: "100.5", change_pct: "0" } };
const read = (data: unknown, issue: ReadInput["issue"] = null): ReadInput => ({ data, issue, received: new Date(now) });
function prices(offset: number, symbols = quote, error: string | null = null) {
  return deskPrices(read({ symbols, as_of: new Date(now + offset).toISOString(), error, limit_seconds: 30 }), now);
}

test("valid Binance snapshots preserve zero change and reject expired, missing and future source times", () => {
  assert.equal(prices(0).value?.[0].change, "0");
  assert.equal(prices(-30_001).state, "stale");
  assert.equal(prices(-100, quote, "rate_limit").state, "stale");
  assert.equal(prices(5001).state, "partial");
  assert.equal(prices(5000).freshness, "fresh");
  assert.equal(deskPrices(read({ symbols: {} }), now).state, "unavailable");
  assert.equal(deskPrices(read({ symbols: quote }, "unauthorized"), now).value, null);
});

test("the session trace uses only fresh distinct source timestamps", () => {
  const first = retainQuotes({}, prices(-2000));
  assert.equal(first.BTCUSDT.length, 1);
  assert.equal(retainQuotes(first, prices(-2000)).BTCUSDT.length, 1);
  assert.equal(retainQuotes(first, prices(-2500)).BTCUSDT.length, 1);
  const next = retainQuotes(first, prices(0, { BTCUSDT: { ...quote.BTCUSDT, last: "102" } }));
  assert.deepEqual(next.BTCUSDT.map(point => point.last), [100.5, 102]);
  assert.equal(retainQuotes(next, prices(0, quote, "rate_limit")), next);
});
