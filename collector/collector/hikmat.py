"""Hikmat Umra Travel source (public HTML site).

Two public pages are read, nothing else:
    {HIKMAT_SITE_URL}/umra-paketlari-narxlari.html   table of packages: name, price "from", duration, hotels
    {HIKMAT_SITE_URL}/umra-dekabr-yanvar.html        table of group departure dates (Wednesdays, ISO dates in data-kunlar)

What the site does NOT publish: prices per date, prices for 1/2/3 people, seats left.  The only price is
"from X $ per person" for a package.  We store it as the price for 4 people in a room (the cheapest room type, the site
says rooms for 4-5 people are cheaper) and mark it in services["price_note"].  Exact prices are entered by hand on the
manual entry page; a manual entry always wins over this automatic price.

The site address comes from the HIKMAT_SITE_URL environment variable, like the other competitors.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html.parser import HTMLParser

import requests

from .mandarin import Packet, ParseResult, SchemaError, parse_price

MAX_BYTES = 3 * 1024 * 1024
PRICES_PATH = "/umra-paketlari-narxlari.html"
DATES_PATH = "/umra-dekabr-yanvar.html"
PRICE_PAX = 4                      # the "from" price is stored as the price for 4 people
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


class _Tables(HTMLParser):
    """Collects every <table> as a list of rows; each cell is (text, attrs-dict)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, dict]]]] = []
        self._row: list | None = None
        self._cell: list[str] | None = None
        self._attrs: dict = {}
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            self.tables.append([])
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._attrs = dict(attrs)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append((re.sub(r"\s+", " ", "".join(self._cell)).strip(), self._attrs))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self.tables:
                self.tables[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _tables(html: str) -> list:
    if len(html) > MAX_BYTES:
        raise SchemaError("page is unexpectedly large")
    p = _Tables()
    p.feed(html)
    return p.tables


# ----------------------------------------------------------------- parsing
def parse_nights(duration: str) -> int | None:
    """'13+ kun: Madina 2 + Makka 11+ kecha' -> 13 (the minimum);  '14 kecha: Istanbul 4 + Madina 3 + Makka 7' -> 14."""
    text = duration or ""
    after = text.split(":", 1)[1] if ":" in text else ""
    parts = [int(n) for n in re.findall(r"\d+", after)]
    if parts:
        return sum(parts)
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def parse_packages(html: str) -> list[dict]:
    """Rows of the price table: [{name, price, nights, duration, hotel, note}]."""
    for table in _tables(html):
        if not table:
            continue
        head = [c[0].lower() for c in table[0]]
        if len(head) >= 4 and head[0].startswith("paket") and head[1].startswith("narx"):
            out = []
            for row in table[1:]:
                cells = [c[0] for c in row]
                if len(cells) < 4 or not cells[0]:
                    continue
                out.append({
                    "name": cells[0],
                    "price": parse_price(cells[1]),
                    "duration": cells[2],
                    "nights": parse_nights(cells[2]),
                    "hotel": cells[3],
                    "note": cells[4] if len(cells) > 4 else "",
                })
            return out
    raise SchemaError("package price table not found")


def parse_departures(html: str) -> list[date]:
    """All ISO dates in data-kunlar attributes of the departure table, sorted, no duplicates."""
    found: set[date] = set()
    for table in _tables(html):
        for row in table:
            for _text, attrs in row:
                for iso in _ISO.findall(attrs.get("data-kunlar") or ""):
                    try:
                        found.add(date.fromisoformat(iso))
                    except ValueError:
                        continue
    if not found:
        raise SchemaError("no departure dates (data-kunlar) found")
    return sorted(found)


def parse_pages(prices_html: str, dates_html: str, today: date) -> ParseResult:
    packages = parse_packages(prices_html)
    departures = [d for d in parse_departures(dates_html) if d >= today]
    result = ParseResult(total=len(packages))
    if not packages:
        raise SchemaError("0 packages in the price table")
    usable = []
    for p in packages:
        if p["price"] is None:
            result.invalid += 1
            continue
        usable.append(p)
    if result.invalid * 2 > result.total or not usable:
        raise SchemaError(f"{result.invalid} of {result.total} packages have no price")
    if not departures:
        raise SchemaError("no departure dates on or after today")
    for p in usable:
        key = p["name"].strip()
        for d in departures:
            nights = p["nights"]
            result.packets.append(Packet(
                family=key, title=key, city=None, category_raw=key.upper(), nights=nights,
                date=d, return_date=(d + timedelta(days=nights)) if nights else None,
                hotels=[],
                services={"price_note": "from", "duration": p["duration"], "hotel_text": p["hotel"],
                          "highlights": p["note"]},
                prices={PRICE_PAX: p["price"]}, sold_out=None))
    return result


# ------------------------------------------------------------------ fetching
def fetch_pages(site_url: str, session: requests.Session | None = None) -> tuple[str, str]:
    s = session or requests.Session()
    base = site_url.rstrip("/")
    texts = []
    for path in (PRICES_PATH, DATES_PATH):
        r = s.get(base + path, timeout=30, headers={"User-Agent": "umra-price-monitor/1.0"})
        r.raise_for_status()
        if len(r.content) > MAX_BYTES:
            raise SchemaError("page is unexpectedly large")
        texts.append(r.text)
    return texts[0], texts[1]
