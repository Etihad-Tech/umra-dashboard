"""Safo Marva Tour source.

The public Safo site loads its packages in the browser from its own Supabase REST API, using a
PUBLIC (anon) key embedded in the page. We do exactly what the page does:
    GET {supabase}/rest/v1/landing_packages?select=*&is_active=eq.true
and nothing else (no other tables, no probing).

The site address comes from the SAFO_SITE_URL environment variable (competitor addresses are not
kept in this public repository). The key is read from the page at run time and is never stored.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date

import requests

from .mandarin import Packet, ParseResult, SchemaError, parse_date, parse_price

MAX_BYTES = 5 * 1024 * 1024
_SUPABASE_URL = re.compile(r"https://[a-z0-9]{15,30}\.supabase\.co")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_SRC = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.I)


def _jwt_role(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:  # noqa: BLE001
        return None


def find_credentials(texts: list[str]) -> tuple[str, str]:
    """Find the Supabase URL and the PUBLIC anon key in page/script texts.

    A key with any role other than 'anon' (e.g. a leaked service_role) is NEVER used.
    """
    joined = "\n".join(texts)
    urls = _SUPABASE_URL.findall(joined)
    if not urls:
        raise SchemaError("supabase url not found on the page")
    keys = [k for k in dict.fromkeys(_JWT.findall(joined)) if _jwt_role(k) == "anon"]
    if not keys:
        raise SchemaError("public anon key not found on the page")
    return urls[0], keys[0]


def discover(site_url: str, session: requests.Session | None = None) -> tuple[str, str]:
    s = session or requests.Session()
    headers = {"User-Agent": "umra-price-monitor/1.0"}
    page = s.get(site_url, timeout=30, headers=headers)
    page.raise_for_status()
    texts = [page.text[:MAX_BYTES]]
    origin = re.match(r"https?://[^/]+", site_url).group(0)
    for src in _SRC.findall(page.text)[:15]:
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = origin + src
        elif not src.startswith("http"):
            src = site_url.rstrip("/") + "/" + src
        if not src.startswith(origin):   # only the site's own scripts
            continue
        r = s.get(src, timeout=30, headers=headers)
        if r.ok:
            texts.append(r.text[:MAX_BYTES])
    try:
        return find_credentials(texts)
    except SchemaError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SchemaError(f"cannot read credentials: {type(e).__name__}")


def fetch_rows(supabase_url: str, key: str, session: requests.Session | None = None) -> list:
    s = session or requests.Session()
    r = s.get(f"{supabase_url}/rest/v1/landing_packages",
              params={"select": "*", "is_active": "eq.true"},
              headers={"apikey": key, "Authorization": f"Bearer {key}",
                       "User-Agent": "umra-price-monitor/1.0"}, timeout=30)
    r.raise_for_status()
    if len(r.content) > MAX_BYTES:
        raise SchemaError("response is unexpectedly large")
    data = r.json()
    if not isinstance(data, list):
        raise SchemaError("landing_packages did not return a list")
    return data


# ----------------------------------------------------------------- parsing
def _next_occurrence(day: int, month: int, on_or_after: date) -> date | None:
    for year in range(on_or_after.year, on_or_after.year + 5):   # 5 years covers 29.02
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= on_or_after:
            return d
    return None


def parse_free_date(text, on_or_after: date) -> date | None:
    """'18.11.2026' / '18.11.26' / ISO -> exact date.  '17.09' -> next occurrence on/after `on_or_after`."""
    if not text:
        return None
    t = str(text).strip()
    full = parse_date(t)
    if full:
        return full
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{2})", t)
    if m:
        try:
            return date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", t)
    if m:
        return _next_occurrence(int(m.group(1)), int(m.group(2)), on_or_after)
    return None


def parse_rows(rows, today: date) -> ParseResult:
    if not isinstance(rows, list):
        raise SchemaError("rows must be a list")
    result = ParseResult(total=len(rows))
    if not rows:
        raise SchemaError("0 rows in landing_packages")
    seen: set[tuple[str, date]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            result.invalid += 1
            continue
        section = str(raw.get("section") or "").strip()
        start = parse_free_date(raw.get("date_out"), today)
        if not section or start is None:
            result.invalid += 1
            continue
        if (section, start) in seen:
            result.duplicates += 1
            continue
        seen.add((section, start))
        end = parse_free_date(raw.get("date_return"), start)
        if end is not None and end < start:
            end = None
        prices = {pax: parse_price(raw.get(f"price_{pax}")) for pax in (2, 3, 4)}  # no 1-person price
        sold = raw.get("is_sold_out")
        result.packets.append(Packet(
            family=section, title=section, city=None, category_raw=section, nights=None,
            date=start, return_date=end, hotels=[],
            services={"days_label": str(raw.get("days_label") or "").strip() or None,
                      "source_row_id": raw.get("id")},
            prices=prices, sold_out=sold if isinstance(sold, bool) else None))
    if result.invalid * 2 > result.total:
        raise SchemaError(f"{result.invalid} of {result.total} rows are invalid")
    if not result.packets:
        raise SchemaError("0 usable rows")
    return result
