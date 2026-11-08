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


def do_sync(store, feed):
    parsed = parse_feed(feed, "TOSHKENT")
    run_id = store.start_run(1)
    return sync_packets(store, store.source, run_id, parsed.packets, "TOSHKENT")


def find_pkg(store, key):
    return next(p for p in store.packages if p["external_key"] == key)


class SyncTests(unittest.TestCase):
    def test_first_run_creates_everything(self):
        s = FakeStore()
        st = do_sync(s, SAMPLE)
        self.assertEqual((st.new_packages, st.new_departures), (7, 8))
        # 6 packets x 4 pax + ECO (3 prices, pax1 empty) ; NOPRICE has none -> 8 packets:
        # FAJR x2, HYATT, SAFAR, NOCAT, WEIRD = 6 full (24), ECO 3, NOPRICE 0
        self.assertEqual(st.snapshots, 27)
        self.assertTrue(all(x["price_usd"] is not None and x["price_usd"] > 0 for x in s.snapshots))

    def test_categories_normalised_via_map(self):
        s = FakeStore()
        do_sync(s, SAMPLE)
        self.assertEqual(find_pkg(s, "FAJR-14")["tier"], "lux")           # LUXE -> lux
        self.assertEqual(find_pkg(s, "HYATT-09")["tier"], "comfort")      # KOMFORT PLUS
        self.assertEqual(find_pkg(s, "SAFAR-7")["tier"], "econom")        # STANDART PLUS
        self.assertIsNone(find_pkg(s, "NOCAT-1")["tier"])                 # empty category
        self.assertIsNone(find_pkg(s, "WEIRD-1")["tier"])                 # city leaked into category: unmapped
        self.assertEqual(find_pkg(s, "WEIRD-1")["category_raw"], "NAMANGAN")

    def test_second_identical_run_writes_nothing(self):
        s = FakeStore()
        do_sync(s, SAMPLE)
        before = len(s.snapshots)
        st = do_sync(s, SAMPLE)
        self.assertEqual((st.new_packages, st.new_departures, st.snapshots, st.changes), (0, 0, 0, 0))
        self.assertEqual(len(s.snapshots), before)

    def test_price_change_writes_one_snapshot(self):
        s = FakeStore()
        do_sync(s, SAMPLE)
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][2]["prices"][0]["amount"] = "1,090 $"   # HYATT-09, 4 pax: 1190 -> 1090
        st = do_sync(s, feed)
        self.assertEqual(st.snapshots, 1)
        self.assertEqual(s.snapshots[-1]["price_usd"], 1090)
        self.assertEqual(s.snapshots[-1]["pax"], 4)

    def test_price_removed_writes_null_snapshot_and_first_null_does_not(self):
        s = FakeStore()
        do_sync(s, SAMPLE)
        self.assertFalse(any(x["price_usd"] is None for x in s.snapshots))  # ECO pax1 "" never recorded
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][3]["prices"][3]["amount"] = ""          # SAFAR-7 pax 1 disappears
        st = do_sync(s, feed)
        self.assertEqual(st.snapshots, 1)
        self.assertIsNone(s.snapshots[-1]["price_usd"])

    def test_departure_deactivated_after_two_missing_runs(self):
        s = FakeStore()
        do_sync(s, SAMPLE)
        feed = copy.deepcopy(SAMPLE)
        feed["packets"] = [p for p in feed["packets"] if p.get("family") != "HYATT-09"]
        do_sync(s, feed)
        dep = next(d for d in s.departures if d["package_id"] == find_pkg(s, "HYATT-09")["id"])
        self.assertEqual((dep["missing_runs"], dep["active"]), (1, True))   # one bad run is not enough
        do_sync(s, feed)
        self.assertEqual((dep["missing_runs"], dep["active"]), (2, False))
        do_sync(s, SAMPLE)                                                   # it came back
        self.assertEqual((dep["missing_runs"], dep["active"]), (0, True))

    def test_package_fields_updated_when_source_changes(self):
        s = FakeStore()
        do_sync(s, SAMPLE)
        feed = copy.deepcopy(SAMPLE)
        feed["packets"][2]["category"] = "LUX"
        st = do_sync(s, feed)
        self.assertEqual(st.updated_packages, 1)
        self.assertEqual(find_pkg(s, "HYATT-09")["tier"], "lux")


class RunTests(unittest.TestCase):
    def test_success_marks_run_and_source_ok(self):
        s = FakeStore()
        with mock.patch.object(runner, "fetch_feed", return_value=SAMPLE):
            runner.run_once(s, "http://x")
        self.assertEqual(s.runs[0]["status"], "ok")
        self.assertEqual(s.source_ok, 1)
        self.assertGreater(s.runs[0]["changes"], 0)

    def test_empty_feed_is_a_failed_run_and_writes_no_data(self):
        s = FakeStore()
        with mock.patch.object(runner, "fetch_feed", return_value={"packets": []}):
            runner.run_once(s, "http://x")
        self.assertEqual(s.runs[0]["status"], "failed")
        self.assertIn("0 packets", s.runs[0]["error"])
        self.assertEqual((s.source_failed, s.source_ok), (1, 0))
        self.assertEqual(s.packages, [])

    def test_network_error_is_a_failed_run(self):
        s = FakeStore()
        with mock.patch.object(runner, "fetch_feed", side_effect=RuntimeError("cannot fetch feed: Timeout")):
            runner.run_once(s, "http://x")
        self.assertEqual(s.runs[0]["status"], "failed")
        self.assertEqual(s.source_failed, 1)

    def test_disabled_source_is_skipped(self):
        s = FakeStore()
        s.source["enabled"] = False
        with mock.patch.object(runner, "fetch_feed", return_value=SAMPLE) as f:
            runner.run_once(s, "http://x")
        f.assert_not_called()
        self.assertEqual(s.runs, [])


if __name__ == "__main__":
    unittest.main()
