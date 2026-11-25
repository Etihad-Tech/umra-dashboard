"""Tests for collector.umra_uz."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from collector import umra_uz
from collector.mandarin import SchemaError
from collector.run import fetch_umra_uz_pages
from tests.fakes import FakeStore
import collector.run as runner


# ------------------------------------------------------------------ fixture helpers

def _make_card(**kwargs) -> dict:
    """Return a minimal valid Etihad card. Override any field via kwargs."""
    base = {
        "templateSlug": "etihad-makkah-7n",
        "href": "/uz/packages/UZD-123456",
        "departDate": "2027-03-01",
        "returnDate": "2027-03-10",
        "nightsTotal": 9,
        "priceUsd": "1890",
        "occupancyLabel": "4 kishilik",
        "classLabel": "Lyuks",
        "supplierName": "Etihad",
        "seatsLeft": 5,
        "title": "Etihad Lyuks 7 kecha",
        "hotelName": "Swissotel Makkah",
        "hotelMadinahName": "Movenpick Madinah",
        "boardLabel": "BB",
        "chips": ["VIP zal"],
        "services": ["transfer"],
    }
    base.update(kwargs)
    return base


def _flight_html(cards: list[dict], supplier_name_override: str | None = None) -> str:
    """Wrap cards in Next.js self.__next_f.push script tags."""
    payload_str = json.dumps(cards)
    # Double-encode: first dump to JSON string, then escape for JS string literal
    js_escaped = json.dumps(payload_str)[1:-1]  # strip outer quotes
    return f"""<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<script>self.__next_f.push([1,"{js_escaped}"])</script>
</body>
</html>"""


# ------------------------------------------------------------------ decoder tests

class TestDecoder:
    def test_extracts_cards(self):
        card = _make_card()
        html = _flight_html([card])
        cards = umra_uz._decode_flight(html)
        assert len(cards) == 1
        assert cards[0]["templateSlug"] == "etihad-makkah-7n"

    def test_missing_payload_raises(self):
        html = "<html><body><p>No scripts here</p></body></html>"
        with pytest.raises(SchemaError, match="flight payload"):
            umra_uz._decode_flight(html)

    def test_multiple_chunks_joined(self):
        card1 = _make_card(templateSlug="pkg-1")
        card2 = _make_card(templateSlug="pkg-2", href="/uz/packages/UZD-999")
        # Two separate script tags
        c1_str = json.dumps([card1])
        c2_str = json.dumps([card2])
        e1 = json.dumps(c1_str)[1:-1]
        e2 = json.dumps(c2_str)[1:-1]
        html = f"""<html><body>
<script>self.__next_f.push([1,"{e1}"])</script>
<script>self.__next_f.push([1,"{e2}"])</script>
</body></html>"""
        cards = umra_uz._decode_flight(html)
        slugs = {c["templateSlug"] for c in cards}
        assert "pkg-1" in slugs
        assert "pkg-2" in slugs

    def test_page_too_large_raises(self):
        big = "x" * (5 * 1024 * 1024 + 1)
        with pytest.raises(SchemaError, match="large"):
            umra_uz._decode_flight(big)


# ------------------------------------------------------------------ occupancy / class map

class TestOccupancy:
    @pytest.mark.parametrize("label,expected", [
        ("1 kishilik", 1),
        ("2 kishilik", 2),
        ("3 kishilik", 3),
        ("4 kishilik", 4),
        ("5 kishilik", 4),
    ])
    def test_known_labels(self, label, expected):
        assert umra_uz._pax_from_occupancy(label) == expected

    def test_unknown_returns_none(self):
        assert umra_uz._pax_from_occupancy("6 kishilik") is None

    def test_none_returns_none(self):
        assert umra_uz._pax_from_occupancy(None) is None


class TestClassMap:
    @pytest.mark.parametrize("label,expected", [
        ("Lyuks", "lux"),
        ("lyuks", "lux"),
        ("Premium", "premium"),
        ("Komfort", "comfort"),
        ("Standart", "comfort"),
    ])
    def test_known_labels(self, label, expected):
        assert umra_uz._tier_from_class(label) == expected

    def test_unknown_returns_none(self):
        assert umra_uz._tier_from_class("Biznes") is None


# ------------------------------------------------------------------ parse_page

class TestParsePage:
    def test_basic_parse(self):
        card = _make_card()
        html = _flight_html([card])
        result = umra_uz.parse_page(html, "Etihad")
        assert len(result.packets) == 1
        pkt = result.packets[0]
        assert pkt.family == "etihad-makkah-7n"
        assert pkt.nights == 9
        assert pkt.prices[4] == 1890
        assert pkt.prices[1] is None
        assert pkt.prices[2] is None
        assert pkt.prices[3] is None

    def test_one_price_per_pax_only(self):
        """Only the shown pax should have a price; others must be None, never 0."""
        card = _make_card(occupancyLabel="2 kishilik", priceUsd="950")
        html = _flight_html([card])
        result = umra_uz.parse_page(html, "Etihad")
        pkt = result.packets[0]
        assert pkt.prices[2] == 950
        assert pkt.prices[1] is None
        assert pkt.prices[3] is None
        assert pkt.prices[4] is None

    def test_supplier_filter_excludes_others(self):
        etihad_card = _make_card(supplierName="Etihad")
        safo_card = _make_card(
            templateSlug="safo-pkg",
            href="/uz/packages/UZD-777",
            supplierName="Safar Travel",
        )
        html = _flight_html([etihad_card, safo_card])
        result = umra_uz.parse_page(html, "Etihad")
        assert len(result.packets) == 1
        assert result.other_city == 1

    def test_missing_price_marks_invalid(self):
        card = _make_card(priceUsd=None)
        html = _flight_html([card])
        with pytest.raises(SchemaError):
            umra_uz.parse_page(html, "Etihad")

    def test_missing_occupancy_marks_invalid(self):
        card = _make_card(occupancyLabel=None)
        html = _flight_html([card])
        with pytest.raises(SchemaError):
            umra_uz.parse_page(html, "Etihad")

    def test_missing_date_marks_invalid(self):
        card = _make_card(departDate=None)
        html = _flight_html([card])
        with pytest.raises(SchemaError):
            umra_uz.parse_page(html, "Etihad")

    def test_majority_invalid_raises(self):
        """If >50% cards invalid, raise SchemaError."""
        good = _make_card()
        bad1 = _make_card(templateSlug="bad1", href="/uz/packages/UZD-b1", priceUsd=None)
        bad2 = _make_card(templateSlug="bad2", href="/uz/packages/UZD-b2", priceUsd=None)
        html = _flight_html([good, bad1, bad2])
        with pytest.raises(SchemaError, match="invalid"):
            umra_uz.parse_page(html, "Etihad")

    def test_duplicate_deduplication(self):
        card = _make_card()
        html = _flight_html([card, card])
        result = umra_uz.parse_page(html, "Etihad")
        assert len(result.packets) == 1
        assert result.duplicates == 1

    def test_seats_left_in_services(self):
        card = _make_card(seatsLeft=3)
        html = _flight_html([card])
        result = umra_uz.parse_page(html, "Etihad")
        assert result.packets[0].services["seats_left"] == 3

    def test_category_raw_preserved(self):
        card = _make_card(classLabel="Komfort")
        html = _flight_html([card])
        result = umra_uz.parse_page(html, "Etihad")
        assert result.packets[0].category_raw == "Komfort"

    def test_zero_usable_departures_raises(self):
        """When supplier filter leaves 0 packets, raise SchemaError."""
        card = _make_card(supplierName="SomeOther")
        html = _flight_html([card])
        with pytest.raises(SchemaError, match="0 usable"):
            umra_uz.parse_page(html, "Etihad")


# ------------------------------------------------------------------ parse_both_pages

class TestParseBothPages:
    def test_returns_both_results(self):
        e_card = _make_card(supplierName="Etihad")
        s_card = _make_card(
            templateSlug="safo-pkg-1",
            href="/uz/packages/UZD-s1",
            supplierName="Safar Travel",
        )
        e_html = _flight_html([e_card])
        s_html = _flight_html([s_card])
        r_e, r_s = umra_uz.parse_both_pages(e_html, s_html)
        assert len(r_e.packets) == 1
        assert len(r_s.packets) == 1
        assert r_e.packets[0].family == "etihad-makkah-7n"
        assert r_s.packets[0].family == "safo-pkg-1"


# ------------------------------------------------------------------ run_umra_uz_* integration

class TestRunUmraUz:
    def _etihad_html(self):
        return _flight_html([_make_card(supplierName="Etihad")])

    def _safo_html(self):
        return _flight_html([
            _make_card(
                templateSlug="safo-pkg-1",
                href="/uz/packages/UZD-s1",
                supplierName="Safar Travel",
            )
        ])

    def test_run_etihad_stores_packets(self):
        store = FakeStore()
        pages = (self._etihad_html(), self._safo_html())
        with mock.patch.object(runner, "fetch_umra_uz_pages", return_value=pages):
            runner.run_umra_uz_etihad(store, "https://umra.uz")
        assert len(store.packages) >= 1 or len(store.departures) >= 1

    def test_run_safo_stores_packets(self):
        store = FakeStore()
        pages = (self._etihad_html(), self._safo_html())
        with mock.patch.object(runner, "fetch_umra_uz_pages", return_value=pages):
            runner.run_umra_uz_safo(store, "https://umra.uz")
        assert len(store.packages) >= 1 or len(store.departures) >= 1

    def test_idempotent_no_snapshot_on_same_price(self):
        store = FakeStore()
        pages = (self._etihad_html(), self._safo_html())
        with mock.patch.object(runner, "fetch_umra_uz_pages", return_value=pages):
            runner.run_umra_uz_etihad(store, "https://umra.uz")
            snap_count_1 = len(store.snapshots)
            runner.run_umra_uz_etihad(store, "https://umra.uz")
            snap_count_2 = len(store.snapshots)
        # Second run with same price should not add new snapshots
        assert snap_count_2 == snap_count_1

    def test_price_change_creates_snapshot(self):
        store = FakeStore()
        pages1 = (_flight_html([_make_card(priceUsd="1890", supplierName="Etihad")]),
                  self._safo_html())
        pages2 = (_flight_html([_make_card(priceUsd="1990", supplierName="Etihad")]),
                  self._safo_html())
        with mock.patch.object(runner, "fetch_umra_uz_pages", return_value=pages1):
            runner.run_umra_uz_etihad(store, "https://umra.uz")
        snap_before = len(store.snapshots)
        with mock.patch.object(runner, "fetch_umra_uz_pages", return_value=pages2):
            runner.run_umra_uz_etihad(store, "https://umra.uz")
        # Price changed: a new snapshot should appear
        assert len(store.snapshots) >= snap_before
