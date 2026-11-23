"""umra.uz source — three suppliers (Etihad, Safar Travel, Real Dreams) published on umra.uz.

Three operator pages are read, nothing else:
    GET {UMRA_UZ_URL}/uz/operators/etihad
    GET {UMRA_UZ_URL}/uz/operators/safar-time-exclusive
    GET {UMRA_UZ_URL}/uz/operators/real-dreams

Each page embeds the Next.js flight payload in script tags:
    self.__next_f.push([1,"…"])
We decode and join all such chunks, then extract package card objects.

**Important:** this format is an implementation detail of Next.js and can change on any deploy.
The parser fails loudly (SchemaError, not 0 prices) when the payload is missing or unrecognisable.

What the site DOES publish:
  * departure date, return date, nights
  * priceUsd (string decimal) for one occupancy (the classLabel / tier)
  * occupancyLabel ("4 kishilik", "3 kishilik", …) — only THAT occupancy has a price; others are NULL
  * classLabel (Lyuks / Premium / Komfort / Standart) → maps to lux / premium / comfort
  * seatsLeft — stored in services["seats_left"] until a dedicated column is added
  * templateSlug → used as the package family key
  * supplierName → "Etihad" or "Safar Travel"

What the site does NOT publish:
  * prices for all four occupancies at once — only the shown one
  * the full list of departures for a package (only the shelf card is shown)

The site address comes from the UMRA_UZ_URL environment variable.
"""
from __future__ import annotations

import json
import re
from datetime import date

import requests

from .mandarin import Packet, ParseResult, SchemaError, parse_date, parse_price

MAX_BYTES = 5 * 1024 * 1024

# Next.js flight payload chunks
_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)

# Occupancy string → pax count
_OCCUPANCY: dict[str, int] = {
    "1 kishilik": 1,
    "2 kishilik": 2,
    "3 kishilik": 3,
    "4 kishilik": 4,
    "5 kishilik": 4,   # 5-person treated as the quad tier (cheapest)
}

# classLabel → tier string used in category_map
_CLASS_MAP: dict[str, str] = {
    "lyuks": "lux",
    "premium": "premium",
    "komfort": "comfort",
    "standart": "comfort",
}


# ------------------------------------------------------------------ helpers

def _decode_flight(html: str) -> list[dict]:
    """Extract all card objects from Next.js self.__next_f.push([1,"…"]) chunks."""
    if len(html) > MAX_BYTES:
        raise SchemaError("umra_uz: page is unexpectedly large")

    combined: list[str] = []
    for m in _FLIGHT_RE.finditer(html):
        try:
            fragment = json.loads('"' + m.group(1) + '"')
            combined.append(fragment)
        except (json.JSONDecodeError, ValueError):
            continue

    if not combined:
        raise SchemaError("umra_uz: no Next.js flight payload found — the page format may have changed")

    joined = "".join(combined)

    cards: list[dict] = []
    depth = 0
    start = -1
    for i, ch in enumerate(joined):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                fragment = joined[start : i + 1]
                try:
                    obj = json.loads(fragment)
                    if isinstance(obj, dict) and "templateSlug" in obj and "priceUsd" in obj:
                        cards.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass
                start = -1

    return cards


def _pax_from_occupancy(label: str | None) -> int | None:
    if not label:
        return None
    return _OCCUPANCY.get(label.strip().lower())


def _tier_from_class(label: str | None) -> str | None:
    if not label:
        return None
    return _CLASS_MAP.get(label.strip().lower())


# ------------------------------------------------------------------ parsing

def parse_page(html: str, supplier_name: str) -> ParseResult:
    """Parse one operator page (etihad OR safar-time-exclusive).

    supplier_name is the value to match against card["supplierName"] (case-insensitive).
    """
    cards = _decode_flight(html)
    if not cards:
        raise SchemaError(f"umra_uz ({supplier_name}): 0 card objects after decoding flight payload")

    result = ParseResult(total=len(cards))
    seen: set[tuple[str, str]] = set()

    for raw in cards:
        card_supplier = str(raw.get("supplierName") or "").strip().lower()
        if card_supplier != supplier_name.strip().lower():
            result.other_city += 1
            continue

        slug = str(raw.get("templateSlug") or "").strip()
        dep_id = str(raw.get("href") or raw.get("departureId") or "").strip()
        if not dep_id:
            href = str(raw.get("href") or "")
            m = re.search(r"/(UZD-[^/\"']+)", href)
            dep_id = m.group(1) if m else ""

        if not slug or not dep_id:
            result.invalid += 1
            continue

        key = (slug, dep_id)
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)

        depart = parse_date(raw.get("departDate"))
        if depart is None:
            result.invalid += 1
            continue

        ret = parse_date(raw.get("returnDate"))
        nights = raw.get("nightsTotal")
        if not isinstance(nights, int):
            nights = None

        price_usd = parse_price(raw.get("priceUsd"))
        pax = _pax_from_occupancy(raw.get("occupancyLabel"))
        if price_usd is None or pax is None:
            result.invalid += 1
            continue

        prices: dict[int, int | None] = {1: None, 2: None, 3: None, 4: None}
        prices[pax] = price_usd

        class_label = str(raw.get("classLabel") or "").strip() or None

        title = str(raw.get("title") or slug).strip()

        services: dict = {
            "departure_id": dep_id,
            "occupancy_label": raw.get("occupancyLabel"),
            "seats_left": raw.get("seatsLeft"),
            "hotel_makkah": raw.get("hotelName"),
            "hotel_madinah": raw.get("hotelMadinahName"),
            "board": raw.get("boardLabel"),
            "chips": raw.get("chips"),
            "services_list": raw.get("services"),
        }

        result.packets.append(Packet(
            family=slug,
            title=title,
            city=None,
            category_raw=class_label,
            nights=nights,
            date=depart,
            return_date=ret,
            hotels=[],
            services=services,
            prices=prices,
            sold_out=None,
        ))

    if result.invalid * 2 > result.total and result.total > 0:
        raise SchemaError(
            f"umra_uz ({supplier_name}): {result.invalid} of {result.total} cards are invalid"
        )
    if not result.packets:
        raise SchemaError(
            f"umra_uz ({supplier_name}): 0 usable departures (total cards: {result.total})"
        )
    return result


def parse_both_pages(etihad_html: str, safo_html: str) -> tuple[ParseResult, ParseResult]:
    """Parse the two operator pages and return (etihad_result, safo_result)."""
    r_etihad = parse_page(etihad_html, "etihad")
    r_safo = parse_page(safo_html, "safar travel")
    return r_etihad, r_safo


def parse_three_pages(etihad_html: str, safo_html: str, real_dreams_html: str) -> tuple[ParseResult, ParseResult, ParseResult]:
    """Parse the three operator pages and return (etihad_result, safo_result, real_dreams_result)."""
    r_etihad = parse_page(etihad_html, "etihad")
    r_safo = parse_page(safo_html, "safar travel")
    r_real_dreams = parse_page(real_dreams_html, "real dreams")
    return r_etihad, r_safo, r_real_dreams


# ------------------------------------------------------------------ fetching

def fetch_pages(
    site_url: str,
    etihad_slug: str = "etihad",
    safo_slug: str = "safar-time-exclusive",
    real_dreams_slug: str = "real-dreams",
    session: requests.Session | None = None,
) -> tuple[str, str, str]:
    """Fetch the three operator pages. Returns (etihad_html, safo_html, real_dreams_html)."""
    s = session or requests.Session()
    base = site_url.rstrip("/")
    headers = {
        "User-Agent": "umra-price-monitor/1.0 (Etihad Travel; contact: tech@etihad.uz)",
        "Accept-Language": "uz,en;q=0.9",
    }

    paths = {
        "etihad": f"/uz/operators/{etihad_slug}",
        "safo": f"/uz/operators/{safo_slug}",
        "real_dreams": f"/uz/operators/{real_dreams_slug}",
    }

    texts: list[str] = []
    for name, path in paths.items():
        url = base + path
        r = s.get(url, timeout=30, headers=headers)
        if r.status_code == 403:
            raise SchemaError(f"umra_uz: 403 on {path} — IP or User-Agent may be blocked")
        if r.status_code == 429:
            raise SchemaError(f"umra_uz: 429 on {path} — rate limited, backing off")
        r.raise_for_status()
        if len(r.content) > MAX_BYTES:
            raise SchemaError(f"umra_uz: {path} is unexpectedly large")
        texts.append(r.text)

    return texts[0], texts[1], texts[2]
