import base64
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from collector import run as runner
from collector import safo
from collector.mandarin import SchemaError
from tests.fakes import FakeStore

ROWS = json.loads((Path(__file__).parent / "fixtures" / "safo_sample.json").read_text())
TODAY = date(2026, 11, 8)


def jwt(role):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{b({'alg': 'HS256'})}.{b({'role': role, 'ref': 'x'})}.{'s' * 20}"


class FreeDate(unittest.TestCase):
    def test_formats(self):
        f = safo.parse_free_date
        self.assertEqual(f("18.11.2026", TODAY), date(2026, 11, 18))
        self.assertEqual(f("18.11.26", TODAY), date(2026, 11, 18))
        self.assertEqual(f("17.09", TODAY), date(2027, 9, 17))     # already passed this year -> next year
        self.assertEqual(f("17.12", TODAY), date(2026, 12, 17))
        self.assertEqual(f("8.11", TODAY), date(2026, 11, 8))      # today still counts
        for bad in ["", None, "soon", "31.02", "99.99"]:
            self.assertIsNone(f(bad, TODAY), bad)

    def test_new_year(self):
        f = safo.parse_free_date
        self.assertEqual(f("02.01", date(2026, 12, 30)), date(2027, 1, 2))
        self.assertEqual(f("30.12", date(2026, 12, 30)), date(2026, 12, 30))
        self.assertEqual(f("30.12", date(2027, 1, 2)), date(2027, 12, 30))
        self.assertEqual(f("29.02", date(2026, 11, 8)), date(2028, 2, 29))   # leap day


class ParseRows(unittest.TestCase):
    def test_counts_and_skips(self):
        r = safo.parse_rows(ROWS, TODAY)
        self.assertEqual((r.total, r.invalid, r.duplicates, len(r.packets)), (7, 2, 1, 4))

    def test_no_one_person_price_and_missing_prices(self):
        r = safo.parse_rows(ROWS, TODAY)
        p = r.packets[0]
        self.assertEqual(p.prices, {2: 2490, 3: 2390, 4: 2290})
        self.assertNotIn(1, p.prices)
        eko = next(x for x in r.packets if x.family == "Ekonom")
        self.assertEqual(eko.prices, {2: 990, 3: None, 4: None})

    def test_dates_and_sold_out(self):
        r = safo.parse_rows(ROWS, TODAY)
        by = {(p.family, p.date): p for p in r.packets}
        self.assertEqual(by[("Lux", date(2026, 12, 17))].return_date, date(2026, 12, 27))
        self.assertEqual(by[("Ekonom", date(2026, 12, 29))].return_date, date(2027, 1, 8))  # crosses New Year
        self.assertTrue(by[("Komfort", date(2027, 1, 5))].sold_out)
        self.assertIsNone(by[("Komfort", date(2027, 1, 5))].city)

    def test_failures(self):
        for bad in [[], None, {}, [{"x": 1}, {"y": 2}, ROWS[0]]]:
            with self.assertRaises(SchemaError):
                safo.parse_rows(bad, TODAY)


class Credentials(unittest.TestCase):
    URL = "https://abcdefghijklmnopqrst.supabase.co"

    def test_finds_anon_key(self):
        anon = jwt("anon")
        self.assertEqual(safo.find_credentials([f'const u="{self.URL}", k="{anon}"']), (self.URL, anon))

    def test_never_uses_service_role(self):
        with self.assertRaises(SchemaError):
            safo.find_credentials([f'{self.URL} {jwt("service_role")}'])

    def test_prefers_anon_when_both_present(self):
        anon = jwt("anon")
        _, key = safo.find_credentials([f'{self.URL} {jwt("service_role")} {anon}'])
        self.assertEqual(key, anon)

    def test_missing_parts(self):
        with self.assertRaises(SchemaError):
            safo.find_credentials(["nothing here"])
        with self.assertRaises(SchemaError):
            safo.find_credentials([self.URL])


class RunSafo(unittest.TestCase):
    def setUp(self):
        self.s = FakeStore()
        self.s.source = {"id": 2, "company_id": "S", "kind": "safo_supabase", "enabled": True,
                         "config": {}, "consecutive_failures": 0, "down_since": None}

    def test_full_run_and_idempotence(self):
        with mock.patch.object(runner, "fetch_safo_rows", return_value=ROWS):
            runner.run_safo(self.s, "http://x", TODAY)
            first = len(self.s.snapshots)
            runner.run_safo(self.s, "http://x", TODAY)
        self.assertEqual(self.s.runs[0]["status"], "ok")
        self.assertEqual(len(self.s.snapshots), first)           # nothing changed -> nothing written
        self.assertEqual(first, 3 + 3 + 3 + 1)                   # Lux x2, Komfort, Ekonom(1 price)
        self.assertTrue(all(x["pax"] in (2, 3, 4) for x in self.s.snapshots))
        sold = [d for d in self.s.departures if d.get("sold_out")]
        self.assertEqual(len(sold), 1)

    def test_sold_out_flip_is_synced(self):
        with mock.patch.object(runner, "fetch_safo_rows", return_value=ROWS):
            runner.run_safo(self.s, "http://x", TODAY)
        flipped = [dict(r) for r in ROWS]
        flipped[2]["is_sold_out"] = False
        with mock.patch.object(runner, "fetch_safo_rows", return_value=flipped):
            runner.run_safo(self.s, "http://x", TODAY)
        self.assertFalse(any(d.get("sold_out") for d in self.s.departures))

    def test_broken_site_is_failed_run(self):
        with mock.patch.object(runner, "fetch_safo_rows", side_effect=SchemaError("public anon key not found")):
            runner.run_safo(self.s, "http://x", TODAY)
        self.assertEqual(self.s.runs[0]["status"], "failed")
        self.assertEqual(self.s.source_failed, 1)


if __name__ == "__main__":
    unittest.main()
