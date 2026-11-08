import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from collector import run as runner
from collector.mandarin import parse_feed
from collector.sync import sync_packets
from tests.fakes import FakeStore

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "mandarin_sample.json").read_text())
SAFO = json.loads((Path(__file__).parent / "fixtures" / "safo_sample.json").read_text())


def run_feed(store, feed):
    """One full Mandarin run, like the scheduler does it (seed detection included)."""
    with mock.patch.object(runner, "fetch_feed", return_value=feed):
        runner.run_once(store, "http://x")


def types(store):
    return [e["type"] for e in store.events]


class Seed(unittest.TestCase):
    def test_first_run_is_a_seed_and_creates_no_events(self):
        s = FakeStore()
        run_feed(s, SAMPLE)
        self.assertEqual(s.events, [])
        self.assertGreater(len(s.snapshots), 0)

    def test_second_identical_run_creates_no_events(self):
        s = FakeStore()
        run_feed(s, SAMPLE); run_feed(s, SAMPLE)
        self.assertEqual(s.events, [])

    def test_a_failed_first_run_does_not_end_the_seed(self):
        s = FakeStore()
        run_feed(s, {"packets": []})          # failed run
        run_feed(s, SAMPLE)                   # first SUCCESSFUL run: still a seed
        self.assertEqual(s.events, [])
        run_feed(s, SAMPLE)
        self.assertEqual(s.events, [])


class PriceEvents(unittest.TestCase):
    def setUp(self):
        self.s = FakeStore()
        run_feed(self.s, SAMPLE)

    def test_price_down_has_old_and_new(self):
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][2]["prices"][0]["amount"] = "1,090 $"      # HYATT-09 4 pax 1190 -> 1090
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["price_down"])
        e = self.s.events[0]
        self.assertEqual((e["old"], e["new"]), ({"pax": 4, "price_usd": 1190}, {"pax": 4, "price_usd": 1090}))
        self.assertEqual(e["company_id"], "F")
        self.assertTrue(e["package_id"] and e["departure_id"])
        self.assertFalse(e["is_manual"])

    def test_price_up(self):
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][2]["prices"][0]["amount"] = "1,290 $"
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["price_up"])

    def test_price_disappearing_or_appearing_is_not_a_price_event(self):
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][3]["prices"][3]["amount"] = ""             # price removed
        run_feed(self.s, feed)
        self.assertEqual(self.s.events, [])
        feed["packets"][3]["prices"][3]["amount"] = "1,990 $"      # and back
        run_feed(self.s, feed)
        self.assertEqual(self.s.events, [])

    def test_unchanged_prices_make_no_events(self):
        run_feed(self.s, SAMPLE)
        self.assertEqual(self.s.events, [])


class StructureEvents(unittest.TestCase):
    def setUp(self):
        self.s = FakeStore()
        run_feed(self.s, SAMPLE)

    def test_new_package_is_announced_once_not_per_date(self):
        feed = copy.deepcopy(SAMPLE)
        for d in ("2026-12-30", "2027-01-05"):
            extra = copy.deepcopy(feed["packets"][2]); extra["family"] = "NEWPKG-1"; extra["date_key"] = d
            feed["packets"].append(extra)
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["new_package"])
        self.assertEqual(self.s.events[0]["new"], {"departures": 2})

    def test_new_date_on_existing_package(self):
        feed = copy.deepcopy(SAMPLE)
        extra = copy.deepcopy(feed["packets"][2]); extra["date_key"] = "2027-02-01"
        feed["packets"].append(extra)
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["new_departure"])
        self.assertIsNotNone(self.s.events[0]["departure_id"])

    def test_removed_only_after_two_missed_runs(self):
        feed = copy.deepcopy(SAMPLE)
        feed["packets"] = [p for p in feed["packets"] if p.get("family") != "HYATT-09"]
        run_feed(self.s, feed)
        self.assertEqual(self.s.events, [])                       # one flaky run is not enough
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["removed"])
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["removed"])              # announced once


class ManualEntries(unittest.TestCase):
    def setUp(self):
        self.s = FakeStore()
        run_feed(self.s, SAMPLE)

    def _hyatt_dep(self):
        pkg = next(p for p in self.s.packages if p["external_key"] == "HYATT-09")
        return next(d for d in self.s.departures if d["package_id"] == pkg["id"])

    def test_manual_departure_is_never_removed(self):
        pkg = self.s.packages[0]
        self.s.insert_departures([{"package_id": pkg["id"], "date": "2030-01-01", "is_manual": True}])
        for _ in range(4):
            run_feed(self.s, SAMPLE)
        d = next(d for d in self.s.departures if d["date"] == "2030-01-01")
        self.assertTrue(d["active"])
        self.assertEqual(self.s.events, [])

    def test_different_site_price_next_to_manual_price_is_a_conflict(self):
        dep = self._hyatt_dep()
        self.s.manual_prices[(dep["id"], 4)] = 1000
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][2]["prices"][0]["amount"] = "1,090 $"
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["manual_conflict", "price_down"])
        e = self.s.events[0]
        self.assertEqual((e["old"], e["new"]), ({"pax": 4, "price_usd": 1000}, {"pax": 4, "price_usd": 1090}))

    def test_same_site_price_as_manual_is_not_a_conflict(self):
        dep = self._hyatt_dep()
        self.s.manual_prices[(dep["id"], 4)] = 1090
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][2]["prices"][0]["amount"] = "1,090 $"
        run_feed(self.s, feed)
        self.assertEqual(types(self.s), ["price_down"])

    def test_unchanged_site_price_makes_no_conflict(self):
        dep = self._hyatt_dep()
        self.s.manual_prices[(dep["id"], 4)] = 1000
        run_feed(self.s, SAMPLE)
        self.assertEqual(self.s.events, [])


class SoldOutEvents(unittest.TestCase):
    def test_sold_out_and_back_on_sale(self):
        s = FakeStore()
        s.source = {"id": 2, "company_id": "S", "kind": "safo_supabase", "enabled": True, "config": {},
                    "consecutive_failures": 0, "down_since": None}
        from datetime import date
        with mock.patch.object(runner, "fetch_safo_rows", return_value=SAFO):
            runner.run_safo(s, "http://x", date(2026, 11, 8))      # seed
        flipped = [dict(r) for r in SAFO]; flipped[0]["is_sold_out"] = True; flipped[2]["is_sold_out"] = False
        with mock.patch.object(runner, "fetch_safo_rows", return_value=flipped):
            runner.run_safo(s, "http://x", date(2026, 11, 8))
        self.assertEqual(sorted(types(s)), ["back_on_sale", "sold_out"])


class SourceHealth(unittest.TestCase):
    def fail(self, s):
        with mock.patch.object(runner, "fetch_feed", side_effect=RuntimeError("cannot fetch feed: Timeout")):
            runner.run_once(s, "http://x")

    def test_source_down_exactly_once_at_the_third_failure(self):
        s = FakeStore()
        run_feed(s, SAMPLE)
        self.fail(s); self.fail(s)
        self.assertEqual(s.events, [])                            # 2 failures: still just a flaky site
        self.fail(s)
        self.assertEqual(types(s), ["source_down"])
        self.assertEqual(s.events[0]["company_id"], "F")
        self.fail(s); self.fail(s)
        self.assertEqual(types(s), ["source_down"])               # no repeats during the outage

    def test_source_recovered_only_after_a_real_outage(self):
        s = FakeStore()
        run_feed(s, SAMPLE)
        self.fail(s); self.fail(s)
        run_feed(s, SAMPLE)                                       # recovered after 2 failures: no outage happened
        self.assertEqual(s.events, [])
        self.fail(s); self.fail(s); self.fail(s)
        run_feed(s, SAMPLE)
        self.assertEqual(types(s), ["source_down", "source_recovered"])

    def test_error_text_is_short(self):
        s = FakeStore()
        run_feed(s, SAMPLE)
        with mock.patch.object(runner, "fetch_feed", side_effect=RuntimeError("x" * 5000)):
            for _ in range(3):
                runner.run_once(s, "http://x")
        self.assertLessEqual(len(s.events[0]["new"]["error"]), 200)


if __name__ == "__main__":
    unittest.main()
