"""Tests for the profit summary and the full-fidelity expired archive."""
import time
import unittest

import app
import bot
import opportunities
import scan_core


def _dead_trade(tid):
    return {"id": tid, "authorName": "@t",
            "lookingFor": [{"name": "Parrot", "type": "fr"}],
            "offering": [{"name": "Goat", "type": "fr"}],
            "_seen_at": time.time() - 400}      # past the TTL


class ProfitSummaryTests(unittest.TestCase):
    def test_only_qualified_trades_at_or_above_ten(self):
        rows = [{"profit_usd": 25.0, "qualified": True},
                {"profit_usd": 12.5, "qualified": True},
                {"profit_usd": 4.0, "qualified": True},
                {"profit_usd": 99.0, "qualified": False},
                {"profit_usd": None, "qualified": True}]
        s = scan_core._profit_summary(rows)
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["total"], 37.5)
        self.assertEqual(s["best"], 25.0)
        self.assertEqual(s["threshold"], 10.0)

    def test_exactly_ten_counts(self):
        s = scan_core._profit_summary([{"profit_usd": 10.0, "qualified": True}])
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["total"], 10.0)

    def test_nine_ninety_nine_does_not(self):
        s = scan_core._profit_summary([{"profit_usd": 9.99, "qualified": True}])
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["total"], 0)

    def test_empty_is_safe(self):
        self.assertEqual(scan_core._profit_summary([]),
                         {"threshold": 10.0, "count": 0, "total": 0, "best": 0.0})


class ArchiveFidelityTests(unittest.TestCase):
    def setUp(self):
        self._saved = (dict(app.STATE["trades"]), app.STATE["archive"],
                       app.STATE["payload"], app.STATE["inventory"],
                       app.STATE["target_pet"])
        app.STATE["trades"] = {"a": _dead_trade("a")}
        app.STATE["archive"] = []
        app.STATE["inventory"] = set()
        app.STATE["target_pet"] = ""
        app.STATE["payload"] = {
            "results": [{
                "trade_id": "a", "author": "@t", "give": ["Parrot Fly Ride"],
                "receive": ["Goat Fly Ride"], "give_usd": 10.0, "recv_usd": 35.0,
                "profit_usd": 25.0, "profit_pct": 250.0, "qualified": True,
                "give_frost": 1.0, "recv_frost": 2.0, "stars": 5, "sellability": 1.0,
                "exit_advice": {"decision": "Sell", "reason": "liquid",
                                "sell_now_net": 35.0, "demand_passed": True},
                "item_demand": {"Goat Fly Ride": {
                    "median_daily": 9, "signal": {"label": "amvgg high", "level": "High"}}},
            }],
            "near_misses": [], "target_trades": [],
        }
        with app.LOCK:
            self.payload = scan_core.refresh(bot, opportunities, app.STATE)

    def tearDown(self):
        trades, archive, payload, inv, target = self._saved
        app.STATE["trades"] = trades
        app.STATE["archive"] = archive
        app.STATE["payload"] = payload
        app.STATE["inventory"] = inv
        app.STATE["target_pet"] = target

    def entry(self):
        self.assertTrue(app.STATE["archive"], "expected an archived trade")
        return app.STATE["archive"][0]

    def test_archive_keeps_prices(self):
        e = self.entry()
        self.assertEqual(e["give_usd"], 10.0)
        self.assertEqual(e["recv_usd"], 35.0)

    def test_archive_keeps_profit_and_frost(self):
        e = self.entry()
        self.assertEqual(e["profit_usd"], 25.0)
        self.assertEqual(e["give_frost"], 1.0)
        self.assertEqual(e["recv_frost"], 2.0)

    def test_archive_keeps_advice_and_demand(self):
        e = self.entry()
        self.assertEqual(e["exit_advice"]["decision"], "Sell")
        self.assertEqual(e["demand"]["Goat Fly Ride"]["label"], "amvgg high")

    def test_expired_summary_uses_same_floor(self):
        self.assertEqual(self.payload["expired_summary"]["total"], 25.0)
        self.assertEqual(self.payload["expired_summary"]["count"], 1)

    def test_payload_exposes_both_summaries(self):
        self.assertIn("profit_summary", self.payload)
        self.assertIn("expired_summary", self.payload)
        self.assertIsInstance(self.payload["expired"], list)


if __name__ == "__main__":
    unittest.main()
