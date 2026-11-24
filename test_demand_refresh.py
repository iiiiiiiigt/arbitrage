"""Tests for the demand refresh TTL logic and failure handling."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

import demand_refresh as dr


class AgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "d.json")

    def write(self, generated_at=None, raw=None):
        if raw is not None:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(raw)
            return
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": generated_at, "pets": []}, f)

    def test_missing_file_has_no_age_and_is_due(self):
        self.assertIsNone(dr.age_hours(self.path))
        self.assertTrue(dr.is_due(self.path, 12))

    def test_iso_generated_at_wins_over_mtime(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        self.write(generated_at=old)
        age = dr.age_hours(self.path)
        self.assertGreater(age, 29)
        self.assertTrue(dr.is_due(self.path, 12))

    def test_fresh_timestamp_is_not_due(self):
        now = datetime.now(timezone.utc).isoformat()
        self.write(generated_at=now)
        self.assertLess(dr.age_hours(self.path), 1)
        self.assertFalse(dr.is_due(self.path, 12))

    def test_numeric_timestamp_supported(self):
        self.write(generated_at=time.time() - 5 * 3600)
        self.assertAlmostEqual(dr.age_hours(self.path), 5, places=0)

    def test_unparsable_file_falls_back_to_mtime(self):
        self.write(raw="not json at all")
        age = dr.age_hours(self.path)
        self.assertIsNotNone(age)
        self.assertLess(age, 1)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "amvgg.json")
        self._orig_path = dr.AMVGG_PATH
        self._orig_ttl = dr.AMVGG_TTL_HOURS
        dr.AMVGG_PATH = self.path
        dr.AMVGG_TTL_HOURS = 12
        self.calls = 0

    def tearDown(self):
        dr.AMVGG_PATH = self._orig_path
        dr.AMVGG_TTL_HOURS = self._orig_ttl

    def fetcher(self, print_summary=False):
        self.calls += 1
        return {"pets": [{"name": "X"}]}

    def test_skips_when_fresh(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "pets": []}, f)
        self.assertFalse(dr.refresh_amvgg(fetcher=self.fetcher))
        self.assertEqual(self.calls, 0)

    def test_fetches_when_forced(self):
        self.assertTrue(dr.refresh_amvgg(force=True, fetcher=self.fetcher))
        self.assertEqual(self.calls, 1)

    def test_fetches_when_stale(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"generated_at":
                       (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
                       "pets": []}, f)
        self.assertTrue(dr.refresh_amvgg(fetcher=self.fetcher))
        self.assertEqual(self.calls, 1)

    def test_failure_is_not_fatal(self):
        def boom(print_summary=False):
            raise RuntimeError("network down")
        self.assertFalse(dr.refresh_amvgg(force=True, fetcher=boom))

    def test_fetcher_return_shape_is_checked(self):
        # a short/empty payload must not crash the caller
        self.assertFalse(dr.refresh_amvgg(force=True, fetcher=lambda print_summary=False: {}))

    def test_refresh_due_never_touches_starpets_by_default(self):
        """The default path must not start a ~40k request crawl."""
        touched = []
        original = dr.refresh_starpets
        dr.refresh_starpets = lambda **kw: touched.append(kw) or True
        try:
            dr.refresh_due(force=True, fetcher=self.fetcher)
            self.assertEqual(touched, [])
            dr.refresh_due(force=True, include_starpets=True, fetcher=self.fetcher)
            self.assertEqual(len(touched), 1)
        finally:
            dr.refresh_starpets = original


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = dr.LOCK_PATH
        dr.LOCK_PATH = os.path.join(self.tmp, "x.lock")

    def tearDown(self):
        dr.LOCK_PATH = self._orig
        try:
            os.remove(dr.LOCK_PATH)
        except OSError:
            pass

    def test_no_lock_means_not_active(self):
        self.assertFalse(dr._lock_active())

    def test_acquire_then_release(self):
        self.assertTrue(dr._acquire_lock())
        self.assertTrue(dr._lock_active())
        self.assertFalse(dr._acquire_lock())      # second holder is refused
        dr._release_lock()
        self.assertFalse(dr._lock_active())

    def test_stale_lock_is_ignored(self):
        with open(dr.LOCK_PATH, "w") as f:
            f.write("1234")
        old = time.time() - (dr.LOCK_MAX_AGE + 60)
        os.utime(dr.LOCK_PATH, (old, old))
        self.assertFalse(dr._lock_active())
        self.assertTrue(dr._acquire_lock())


class GuardTests(unittest.TestCase):
    """A failed crawl must never destroy the previous dataset."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._lock = dr.LOCK_PATH
        self._sp = dr.STARPETS_PATH
        dr.LOCK_PATH = os.path.join(self.tmp, "l.lock")
        dr.STARPETS_PATH = os.path.join(self.tmp, "demand.json")
        import scrape_starpets as ss
        self.ss = ss
        self._run = ss.run
        self._cat = ss.CATALOG_PATH
        ss.run = lambda args: None

    def tearDown(self):
        self.ss.run = self._run
        self.ss.CATALOG_PATH = self._cat
        dr.LOCK_PATH = self._lock
        dr.STARPETS_PATH = self._sp

    def test_empty_catalog_is_refused(self):
        empty = os.path.join(self.tmp, "empty.json")
        with open(empty, "w") as f:
            json.dump({}, f)
        self.ss.CATALOG_PATH = empty
        self.assertFalse(dr.refresh_starpets(force=True))
        self.assertFalse(os.path.exists(dr.STARPETS_PATH),
                         "must not write a demand file from an empty catalog")

    def test_existing_demand_survives_a_failed_run(self):
        with open(dr.STARPETS_PATH, "w") as f:
            json.dump({"scope": "x", "variants": {"keep": 1}}, f)
        empty = os.path.join(self.tmp, "empty.json")
        with open(empty, "w") as f:
            json.dump({}, f)
        self.ss.CATALOG_PATH = empty
        self.assertFalse(dr.refresh_starpets(force=True))
        with open(dr.STARPETS_PATH) as f:
            self.assertIn("keep", json.load(f)["variants"])

    def test_lock_released_after_failure(self):
        empty = os.path.join(self.tmp, "empty.json")
        with open(empty, "w") as f:
            json.dump({}, f)
        self.ss.CATALOG_PATH = empty
        dr.refresh_starpets(force=True)
        self.assertFalse(os.path.exists(dr.LOCK_PATH), "lock must be released")


if __name__ == "__main__":
    unittest.main()
