"""Parser for the Family Mandarin showroom feed.

Pure functions only: no network, no database. That makes it easy to test on saved data.

Feed (address comes from the MANDARIN_URL env var):  {"packets": [...]}
Important facts (see TASK section 8.2):
  * ``company`` is the DEPARTURE CITY (TOSHKENT / NAMANGAN / URGANCH), not a category.
  * ``prices[]`` holds {"pax": 4, "amount": "1,190 $"}; amount is text.
  * There is no seats-left field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

MAX_PRICE_USD = 100_000  # anything above is certainly a parse error


class SchemaError(Exception):
    """The feed does not look like what we expect. The run must be marked as failed."""


@dataclass
class Packet:
    family: str                 # package code, e.g. FAJR-14 (our packages.external_key)
    title: str | None
    city: str | None            # departure city, upper case (None when the source has no such field)
    category_raw: str | None    # exactly as published (stripped), None when empty
    nights: int | None
    date: date
    return_date: date | None
    hotels: list
    services: dict
    prices: dict[int, int | None]  # pax (1..4) -> USD, None = no price published
    sold_out: bool | None = None   # None = the source does not say


@dataclass
class ParseResult:
    packets: list[Packet] = field(default_factory=list)
    total: int = 0              # packets in the feed
    invalid: int = 0            # broken packets (no family / date / city)
    other_city: int = 0         # valid but another departure city
    duplicates: int = 0         # same family + city + date seen twice (first one wins)


def parse_price(value) -> int | None:
    """'1,190 $' -> 1190.  Empty, zero or unparseable -> None (never 0)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(round(value))
        return n if 0 < n <= MAX_PRICE_USD else None
    m = re.search(r"\d[\d\s,. ]*", str(value))
    if not m:
        return None
    digits = m.group(0).strip()
    digits = re.sub(r"\.\d{1,2}$", "", digits)        # drop cents: 1,190.00 -> 1,190
    digits = re.sub(r"[,.\s ]", "", digits)       # thousands separators
    if not digits:
        return None
    n = int(digits)
    return n if 0 < n <= MAX_PRICE_USD else None


def parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt, size in (("%Y-%m-%d", 10), ("%d.%m.%Y", 10), ("%d/%m/%Y", 10)):
        try:
            return datetime.strptime(text[:size], fmt).date()
        except ValueError:
            continue
    return None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _parse_prices(raw) -> dict[int, int | None]:
    prices: dict[int, int | None] = {}
    if not isinstance(raw, list):
        return prices
    for item in raw:
        if not isinstance(item, dict):
            continue
        pax = _to_int(item.get("pax"))
        if pax is None or not 1 <= pax <= 4:
            continue
        price = parse_price(item.get("amount"))
        if prices.get(pax) is None:   # keep the first real price if pax is repeated
            prices[pax] = price
    return prices


def parse_feed(data, departure_city: str = "TOSHKENT") -> ParseResult:
    """Validate the feed and return packets of ONE departure city.

    Raises SchemaError when the feed is broken or yields zero items:
    "0 items" must be a failed run, never an empty success.
    """
    if not isinstance(data, dict) or not isinstance(data.get("packets"), list):
        raise SchemaError("feed has no 'packets' list")
    raw_packets = data["packets"]
    result = ParseResult(total=len(raw_packets))
    if result.total == 0:
        raise SchemaError("feed contains 0 packets")

    wanted = departure_city.strip().upper()
    seen: set[tuple[str, str, date]] = set()

    for raw in raw_packets:
        if not isinstance(raw, dict):
            result.invalid += 1
            continue
        family = _clean(raw.get("family"))
        city = _clean(raw.get("company"))
        start = parse_date(raw.get("date_key")) or parse_date(raw.get("date_start"))
        if not family or not city or start is None:
            result.invalid += 1
            continue
        city = city.upper()
        if city != wanted:
            result.other_city += 1
            continue
        key = (family, city, start)
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)

        end = parse_date(raw.get("date_end"))
        if end is not None and end < start:
            end = None
        hotels = [h for h in raw.get("hotels") or [] if isinstance(h, dict)]
        services = {
            "list": raw.get("services") if isinstance(raw.get("services"), list) else [],
            "trains": raw.get("trains") if isinstance(raw.get("trains"), list) else [],
            "airline": _clean(raw.get("airline")),
            "route_go": _clean(raw.get("route_go")),
            "route_ret": _clean(raw.get("route_ret")),
            "extra_note": _clean(raw.get("extra_note")),
        }
        result.packets.append(Packet(
            family=family,
            title=_clean(raw.get("title")),
            city=city,
            category_raw=_clean(raw.get("category")),
            nights=_to_int(raw.get("nights")),
            date=start,
            return_date=end,
            hotels=hotels,
            services=services,
            prices=_parse_prices(raw.get("prices")),
        ))

    # Many broken packets means the feed format changed: fail loudly instead of syncing half the data.
    if result.invalid * 2 > result.total:
        raise SchemaError(f"{result.invalid} of {result.total} packets are invalid")
    if not result.packets:
        raise SchemaError(f"0 packets for departure city {wanted}")
    return result
