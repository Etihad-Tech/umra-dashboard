import re
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from collector import hikmat
from collector import run as runner
from collector.mandarin import SchemaError
from tests.fakes import FakeStore

TODAY = date(2026, 11, 18)

PRICES = """<html><body><table><thead><tr><th>Paket</th><th>Narx (1 kishi)</th><th>Davomiyligi</th>
<th>Mehmonxona</th><th>Asosiy farqi</th></tr></thead><tbody>
<tr><td><a href="#a"><b>Alfa</b></a></td><td>1100 $ dan</td><td>13+ kun: Madina 2 + Makka 11+ kecha</td><td>Makka: 700 m</td><td>cheap</td></tr>
<tr><td><a href="#b"><b>Beta + Umra</b></a></td><td>1,550 $ dan</td><td>14 kecha: Istanbul 4 + Madina 3 + Makka 7</td><td>x</td><td>y</td></tr>
<tr><td><a href="#c"><b>Gamma</b></a></td><td>1700 $ dan</td><td>7 kecha: Madina 3 + Makka 4</td><td>Address 5*</td><td>lux</td></tr>
</tbody></table></body></html>"""

DATES = """<html><body><table><thead><tr><th>Oy</th><th>Kunlar</th></tr></thead><tbody>
<tr><td><b>noyabr 2026</b></td><td data-kunlar="2026-11-11,2026-11-18,2026-11-25">11, 18, 25</td></tr>
<tr><td><b>dekabr 2026</b></td><td data-kunlar="2026-12-02,2026-12-09">2, 9</td></tr>
</tbody></table></body></html>"""


class Nights(unittest.TestCase):
    def test_sum_of_cities(self):
        self.assertEqual(hikmat.parse_nights("13+ kun: Madina 2 + Makka 11+ kecha"), 13)
        self.assertEqual(hikmat.parse_nights("14 kecha: Istanbul 4 + Madina 3 + Makka 7"), 14)
        self.assertEqual(hikmat.parse_nights("7 kecha: Madina 3 + Makka 4"), 7)

    def test_fallbacks(self):
        self.assertEqual(hikmat.parse_nights("9 kecha"), 9)
        self.assertIsNone(hikmat.parse_nights(""))
        self.assertIsNone(hikmat.parse_nights("tez orada"))


class Parse(unittest.TestCase):
    def test_packages(self):
        pk = hikmat.parse_packages(PRICES)
        self.assertEqual([p["name"] for p in pk], ["Alfa", "Beta + Umra", "Gamma"])
        self.assertEqual([p["price"] for p in pk], [1100, 1550, 1700])
        self.assertEqual([p["nights"] for p in pk], [13, 14, 7])

    def test_departures_dedupe_and_sort(self):
        self.assertEqual(hikmat.parse_departures(DATES)[:2], [date(2026, 11, 11), date(2026, 11, 18)])
        self.assertEqual(len(hikmat.parse_departures(DATES)), 5)

    def test_packets_only_future_dates_price_for_four(self):
        r = hikmat.parse_pages(PRICES, DATES, TODAY)
        self.assertEqual(r.total, 3)
        self.assertEqual(len(r.packets), 3 * 4)                     # 4 dates on/after today (11.11 is dropped)
        self.assertTrue(all(p.date >= TODAY for p in r.packets))
        g = [p for p in r.packets if p.family == "Gamma"][0]
        self.assertEqual((g.category_raw, g.nights, g.prices), ("GAMMA", 7, {4: 1700}))
        self.assertEqual(g.return_date, date(2026, 11, 25))
        self.assertIsNone(g.sold_out)
        self.assertEqual(g.services["price_note"], "from")

    def test_no_price_rows_are_counted_not_used(self):
        html = PRICES.replace("1700 $ dan", "narxni so'rang")
        r = hikmat.parse_pages(html, DATES, TODAY)
        self.assertEqual(r.invalid, 1)
        self.assertNotIn("Gamma", {p.family for p in r.packets})

    def test_broken_pages_raise(self):
        with self.assertRaises(SchemaError):
            hikmat.parse_pages("<html>no tables</html>", DATES, TODAY)
        with self.assertRaises(SchemaError):
            hikmat.parse_pages(PRICES, "<table><tr><td>no dates</td></tr></table>", TODAY)
        with self.assertRaises(SchemaError):                       # every date is in the past
            hikmat.parse_pages(PRICES, DATES, date(2027, 6, 1))
        with self.assertRaises(SchemaError):                       # more than half the packages without a price
            hikmat.parse_pages(re.sub(r"\d[\d,]* \$ dan", "so'rang", PRICES), DATES, TODAY)

    def test_huge_page_rejected(self):
        with self.assertRaises(SchemaError):
            hikmat.parse_packages("x" * (hikmat.MAX_BYTES + 1))


class Real(unittest.TestCase):
    """Runs only when the real pages were saved in collector/samples/ (that folder is git-ignored)."""
    base = Path(__file__).resolve().parent.parent / "samples"

    @unittest.skipUnless((base / "hikmat-narxlar.html").exists() and (base / "hikmat-sanalar.html").exists(), "no samples")
    def test_saved_pages(self):
        r = hikmat.parse_pages((self.base / "hikmat-narxlar.html").read_text(encoding="utf-8"),
                               (self.base / "hikmat-sanalar.html").read_text(encoding="utf-8"), date(2026, 11, 18))
        self.assertGreaterEqual(r.total, 6)
        self.assertGreater(len(r.packets), 0)


class RunHikmat(unittest.TestCase):
    def setUp(self):
        self.s = FakeStore()
        self.s.source = {"id": 4, "company_id": "H", "kind": "hikmat_html", "enabled": True,
                         "config": {}, "consecutive_failures": 0, "down_since": None}

    def test_full_run_and_idempotence(self):
        with mock.patch.object(runner, "fetch_hikmat_pages", return_value=(PRICES, DATES)):
            runner.run_hikmat(self.s, "http://x", TODAY)
            first = len(self.s.snapshots)
            runner.run_hikmat(self.s, "http://x", TODAY)
        self.assertEqual(self.s.runs[0]["status"], "ok")
        self.assertEqual(first, 3 * 4)                           # 3 packages x 4 future dates, one price (4 people) each
        self.assertEqual(len(self.s.snapshots), first)           # nothing changed -> nothing written
        self.assertTrue(all(x["pax"] == 4 for x in self.s.snapshots))
        self.assertEqual(self.s.events, [])                      # the very first run only fills the database

    def test_price_change_is_synced(self):
        with mock.patch.object(runner, "fetch_hikmat_pages", return_value=(PRICES, DATES)):
            runner.run_hikmat(self.s, "http://x", TODAY)
        cheaper = PRICES.replace("1700 $ dan", "1650 $ dan")
        with mock.patch.object(runner, "fetch_hikmat_pages", return_value=(cheaper, DATES)):
            runner.run_hikmat(self.s, "http://x", TODAY)
        self.assertEqual(len([x for x in self.s.snapshots if x["price_usd"] == 1650]), 4)

    def test_broken_site_is_failed_run(self):
        with mock.patch.object(runner, "fetch_hikmat_pages", return_value=("<html></html>", DATES)):
            runner.run_hikmat(self.s, "http://x", TODAY)
        self.assertEqual(self.s.runs[0]["status"], "failed")
        self.assertEqual(self.s.source_failed, 1)
        self.assertEqual(self.s.snapshots, [])


if __name__ == "__main__":
    unittest.main()
