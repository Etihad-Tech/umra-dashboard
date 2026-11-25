"""Safar Travel source — read through SafarAgent, the company's own authenticated agent dashboard.

This is NOT a public scrape: SafarAgent is our company's legitimate agent login for Safar Travel's
dashboard (dashboard.safartravel.uz), backed by a JSON API:

    GET {SAFAR_TRAVEL_API_URL}   (HTTP Basic Auth: SAFAR_TRAVEL_USER / SAFAR_TRAVEL_PASS)
    -> a flat JSON array of ~750 rows, one row per departure of one package, e.g.:
        {
          "Product": "A100-HY0207SAF14MED", "ProductName": "A100",
          "FlightDirection": "Tashkent-Madina",
          "AllQuantity": 5, "Reserve": 5, "Quantity": "0",
          "TypePackage": "MILLIY-14",
          "StartProject": "2026-07-02T00:00:00", "EndProject": "2026-07-16T00:00:00",
          "PriceTPL": "$ 1 400", "PriceBDL": "$ 1 500", "PriceQUAD": "$ 1 300",
          "Manager": "RUSTAMJON",
          "hotel1": "AL FAYHA GOLDEN", "hotel2": "SEBAL AL MASI HOTEL",
          "nights1": 11, "nights2": 3, "train": "Poyezd Yo'q",
          "businessTourists": 0, "businessTouristsReturn": 0, "Infant": 0,
          "MavjudBiletBorish": 0, "MavjudBiletQaytish": 0,
          "TPLBonus": [], "DBLBonus": [], "QUADBonus": [], "Aksiya": []
        }

Rows with an empty "TypePackage" are placeholders (no active package) and are skipped.
"Quantity" (seats left) is sometimes prefixed with "!" (e.g. "!3", "!-1"); the prefix is stripped,
the number can be negative (oversold) and is kept as-is in services["qoldiq"] — there is no
seats_left column wired up for any source yet, so it is informational only for now, like kvota/bron.

FlightDirection carries the departure city in free text (TAS/Toshkent/Tashkent, NAM/NMA/Namangan,
FEG/Fargona, and typos of all of the above, or sometimes missing). parse_rows keeps only the rows
for ONE departure city per run, same convention as the Mandarin source.
"""
from __future__ import annotations

import re
from datetime import date

import requests

from .mandarin import Packet, ParseResult, SchemaError, parse_date, parse_price

MAX_BYTES = 10 * 1024 * 1024


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_quantity(value) -> int | None:
    """'!3' -> 3, '!-1' -> -1, '0' -> 0, '' / None -> None."""
    text = _clean(value)
    if text is None:
        return None
    text = text.lstrip("!").strip()
    try:
        return int(text)
    except ValueError:
        return None


def normalize_city(flight_direction) -> str | None:
    """Free-text departure/route text -> TOSHKENT | NAMANGAN | FARGONA | None (unknown)."""
    t = (flight_direction or "").upper()
    if "NAM" in t or "NMA" in t:
        return "NAMANGAN"
    if "TAS" in t or "TOSH" in t:
        return "TOSHKENT"
    if "FEG" in t or "FARG" in t:
        return "FARGONA"
    return None


def _hot(city: str, name, nights) -> list | None:
    name = _clean(name)
    if not name:
        return None
    return [city, name, nights if isinstance(nights, int) and nights > 0 else None]


_NO_TRAIN = {"", "yo'q", "yoq", "poyezd yo'q", "poyezd yoq"}


def has_train(text) -> bool:
    """The field holds either a real route ("Madina-Makka (bir tomon)") or one of a few "none" spellings."""
    t = (text or "").strip().strip(".").lower()
    return bool(t) and t not in _NO_TRAIN


# ------------------------------------------------------------------- fetch
def fetch_rows(api_url: str, user: str, password: str, session: requests.Session | None = None) -> list:
    s = session or requests.Session()
    r = s.get(api_url, auth=(user, password),
              headers={"User-Agent": "umra-price-monitor/1.0", "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    if len(r.content) > MAX_BYTES:
        raise SchemaError("safar_travel: response is unexpectedly large")
    data = r.json()
    if not isinstance(data, list):
        raise SchemaError("safar_travel: response is not a list")
    return data


# ------------------------------------------------------------------ parsing
def parse_rows(rows, departure_city: str = "TOSHKENT", today: date | None = None) -> ParseResult:
    if not isinstance(rows, list):
        raise SchemaError("safar_travel: rows must be a list")
    result = ParseResult(total=len(rows))
    if not rows:
        raise SchemaError("safar_travel: 0 rows")

    wanted = departure_city.strip().upper()
    seen: set[tuple[str, str, date]] = set()

    for raw in rows:
        if not isinstance(raw, dict):
            result.invalid += 1
            continue
        family = _clean(raw.get("TypePackage"))
        start = parse_date(raw.get("StartProject"))
        if not family or start is None:
            result.invalid += 1
            continue

        city = normalize_city(raw.get("FlightDirection"))
        if city != wanted:
            result.other_city += 1
            continue

        key = (family, city, start)
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)

        end = parse_date(raw.get("EndProject"))
        if end is not None and end < start:
            end = None
        nights = (end - start).days if end else None

        hot = [h for h in (
            _hot("Madina", raw.get("hotel1"), raw.get("nights1")),
            _hot("Makka", raw.get("hotel2"), raw.get("nights2")),
        ) if h]
        hotels = [{"city": h[0], "name": h[1], "nights": h[2]} for h in hot]

        qoldiq = parse_quantity(raw.get("Quantity"))
        train_text = _clean(raw.get("train"))
        services = {
            "kvota": raw.get("AllQuantity") if isinstance(raw.get("AllQuantity"), int) else None,
            "bron": raw.get("Reserve") if isinstance(raw.get("Reserve"), int) else None,
            "qoldiq": qoldiq,
            "trains": [train_text] if has_train(train_text) else [],   # live.js reads services.trains (array)
            "route_go": _clean(raw.get("FlightDirection")),            # shown in the "air/route" cell
            "manager": _clean(raw.get("Manager")),
            "infant": raw.get("Infant") if isinstance(raw.get("Infant"), int) else None,
            "tickets_out": raw.get("MavjudBiletBorish") if isinstance(raw.get("MavjudBiletBorish"), int) else None,
            "tickets_return": raw.get("MavjudBiletQaytish") if isinstance(raw.get("MavjudBiletQaytish"), int) else None,
            "source_code": _clean(raw.get("ProductName")),
            "source_row_id": _clean(raw.get("Product")),
        }
        prices = {2: parse_price(raw.get("PriceBDL")), 3: parse_price(raw.get("PriceTPL")),
                  4: parse_price(raw.get("PriceQUAD"))}
        sold_out = (qoldiq <= 0) if qoldiq is not None else None

        result.packets.append(Packet(
            family=family, title=family, city=city, category_raw=family, nights=nights,
            date=start, return_date=end, hotels=hotels, services=services, prices=prices,
            sold_out=sold_out))

    if result.invalid * 2 > result.total:
        raise SchemaError(f"safar_travel: {result.invalid} of {result.total} rows are invalid")
    if not result.packets:
        raise SchemaError(f"safar_travel: 0 usable rows for departure city {wanted}")
    return result
