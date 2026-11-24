"""Tests that the app schedules the StarPets refresh on boot, without crawling."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app
import demand_refresh


class SchedulerTests(unittest.TestCase):
    def _run(self, env, startup_delay=0.2):
        calls = []
        with mock.patch.object(demand_refresh, "refresh_starpets",
                               side_effect=lambda **kw: calls.append(kw) or False), \
             mock.patch.object(demand_refresh, "refresh_amvgg",
                               side_effect=lambda **kw: False), \
             mock.patch.dict(os.environ, env, clear=False):
            app.start_demand_refresh(interval=9999, starpets_interval=9999,
                                     startup_delay=startup_delay)
            time.sleep(startup_delay + 0.4)
        return calls

    def test_starpets_refresh_fires_on_startup(self):
        calls = self._run({"DEMAND_STARPETS_AUTOREFRESH": "1"})
        self.assertEqual(len(calls), 1, "starpets refresh should run once on boot")
        # the call goes through the TTL gate, not forced
        self.assertFalse(calls[0].get("force", False))

    def test_starpets_can_be_disabled(self):
        calls = self._run({"DEMAND_STARPETS_AUTOREFRESH": "0"})
        self.assertEqual(calls, [])

    def test_everything_can_be_disabled(self):
        calls = self._run({"DEMAND_AUTOREFRESH": "0"})
        self.assertEqual(calls, [])

    def test_reload_catalog_returns_variant_count(self):
        n = app.bot.reload_catalog()
        self.assertGreater(n, 1000)
        key = next(iter(app.bot.CATALOG))
        self.assertEqual(app.bot.CATALOG[key].get("_key"), key)


if __name__ == "__main__":
    unittest.main()
