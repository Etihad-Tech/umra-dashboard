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
        self.assertEqual(f("17.09", TODAY), date(2027, 9, 17))     # passed 52 days ago (> 45) -> next year
        self.assertEqual(f("17.12", TODAY), date(2026, 12, 17))
        self.assertEqual(f("8.11", TODAY), date(2026, 11, 8))      # today still counts
        for bad in ["", None, "soon", "31.02", "99.99"]:
            self.assertIsNone(f(bad, TODAY), bad)

    def test_new_year(self):
        f = safo.parse_free_date
        self.assertEqual(f("02.01", date(2026, 12, 30)), date(2027, 1, 2))
        self.assertEqual(f("30.12", date(2026, 12, 30)), date(2026, 12, 30))
        self.assertEqual(f("30.12", date(2027, 1, 2)), date(2026, 12, 30))   # left 3 days ago: LAST December
        self.assertEqual(f("28.11", date(2027, 1, 2)), date(2026, 11, 28))   # 35 days ago, across New Year
        self.assertEqual(f("15.11", date(2027, 1, 2)), date(2027, 11, 15))   # 48 days ago: next one
        self.assertEqual(f("29.02", date(2026, 11, 8)), date(2028, 2, 29))   # leap day

    def test_a_recently_passed_departure_keeps_its_year(self):
        f = safo.parse_free_date
        self.assertEqual(f("08.11", date(2026, 11, 9)), date(2026, 11, 8))    # stale row the day after: not 2027
        self.assertEqual(f("25.09", date(2026, 11, 9)), date(2026, 9, 25))    # 45 days: still this year
        self.assertEqual(f("24.09", date(2026, 11, 9)), date(2027, 9, 24))    # 46 days: next year
        self.assertEqual(f("08.11", date(2026, 11, 9), grace_days=0), date(2027, 11, 8))

    def test_return_date_is_the_next_one_after_departure(self):
        rows = [{"id": 1, "section": "Lux", "date_out": "28.12", "date_return": "05.01", "price_4": 2000},
                {"id": 2, "section": "Eko", "date_out": "10.12", "date_return": "05.12", "price_4": 1000}]
        by = {p.family: p for p in safo.parse_rows(rows, date(2026, 11, 9)).packets}
        self.assertEqual((by["Lux"].date, by["Lux"].return_date), (date(2026, 12, 28), date(2027, 1, 5)))
        self.assertEqual(by["Eko"].return_date, date(2027, 12, 5))     # no grace for a return date

    def test_stale_row_across_midnight_makes_no_new_date_and_no_removal(self):
        rows = [{"id": 1, "section": "swiss10", "date_out": "08.11", "price_4": "1200"},
                {"id": 2, "section": "hyatt", "date_out": "20.11", "price_4": "1500"}]
        s = FakeStore()
        s.source = {**s.source, "company_id": "S", "kind": "safo_supabase"}
        for today in (date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 9)):
            with mock.patch.object(runner, "fetch_safo_rows", return_value=rows):
                runner.run_safo(s, "http://x", today)
        self.assertEqual(sorted(d["date"] for d in s.departures), ["2026-11-08", "2026-11-20"])
        self.assertEqual(s.events, [])


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


class RelativeGateway(unittest.TestCase):
    ORIGIN = "https://site.example"

    def page(self, url="'/gw'", key=None):
        return f"var SUPABASE_URL = {url}; var ANON_KEY = '{key or jwt('anon')}';"

    def test_relative_gateway_is_resolved_against_the_site_origin(self):
        url, key = safo.find_credentials([self.page()], self.ORIGIN)
        self.assertEqual(url, "https://site.example/gw")
        self.assertEqual(safo._jwt_role(key), "anon")

    def test_relative_path_needs_an_origin(self):
        with self.assertRaises(safo.SchemaError):
            safo.find_credentials([self.page()])

    def test_protocol_relative_or_external_urls_are_never_followed(self):
        for bad in ("'//evil.example/gw'", "'https://evil.example/gw'", "'/gw/../x?y=1'"):
            with self.assertRaises(safo.SchemaError, msg=bad):
                safo.find_credentials([self.page(bad)], self.ORIGIN)

    def test_service_role_is_still_refused_with_a_relative_url(self):
        with self.assertRaises(safo.SchemaError):
            safo.find_credentials([self.page(key=jwt("service_role"))], self.ORIGIN)


class SafoPaging(unittest.TestCase):
    class Resp:
        def __init__(self, data):
            self._data, self.status_code, self.content = data, 200, b"[]"

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def session(self, *pages):
        sess = mock.Mock()
        sess.get.side_effect = [self.Resp(p) for p in pages]
        return sess

    def test_reads_past_a_row_cap_in_id_order(self):
        pages = [[{"id": i} for i in range(a, b)] for a, b in ((1, 1001), (1001, 1201))] + [[]]
        sess = self.session(*pages)
        rows = safo.fetch_rows("https://x.supabase.co", "anon", session=sess, page_size=1000)
        self.assertEqual(len(rows), 1200)
        params = [c.kwargs["params"] for c in sess.get.call_args_list]
        self.assertEqual([p["offset"] for p in params], [0, 1000, 1200])
        self.assertTrue(all(p["order"] == "id.asc" and p["is_active"] == "eq.true" for p in params))

    def test_a_server_that_ignores_offset_does_not_loop(self):
        same = [{"id": 1}, {"id": 2}]
        sess = self.session(same, same)
        self.assertEqual(safo.fetch_rows("https://x.supabase.co", "anon", session=sess, page_size=1000), same)
        self.assertEqual(sess.get.call_count, 2)

    def test_a_server_that_ignores_limit_is_read_once(self):
        everything = [{"id": i} for i in range(5)]
        sess = self.session(everything)
        self.assertEqual(len(safo.fetch_rows("https://x.supabase.co", "anon", session=sess, page_size=2)), 5)
        self.assertEqual(sess.get.call_count, 1)

    def test_endless_pages_fail_loudly(self):
        sess = mock.Mock()
        sess.get.side_effect = lambda *a, **kw: self.Resp([{"no_id": 1}])
        with self.assertRaises(SchemaError):
            safo.fetch_rows("https://x.supabase.co", "anon", session=sess, page_size=1)


class TashkentToday(unittest.TestCase):
    def test_the_day_changes_at_19_utc(self):
        from datetime import datetime, timezone
        from collector.clock import today_tashkent
        self.assertEqual(today_tashkent(datetime(2026, 12, 31, 18, 59, tzinfo=timezone.utc)), date(2026, 12, 31))
        self.assertEqual(today_tashkent(datetime(2026, 12, 31, 19, 0, tzinfo=timezone.utc)), date(2027, 1, 1))

    def test_safo_run_uses_the_tashkent_date(self):
        s = FakeStore()
        s.source = {**s.source, "company_id": "S", "kind": "safo_supabase"}
        rows = [{"id": 1, "section": "Lux", "date_out": "17.09", "price_4": 2000}]
        with mock.patch.object(runner, "fetch_safo_rows", return_value=rows), \
             mock.patch.object(runner, "today_tashkent", return_value=date(2026, 9, 20)):
            runner.run_safo(s, "http://x")
        self.assertEqual([d["date"] for d in s.departures], ["2026-09-17"])
