"""Parser for the Family Mandarin showroom feed.

Pure functions only: no network, no database. That makes it easy to test on saved data.

Feed (address comes from the MANDARIN_URL env var):  {"packets": [...]}
Important facts (see TASK section 8.2):
  * ``company`` is the DEPARTURE CITY (TOSHKENT / NAMANGAN / URGANCH), not a category.
  * ``prices[]`` holds {"pax": 4, "amount": "1,190 $"}; amount is text.
  * There is no seats-left field.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime

MIN_PRICE_USD = 100      # an Umra package below this is certainly a parse error ("2 x ...", "12,50")
MAX_PRICE_USD = 100_000  # anything above is certainly a parse error
PAX = (4, 3, 2, 1)


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


# One amount: "1,190" / "1 190" / "1.190" (thousands groups) or "1190", optionally with cents ".00" / ",00".
_GAP = "[ \u00a0\u202f\u2009]"          # space, no-break space, narrow no-break space, thin space
_NUMBER = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:(?:%s|[.,])\d{3})+|\d+)(?:[.,]\d{1,2})?(?!\d)" % _GAP)


def _in_range(n: int) -> int | None:
    return n if MIN_PRICE_USD <= n <= MAX_PRICE_USD else None


def parse_price(value) -> int | None:
    """'1,190 $' / '1 190,00 $' -> 1190.  Empty, zero, out of range, unparseable or ambiguous -> None (never 0).

    The text must hold exactly ONE amount: '2 x 1190' or '1190$ / 1290$' is not a price we can trust.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return _in_range(int(round(value)))
    numbers = _NUMBER.findall(str(value))
    if len(numbers) != 1:
        return None
    whole = re.sub(r"[.,]\d{1,2}$", "", numbers[0])   # drop cents: 1,190.00 / 1 190,00 -> 1,190 / 1 190
    digits = re.sub(r"\D", "", whole)                  # thousands separators
    return _in_range(int(digits)) if digits else None


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
    """A positive whole number, else None. True/False are not numbers here."""
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n > 0 else None


def _parse_prices(raw) -> dict[int, int | None]:
    """Always all four pax: a pax that is no longer listed is "no price" (NULL), not "unchanged"."""
    prices: dict[int, int | None] = {pax: None for pax in PAX}
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
    require_some_price(result, "feed")
    return result


def require_some_price(result: ParseResult, what: str) -> None:
    """Not a single price anywhere = the price fields moved or were renamed. Fail loudly instead of
    overwriting every known price with NULL."""
    if not any(v is not None for p in result.packets for v in p.prices.values()):
        raise SchemaError(f"{what}: no prices in any of {len(result.packets)} packets")
