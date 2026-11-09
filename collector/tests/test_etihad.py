import unittest
from datetime import date
from unittest import mock

from collector import etihad, run as runner
from collector.mandarin import SchemaError
from tests.fakes import FakeStore


def pkg(**kw):
    base = {"id": "jumeirah10", "name": "JUMEIRAH 10", "tier": "premium", "dates": "03.09.2026 – 09.01.2027",
            "days": 10, "nights": 9, "depday": "thu", "route": "TAS → MED + JED → TAS",
            "stay": [{"h": "madinah_x", "meals": "b2", "n": 4}],
            "bands": [{"from": "2026-09-03", "to": "2026-09-24",
                       "prices": {"dbl": 1990, "quad": 1750, "sngl": 2800, "trpl": 1890}},
                      {"from": "2026-09-25", "to": "2026-10-08",
                       "prices": {"dbl": 2090, "quad": 1850, "sngl": None, "trpl": 1990}}],
            "prices": {"dbl": 1990, "quad": 1750, "sngl": 2800, "trpl": 1890}}
    base.update(kw)
    return base


def feed(*pkgs):
    return {"ok": True, "v": 1, "packages": list(pkgs)}


class Parse(unittest.TestCase):
    def test_departures_are_the_flight_weekday_inside_bands(self):
        r = etihad.parse_packages(feed(pkg()))
        dates = [p.date for p in r.packets]
        self.assertEqual(dates, [date(2026, 9, 3), date(2026, 9, 10), date(2026, 9, 17), date(2026, 9, 24),
                                 date(2026, 10, 1), date(2026, 10, 8)])
        self.assertTrue(all(d.weekday() == 3 for d in dates))

    def test_prices_per_room_type_and_band(self):
        r = etihad.parse_packages(feed(pkg()))
        self.assertEqual(r.packets[0].prices, {4: 1750, 3: 1890, 2: 1990, 1: 2800})
        self.assertEqual(r.packets[-1].prices, {4: 1850, 3: 1990, 2: 2090, 1: None})

    def test_return_date_tier_and_no_city_or_sold_out(self):
        p = etihad.parse_packages(feed(pkg())).packets[0]
        self.assertEqual(p.return_date, date(2026, 9, 12))
        self.assertEqual((p.category_raw, p.city, p.sold_out, p.family), ("PREMIUM", None, None, "jumeirah10"))

    def test_without_weekday_only_band_start(self):
        r = etihad.parse_packages(feed(pkg(depday=None)))
        self.assertEqual([p.date for p in r.packets], [date(2026, 9, 3), date(2026, 9, 25)])

    def test_overlapping_bands_first_wins(self):
        bands = [{"from": "2026-09-03", "to": "2026-09-10", "prices": {"dbl": 1000}},
                 {"from": "2026-09-10", "to": "2026-09-17", "prices": {"dbl": 2000}}]
        r = etihad.parse_packages(feed(pkg(bands=bands)))
        self.assertEqual(len(r.packets), 3)
        self.assertEqual(r.duplicates, 1)
        self.assertEqual(next(p for p in r.packets if p.date == date(2026, 9, 10)).prices[2], 1000)

    def test_no_bands_uses_date_range_and_default_prices(self):
        r = etihad.parse_packages(feed(pkg(bands=[], dates="03.09.2026 – 17.09.2026")))
        self.assertEqual(len(r.packets), 3)
        self.assertEqual(r.packets[0].prices[4], 1750)

    def test_zero_or_missing_price_is_none(self):
        r = etihad.parse_packages(feed(pkg(bands=[{"from": "2026-09-03", "to": "2026-09-03",
                                                   "prices": {"dbl": 0, "quad": None}}]), pkg(id="priced")))
        self.assertEqual(r.packets[0].prices, {4: None, 3: None, 2: None, 1: None})

    def test_no_price_anywhere_is_failure(self):
        with self.assertRaises(SchemaError):
            etihad.parse_packages(feed(pkg(bands=[{"from": "2026-09-03", "to": "2026-09-03", "prices": {}}])))

    def test_bad_nights_skip_only_that_package(self):
        for bad in (0, -3, True, False, 12.5, "abc", 200):
            r = etihad.parse_packages(feed(pkg(id="bad", nights=bad), pkg(id="good"), pkg(id="good2")))
            self.assertEqual(r.invalid, 1, bad)
            self.assertEqual({p.family for p in r.packets}, {"good", "good2"}, bad)
            self.assertTrue(all(p.return_date > p.date for p in r.packets))

    def test_missing_or_numeric_text_nights_are_fine(self):
        r = etihad.parse_packages(feed(pkg(id="a", nights=None), pkg(id="b", nights="9"), pkg(id="c", nights=9.0)))
        by = {p.family: p for p in r.packets}
        self.assertEqual((by["a"].nights, by["a"].return_date), (None, None))
        self.assertEqual((by["b"].nights, by["c"].nights), (9, 9))
        self.assertEqual(r.invalid, 0)

    def test_bad_packages_are_counted(self):
        r = etihad.parse_packages(feed(pkg(), pkg(id="b"), {"name": "no id"}))
        self.assertEqual((r.total, r.invalid), (3, 1))

    def test_schema_errors(self):
        for bad in [None, [], {}, {"packages": "x"}, feed(), feed({"id": "a"}), feed({"x": 1}, {"y": 2}, pkg())]:
            with self.assertRaises(SchemaError, msg=str(bad)[:40]):
                etihad.parse_packages(bad)

    def test_absurd_band_is_rejected(self):
        with self.assertRaises(SchemaError):
            etihad.parse_packages(feed(pkg(bands=[{"from": "2020-01-01", "to": "2030-01-01", "prices": {}}])))


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.content = data, status, b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError("401")

    def json(self):
        return self._data


class Run(unittest.TestCase):
    def test_key_goes_in_header_not_url(self):
        session = mock.Mock()
        session.get.return_value = FakeResponse(feed(pkg()))
        runner.fetch_feed("https://x.test/api/packages", session, headers={"X-Api-Key": "KEY-123"})
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://x.test/api/packages")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], "KEY-123")
        self.assertNotIn("KEY-123", args[0])

    def test_error_message_never_contains_the_key(self):
        session = mock.Mock()
        session.get.return_value = FakeResponse({}, 401)
        with mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as cm:
                runner.fetch_feed("https://x.test/api/packages?x=1", session, retries=0,
                                  headers={"X-Api-Key": "SECRET123"})
        self.assertNotIn("SECRET123", str(cm.exception))

    def test_first_run_seeds_without_events_then_price_change_is_an_event(self):
        store = FakeStore()
        store.source.update(company_id="B", kind="etihad_showroom")
        with mock.patch.object(runner, "fetch_feed", return_value=feed(pkg())):
            runner.run_etihad(store, "https://x.test", "k")
            self.assertEqual(store.events, [])
            changed = pkg()
            changed["bands"][0]["prices"]["quad"] = 1700
            with mock.patch.object(runner, "fetch_feed", return_value=feed(changed)):
                runner.run_etihad(store, "https://x.test", "k")
        kinds = {e["type"] for e in store.events}
        self.assertEqual(kinds, {"price_down"})
        self.assertEqual({e["company_id"] for e in store.events}, {"B"})


if __name__ == "__main__":
    unittest.main()
