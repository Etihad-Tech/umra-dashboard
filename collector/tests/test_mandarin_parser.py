import json
import unittest
from datetime import date
from pathlib import Path

from collector.mandarin import SchemaError, parse_feed, parse_price

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "mandarin_sample.json").read_text())


class ParsePrice(unittest.TestCase):
    def test_formats(self):
        for raw, want in [("1,190 $", 1190), ("890 $", 890), ("1 050 $", 1050), ("$2,290", 2290),
                          ("1,190.00 $", 1190), ("1 190 $", 1190), (1190, 1190), (1190.0, 1190)]:
            self.assertEqual(parse_price(raw), want, raw)

    def test_no_data_is_none_never_zero(self):
        for raw in ["", None, "0 $", "—", "n/a", 0, True, "999999 $"]:
            self.assertIsNone(parse_price(raw), raw)


class ParseFeed(unittest.TestCase):
    def test_filters_city_and_counts(self):
        r = parse_feed(SAMPLE, "TOSHKENT")
        self.assertEqual(r.total, 12)
        self.assertEqual(r.invalid, 1)
        self.assertEqual(r.other_city, 2)
        self.assertEqual(r.duplicates, 1)
        self.assertEqual(len(r.packets), 8)

    def test_city_is_not_category(self):
        r = parse_feed(SAMPLE, "TOSHKENT")
        weird = next(p for p in r.packets if p.family == "WEIRD-1")
        self.assertEqual(weird.city, "TOSHKENT")
        self.assertEqual(weird.category_raw, "NAMANGAN")  # kept raw; the category_map will not map it

    def test_other_city_can_be_selected(self):
        r = parse_feed(SAMPLE, "namangan")
        self.assertEqual([p.family for p in r.packets], ["FAJR-14"])

    def test_empty_category_is_none(self):
        r = parse_feed(SAMPLE, "TOSHKENT")
        self.assertIsNone(next(p for p in r.packets if p.family == "NOCAT-1").category_raw)

    def test_prices_and_missing_price(self):
        r = parse_feed(SAMPLE, "TOSHKENT")
        eco = next(p for p in r.packets if p.family == "ECO-10")
        self.assertEqual(eco.prices, {4: 890, 3: 950, 2: 1050, 1: None})
        none = next(p for p in r.packets if p.family == "NOPRICE-1")
        self.assertEqual(none.prices, {})

    def test_duplicate_keeps_first(self):
        r = parse_feed(SAMPLE, "TOSHKENT")
        first = [p for p in r.packets if p.family == "FAJR-14" and p.date == date(2026, 12, 10)]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].prices[4], 2290)

    def test_zero_packets_is_failure(self):
        with self.assertRaises(SchemaError):
            parse_feed({"packets": []})

    def test_no_packets_for_city_is_failure(self):
        with self.assertRaises(SchemaError):
            parse_feed(SAMPLE, "SAMARKAND")

    def test_bad_shape_is_failure(self):
        for bad in [None, [], {}, {"packets": "x"}, {"items": []}]:
            with self.assertRaises(SchemaError):
                parse_feed(bad)

    def test_mostly_broken_feed_is_failure(self):
        with self.assertRaises(SchemaError):
            parse_feed({"packets": [{"x": 1}, {"y": 2}, SAMPLE["packets"][0]]})


if __name__ == "__main__":
    unittest.main()
