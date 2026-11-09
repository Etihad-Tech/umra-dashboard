"""Safo Marva Tour source.

The public Safo site loads its packages in the browser from its own Supabase REST API, using a
PUBLIC (anon) key embedded in the page. We do what the page does:
    GET {supabase}/rest/v1/landing_packages?select=*&is_active=eq.true
and nothing else (no other tables, no probing). We add order=id.asc + limit/offset so the rows come in a
stable order and a server row cap (1000 by default) can never cut the list short silently.

Requests per run (not one, by design): the page itself, up to 15 of the site's own scripts (the key and
the API address may live in a script), then the data pages (normally one page + one empty page that
confirms the end). A failed attempt repeats all of it, up to 3 attempts.

The site address comes from the SAFO_SITE_URL environment variable (competitor addresses are not
kept in this public repository). The key is read from the page at run time and is never stored.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date

import requests

from .mandarin import Packet, ParseResult, SchemaError, parse_date, parse_price, require_some_price

MAX_BYTES = 5 * 1024 * 1024
PAGE = 1000
MAX_PAGES = 20           # 20,000 rows: far above a landing page; more means the server ignores paging
PAST_GRACE_DAYS = 45     # a yearless date that passed at most this long ago keeps that (past) year
_SUPABASE_URL = re.compile(r"https://[a-z0-9]{15,30}\.supabase\.co")
# the page may point at a gateway on its OWN domain instead: var SUPABASE_URL = '/gw';
_REL_URL = re.compile(r"""SUPABASE_URL\s*=\s*['"](/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*)['"]""")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_SRC = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.I)


def _jwt_role(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:  # noqa: BLE001
        return None


def find_credentials(texts: list[str], site_origin: str | None = None) -> tuple[str, str]:
    """Find the Supabase URL and the PUBLIC anon key in page/script texts.

    The URL is either a full https://<ref>.supabase.co address, or a relative path such as '/gw'
    (a gateway on the site's own domain), which is resolved against `site_origin`.
    A key with any role other than 'anon' (e.g. a leaked service_role) is NEVER used.
    """
    joined = "\n".join(texts)
    urls = _SUPABASE_URL.findall(joined)
    if not urls and site_origin:
        urls = [site_origin.rstrip("/") + path for path in _REL_URL.findall(joined)]
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
    final_origin = re.match(r"https?://[^/]+", getattr(page, "url", None) or site_url).group(0)
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
        return find_credentials(texts, final_origin)
    except SchemaError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SchemaError(f"cannot read credentials: {type(e).__name__}")


def fetch_rows(supabase_url: str, key: str, session: requests.Session | None = None, page_size: int = PAGE) -> list:
    """All active rows, in id order. Stops only on an EMPTY page (a server cap below page_size cannot cut the
    list short). If the server ignores offset (the same rows again), the rows already read are the full list."""
    s = session or requests.Session()
    rows: list = []
    seen: set = set()
    for _ in range(MAX_PAGES):
        r = s.get(f"{supabase_url}/rest/v1/landing_packages",
                  params={"select": "*", "is_active": "eq.true", "order": "id.asc",
                          "limit": page_size, "offset": len(rows)},
                  headers={"apikey": key, "Authorization": f"Bearer {key}",
                           "User-Agent": "umra-price-monitor/1.0"}, timeout=30)
        r.raise_for_status()
        if len(r.content) > MAX_BYTES:
            raise SchemaError("response is unexpectedly large")
        data = r.json()
        if not isinstance(data, list):
            raise SchemaError("landing_packages did not return a list")
        ids = {x.get("id") for x in data if isinstance(x, dict) and x.get("id") is not None}
        if not data or (ids and ids <= seen):
            return rows
        seen |= ids
        rows.extend(data)
        if len(data) > page_size:       # the server ignored limit: it sent everything at once
            return rows
    raise SchemaError(f"landing_packages: more than {MAX_PAGES} pages")


# ----------------------------------------------------------------- parsing
def _occurrence(day: int, month: int, today: date, grace_days: int = 0) -> date | None:
    """The date of day.month nearest to `today` in the sense of the timetable:
    the most recent past one if it is at most `grace_days` old (a departure that left recently and is still
    listed keeps its year, also across New Year: '28.12' on 02.01 is LAST December), otherwise the next one
    on or after today."""
    if grace_days:
        for year in range(today.year, today.year - 5, -1):   # 5 years covers 29.02
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if d <= today:
                if (today - d).days <= grace_days:
                    return d
                break
    for year in range(today.year, today.year + 5):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def parse_free_date(text, today: date, grace_days: int = PAST_GRACE_DAYS) -> date | None:
    """'18.11.2026' / '18.11.26' / ISO -> exact date.  '17.09' -> see _occurrence (no year given)."""
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
        return _occurrence(int(m.group(1)), int(m.group(2)), today, grace_days)
    return None


def parse_rows(rows, today: date) -> ParseResult:
    """`today` is the Tashkent date (clock.today_tashkent)."""
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
        end = parse_free_date(raw.get("date_return"), start, grace_days=0)   # next one on/after the departure
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
    require_some_price(result, "landing_packages")
    return result
