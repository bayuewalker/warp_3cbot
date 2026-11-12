import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from warp3c.config import Settings
from warp3c.threecommas import IntegrationError, ThreeCommas, dump_payload
from warp3c.vibe import VibeResearch, extract_decisions


def profile(code="p123"):
    return {
        "code": code, "status": "active", "demo": True,
        "exchange": {"code": "BINANCE", "name": "BINANCE-SPOT"},
        "balance": {"assets": {
            "USDT": {"free": "950.00", "used": "50.00", "total": "1000.00"},
            "BTC": {"free": "0", "used": "0", "total": "0"},
        }},
    }


def envelope(items, *, page, total, pages, limit=100):
    """Documented REST list shape: items plus pagination.page/limit/total/pages."""
    return {
        "items": items,
        "pagination": {"page": page, "limit": limit, "total": total, "pages": pages},
    }


class ContractTests(unittest.TestCase):
    def test_decimal_body_is_a_json_number(self):
        body = dump_payload({"amountPerTrade": Decimal("10.10"), "nested": {"take": Decimal("0.025")}})
        self.assertIn('"amountPerTrade":10.1', body)
        self.assertNotIn("__DEC__", body)
        self.assertEqual(json.loads(body)["nested"]["take"], 0.025)

    def test_official_pages_reach_account_and_live_strategies(self):
        def handler(req):
            self.assertEqual(req.url.params["limit"], "100")
            page = req.url.params["page"]
            if req.url.path.endswith("api_profiles"):
                if page == "1":
                    stubs = [{"code": f"other-{i:03d}"} for i in range(100)]
                    return httpx.Response(200, json=envelope(stubs, page=1, total=101, pages=2))
                if page == "2":
                    return httpx.Response(200, json=envelope([profile()], page=2, total=101, pages=2))
            if req.url.path.endswith("live"):
                if page == "1":
                    stubs = [{"strategyId": f"s{i:03d}"} for i in range(100)]
                    return httpx.Response(200, json=envelope(stubs, page=1, total=101, pages=2))
                if page == "2":
                    return httpx.Response(200, json=envelope([{"strategyId": "s-last"}], page=2, total=101, pages=2))
            raise AssertionError(req.url.path)

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        account = api.get_account("p123")
        self.assertEqual(account.free_usdt, Decimal("950.00"))
        self.assertTrue(account.demo)
        self.assertEqual(account.assets["BTC"].total, Decimal("0"))
        live = api.get_live()
        self.assertEqual(len(live), 101)
        self.assertEqual(live[-1]["strategyId"], "s-last")
        api.close()

    def test_empty_official_page_is_complete(self):
        def handler(req):
            pages = 0 if req.url.params["page"] == "1" else 99
            return httpx.Response(200, json=envelope([], page=1, total=0, pages=pages))

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        self.assertEqual(api.get_live(), [])
        with self.assertRaises(IntegrationError):
            api.get_account("p123")
        api.close()

    def test_inconsistent_pages_fail_closed(self):
        samples = [
            {"data": [profile()], "has_more": False},
            envelope([profile()], page=1, total=1, pages=1) | {"items": ["bad"]},
            envelope([], page=2, total=0, pages=0),
            envelope([{"code": "p123"}, {"code": "p123"}], page=1, total=2, pages=1),
            envelope([{"code": "only"}], page=1, total=150, pages=2),
            envelope([profile()], page=1, total=150, pages=1),
            {"items": [profile()], "pagination": {"page": "1", "limit": 100, "total": 1, "pages": 1}},
        ]

        def run(payload):
            api = ThreeCommas(
                "https://trade.3commas.io", "key", "secret",
                transport=httpx.MockTransport(lambda _req: httpx.Response(200, json=payload)),
            )
            try:
                with self.assertRaises(IntegrationError):
                    api.get_profiles()
            finally:
                api.close()

        for payload in samples:
            run(payload)

    def test_second_page_repeating_cursor_fails_closed(self):
        def handler(req):
            if req.url.params["page"] == "1":
                stubs = [{"code": f"c{i:03d}"} for i in range(100)]
                return httpx.Response(200, json=envelope(stubs, page=1, total=101, pages=2))
            return httpx.Response(200, json=envelope([profile()], page=1, total=101, pages=2))

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        with self.assertRaises(IntegrationError) as caught:
            api.get_profiles()
        self.assertIn("cursor", str(caught.exception).lower())
        api.close()

    def test_http_errors_do_not_return_a_partial_list(self):
        def handler(req):
            code = 500 if req.url.path.endswith("live") else 401
            return httpx.Response(code, json={"error": "nope"})

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        with self.assertRaises(IntegrationError) as denied:
            api.get_profiles()
        self.assertEqual(denied.exception.status_code, 401)
        self.assertFalse(denied.exception.uncertain)
        with self.assertRaises(IntegrationError) as failed:
            api.get_live()
        self.assertEqual(failed.exception.status_code, 500)
        self.assertTrue(failed.exception.uncertain)
        api.close()

    def test_post_is_not_retried_and_signed_body_matches(self):
        seen = []

        def handler(req):
            seen.append(req)
            message = "\n".join(["POST", req.url.path, req.headers["X-Timestamp"], "60000", req.content.decode()])
            import base64
            import hashlib
            import hmac
            expected = base64.b64encode(hmac.new(b"secret", message.encode(), hashlib.sha256).digest()).decode()
            self.assertEqual(req.headers["X-Signature"], expected)
            self.assertNotIn(b"profilesCodes", req.content)
            self.assertIn(b'"profiles":[', req.content)
            return httpx.Response(200, json={"strategyId": "abc", "clientId": "warp3c-" + "a" * 32})

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        payload = {
            "clientId": "warp3c-" + "a" * 32,
            "profiles": [{"code": "p123", "multiplier": 1}],
            "amountPerTrade": Decimal("25.50"),
        }
        result = api.create_strategy(payload)
        self.assertEqual(result["strategyId"], "abc")
        self.assertEqual(len(seen), 1)
        api.close()

    def test_fenced_vibe_json_is_accepted_and_duplicates_are_not(self):
        data = {"decisions": [{
            "symbol": "BTCUSDT", "action": "WAIT", "confidence": 0.4,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "source": "get_market_data", "thesis": "Range with no fresh impulse",
        }]}
        parsed = extract_decisions("Here you go:\n```json\n" + json.dumps(data) + "\n```")
        self.assertEqual(parsed["decisions"][0]["action"], "WAIT")

        def handler(req):
            if req.url.path == "/sessions" and req.method == "POST":
                return httpx.Response(201, json={"session_id": "s1"})
            if req.method == "POST":
                return httpx.Response(200, json={"attempt_id": "a1"})
            content = {"decisions": [
                {**data["decisions"][0], "action": "BUY", "confidence": 0.9},
                {**data["decisions"][0], "symbol": "BTCUSDT", "action": "SKIP", "confidence": 0.2},
            ]}
            return httpx.Response(200, json=[{
                "role": "assistant", "linked_attempt_id": "a1",
                "tool_trail": [{"tool": "get_market_data", "status": "ok"}],
                "content": json.dumps(content),
            }])

        vibe = VibeResearch("http://localhost:8899", transport=httpx.MockTransport(handler))
        from warp3c.vibe import ResearchError
        with self.assertRaises(ResearchError):
            vibe.research(("BTCUSDT",), 30)
        vibe.close()

    def test_default_mode_is_observe(self):
        self.assertEqual(Settings().mode, "observe")
        with self.assertRaises(ValueError):
            Settings(alert_webhook="http://example.com/hook")
