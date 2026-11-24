"""Tests for the all-pets exploration mode."""
import time
import unittest
from unittest import mock

import app
import bot


def _trade(tid="t1"):
    return {"id": tid, "authorName": "@x",
            "lookingFor": [{"name": "Parrot", "type": "fr"}],
            "offering": [{"name": "Goat", "type": "fr"}]}


class FilterTests(unittest.TestCase):
    def test_inventory_mode_rejects_pets_you_do_not_own(self):
        _, reason = bot._evaluate_trade(_trade(), set())
        self.assertEqual(reason, "not in your inventory")

    def test_all_mode_bypasses_the_inventory_filter(self):
        _, reason = bot._evaluate_trade(_trade(), set(), ignore_inventory=True)
        self.assertNotEqual(reason, "not in your inventory",
                            "all-pets mode must not filter on inventory")

    def test_rank_trades_counts_inventory_rejections_only_in_inventory_mode(self):
        stats = {}
        bot.rank_trades([_trade()], set(), min_stars=1, stats=stats)
        self.assertGreater(stats.get("not in your inventory", 0), 0)

        stats_all = {}
        bot.rank_trades([_trade()], set(), min_stars=1, stats=stats_all,
                        ignore_inventory=True)
        self.assertEqual(stats_all.get("not in your inventory", 0), 0)

    def test_near_misses_does_not_hide_unowned_trades_in_all_mode(self):
        rows = bot.get_near_misses([_trade()], set(), limit=5, ignore_inventory=True)
        self.assertTrue(rows, "an evaluated trade should surface as a near miss")
        self.assertNotIn("not in your inventory", rows[0]["reason"])

    def test_owned_pets_unaffected(self):
        res, reason = bot._evaluate_trade(_trade(), {"Parrot Fly Ride"})
        self.assertNotEqual(reason, "not in your inventory")


class AppModeTests(unittest.TestCase):
    def setUp(self):
        # keep the scanner off the network
        self._orig_fetch = bot.fetch_one_batch
        self._orig_backfill = bot.backfill
        bot.fetch_one_batch = lambda: ([], 0)
        bot.backfill = lambda pages=2: []
        self.client = app.app.test_client()

    def tearDown(self):
        bot.fetch_one_batch = self._orig_fetch
        bot.backfill = self._orig_backfill
        with app.LOCK:
            app.STATE["running"] = False
            app.STATE["mode"] = "inventory"
        time.sleep(0.2)

    def test_find_all_mode_works_without_inventory(self):
        r = self.client.post("/find", data={"inventory": "[]", "mode": "all"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"Select at least one pet", r.data)
        self.assertEqual(app.STATE["mode"], "all")

    def test_find_inventory_mode_still_requires_a_pet(self):
        r = self.client.post("/find", data={"inventory": "[]"})
        self.assertIn(b"Select at least one pet", r.data)

    def test_status_reports_the_mode(self):
        with app.LOCK:
            app.STATE["mode"] = "all"
            app.STATE["payload"] = None
        d = self.client.get("/status").get_json()
        self.assertEqual(d.get("mode"), "all")

    def test_set_mode_switches(self):
        with app.LOCK:
            app.STATE["mode"] = "inventory"
            app.STATE["payload"] = None
        d = self.client.post("/set_mode", data={"mode": "all"}).get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["mode"], "all")
        self.assertEqual(app.STATE["mode"], "all")
        d = self.client.post("/set_mode", data={"mode": "nonsense"}).get_json()
        self.assertEqual(d["mode"], "inventory", "unknown values fall back")


if __name__ == "__main__":
    unittest.main()
