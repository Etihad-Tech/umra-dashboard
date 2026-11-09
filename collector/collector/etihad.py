"""Parser for the ETIHAD partner API (our own showroom).

Pure functions only: no network, no database.

API (address and key come from ETIHAD_API_URL / ETIHAD_API_KEY, key goes in the X-Api-Key header):
  GET /api/packages -> {"ok": true, "v": 128, "packages": [...], ...}
Important facts:
  * a package has price BANDS (from..to, inclusive) instead of single departures; prices are per person
    by room type: quad = 4 pax, trpl = 3, dbl = 2, sngl = 1;
  * ``depday`` (mon..sun) is the weekday of the flight, so departures = every such weekday inside a band;
    without ``depday`` only the first day of a band is used;
  * ``tier`` is already comfort | lux | premium;
  * there is NO seats-left / quota field in this API.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .mandarin import Packet, ParseResult, SchemaError, parse_date, parse_price

ROOM_TO_PAX = {"quad": 4, "trpl": 3, "dbl": 2, "sngl": 1}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MAX_BAND_DAYS = 800          # a band longer than this is certainly a data error


def _prices(raw) -> dict[int, int | None]:
    raw = raw if isinstance(raw, dict) else {}
    return {pax: parse_price(raw.get(room)) for room, pax in ROOM_TO_PAX.items()}


def _range_from_text(text) -> tuple[date, date] | None:
    """'03.09.2026 – 09.01.2027' -> (date, date)."""
    found = re.findall(r"\d{1,2}\.\d{1,2}\.\d{4}", str(text or ""))
    if len(found) != 2:
        return None
    a, b = parse_date(found[0]), parse_date(found[1])
    return (a, b) if a and b and a <= b else None


def _departure_dates(start: date, end: date, weekday: int | None) -> list[date]:
    if weekday is None:
        return [start]
    if (end - start).days > MAX_BAND_DAYS:
        raise SchemaError("price band is unreasonably long")
    days = (end - start).days + 1
    return [d for d in (start + timedelta(n) for n in range(days)) if d.weekday() == weekday]


def parse_packages(data) -> ParseResult:
    if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
        raise SchemaError("etihad: response has no packages list")
    items = data["packages"]
    if not items:
        raise SchemaError("etihad: 0 packages")
    result = ParseResult(total=len(items))
    seen: set[tuple[str, date]] = set()
    for raw in items:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            result.invalid += 1
            continue
        pkg_id = str(raw["id"]).strip()
        nights = raw.get("nights") if isinstance(raw.get("nights"), int) else None
        weekday = WEEKDAYS.get(str(raw.get("depday") or "").strip().lower()[:3])
        tier = str(raw.get("tier") or "").strip().upper() or None

        bands = []
        for b in raw.get("bands") or []:
            if not isinstance(b, dict):
                continue
            s, e = parse_date(b.get("from")), parse_date(b.get("to"))
            if s and e and s <= e:
                bands.append((s, e, _prices(b.get("prices"))))
        if not bands:                                       # no periods: one price for the whole date range
            rng = _range_from_text(raw.get("dates"))
            if rng:
                bands.append((rng[0], rng[1], _prices(raw.get("prices"))))
        if not bands:
            result.invalid += 1
            continue

        stay = raw.get("stay") if isinstance(raw.get("stay"), list) else []
        services = {"transfer": raw.get("transfer"), "route": raw.get("route"), "depart": raw.get("depart"),
                    "depday": raw.get("depday"), "season": raw.get("season"), "days": raw.get("days")}
        for s, e, prices in bands:
            for d in _departure_dates(s, e, weekday):
                if (pkg_id, d) in seen:
                    result.duplicates += 1
                    continue
                seen.add((pkg_id, d))
                result.packets.append(Packet(
                    family=pkg_id, title=str(raw.get("name") or pkg_id).strip(), city=None,
                    category_raw=tier, nights=nights, date=d,
                    return_date=d + timedelta(days=nights) if nights else None,
                    hotels=stay, services=services, prices=prices, sold_out=None))
    if result.invalid * 2 > result.total:
        raise SchemaError(f"etihad: {result.invalid} of {result.total} packages are invalid")
    if not result.packets:
        raise SchemaError("etihad: 0 usable departures")
    return result
