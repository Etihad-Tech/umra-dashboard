"""Minimal Supabase (PostgREST) client for the collector.

Uses the service_role key, which bypasses RLS. The key comes ONLY from the environment of the
server (see .env.example). It is never logged, printed or committed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import requests

PAGE = 1000
CHUNK = 150  # ids per "in.(...)" filter, keeps URLs short


class DbError(Exception):
    pass


@dataclass
class State:
    packages: dict = field(default_factory=dict)       # (external_key, city) -> row
    departures: dict = field(default_factory=dict)     # (package_id, date) -> row
    last_prices: dict = field(default_factory=dict)    # (departure_id, pax) -> price | None
    package_city: dict = field(default_factory=dict)   # package_id -> city
    manual_prices: dict = field(default_factory=dict)  # (departure_id, pax) -> price of the ACTIVE manual entry


class RestStore:
    def __init__(self, url: str, service_key: str, session: requests.Session | None = None, timeout: int = 30):
        self.base = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers.update({
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        })

    # ---- low level -------------------------------------------------------
    def _request(self, method: str, path: str, *, params=None, json=None, prefer: str | None = None):
        headers = {"Prefer": prefer} if prefer else {}
        r = self.s.request(method, f"{self.base}/{path}", params=params, json=json,
                           headers=headers, timeout=self.timeout)
        if r.status_code >= 300:
            # never include request headers (they hold the key); body of an error is safe
            raise DbError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None

    def _select_all(self, table: str, params: dict) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            page = self._request("GET", table, params={**params, "limit": PAGE, "offset": offset})
            rows.extend(page)
            if len(page) < PAGE:
                return rows
            offset += PAGE

    @staticmethod
    def _chunks(items: list, size: int = CHUNK):
        for i in range(0, len(items), size):
            yield items[i:i + size]

    # ---- sources / runs --------------------------------------------------
    def get_source(self, kind: str) -> dict:
        rows = self._request("GET", "sources", params={"kind": f"eq.{kind}", "select": "*"})
        if not rows:
            raise DbError(f"source '{kind}' not found (did the seed migration run?)")
        return rows[0]

    def start_run(self, source_id: int) -> int:
        rows = self._request("POST", "collector_runs", json={"source_id": source_id},
                             prefer="return=representation")
        return rows[0]["id"]

    def finish_run(self, run_id: int, status: str, items_seen=None, changes=None, error=None):
        self._request("PATCH", "collector_runs", params={"id": f"eq.{run_id}"}, json={
            "status": status, "finished_at": _now(), "items_seen": items_seen,
            "changes": changes, "error": (error or None) and error[:500],
        })

    def mark_source_ok(self, source: dict):
        self._request("PATCH", "sources", params={"id": f"eq.{source['id']}"}, json={
            "last_ok_at": _now(), "last_error": None, "consecutive_failures": 0, "down_since": None,
        })

    def mark_source_failed(self, source: dict, error: str) -> int:
        """Returns the new number of consecutive failures."""
        fresh = self._request("GET", "sources", params={"id": f"eq.{source['id']}", "select": "*"})[0]
        failures = (fresh.get("consecutive_failures") or 0) + 1
        self._request("PATCH", "sources", params={"id": f"eq.{source['id']}"}, json={
            "last_error": error[:500],
            "consecutive_failures": failures,
            "down_since": fresh.get("down_since") or _now(),
        })
        return failures

    def has_ok_run(self, source_id: int) -> bool:
        rows = self._request("GET", "collector_runs", params={
            "source_id": f"eq.{source_id}", "status": "eq.ok", "select": "id", "limit": 1})
        return bool(rows)

    def insert_events(self, rows: list[dict]):
        for chunk in self._chunks(rows, 500):
            self._request("POST", "events", json=chunk, prefer="return=minimal")

    # ---- reference data --------------------------------------------------
    def category_map(self) -> dict[str, str]:
        return {r["raw"]: r["tier"] for r in self._select_all("category_map", {"select": "raw,tier"})}

    # ---- state -----------------------------------------------------------
    def load_state(self, company_id: str) -> State:
        st = State()
        for r in self._select_all("packages", {"company_id": f"eq.{company_id}", "select": "*"}):
            st.packages[(r["external_key"], r["departure_city"])] = r
            st.package_city[r["id"]] = r["departure_city"]
        pkg_ids = sorted(st.package_city)
        dep_ids: list[int] = []
        for chunk in self._chunks(pkg_ids):
            rows = self._select_all("departures", {
                "package_id": f"in.({','.join(map(str, chunk))})",
                "select": "id,package_id,date,return_date,sold_out,active,missing_runs,is_manual"})
            for r in rows:
                st.departures[(r["package_id"], date.fromisoformat(r["date"]))] = r
                dep_ids.append(r["id"])
        for chunk in self._chunks(sorted(dep_ids)):
            rows = self._select_all("price_snapshots", {
                "departure_id": f"in.({','.join(map(str, chunk))})",
                "select": "departure_id,pax,price_usd",
                "order": "seen_at.desc,id.desc"})
            for r in rows:  # newest first: the first row per key is the last known price
                st.last_prices.setdefault((r["departure_id"], r["pax"]), r["price_usd"])
        # active manual prices (not cleared, not expired): a different automatic price next to them is a conflict
        now = _now()
        for chunk in self._chunks(sorted(dep_ids)):
            rows = self._select_all("manual_entries", {
                "departure_id": f"in.({','.join(map(str, chunk))})",
                "cleared_at": "is.null", "valid_until": f"gt.{now}", "price_usd": "not.is.null",
                "select": "departure_id,pax,price_usd", "order": "created_at.desc,id.desc"})
            for r in rows:  # newest first
                st.manual_prices.setdefault((r["departure_id"], r["pax"]), r["price_usd"])
        return st

    # ---- notifier --------------------------------------------------------
    def unsent_events(self, limit: int) -> list[dict]:
        return self._request("GET", "events", params={
            "telegram_sent_at": "is.null", "select": "*", "order": "id.asc", "limit": limit})

    def mark_events_sent(self, ids: list[int]):
        for chunk in self._chunks(sorted(ids)):
            self._request("PATCH", "events", params={"id": f"in.({','.join(map(str, chunk))})",
                                                     "telegram_sent_at": "is.null"},
                          json={"telegram_sent_at": _now()})

    def get_setting(self, key: str):
        rows = self._request("GET", "app_settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else None

    def _by_ids(self, table: str, ids, select: str, column: str = "id") -> list[dict]:
        out: list[dict] = []
        for chunk in self._chunks(sorted({i for i in ids if i is not None})):
            out.extend(self._select_all(table, {column: f"in.({','.join(map(str, chunk))})", "select": select}))
        return out

    def event_context(self, events: list[dict]) -> dict:
        """Everything the messages need: package/departure info and, for competitor price events,
        the price of the matched ETIHAD package on the same date."""
        packages = {r["id"]: r for r in self._by_ids(
            "packages", (e.get("package_id") for e in events), "id,external_key,name,tier,company_id")}
        departures = {r["id"]: r for r in self._by_ids(
            "departures", (e.get("departure_id") for e in events), "id,date,package_id")}
        our_prices: dict = {}
        priced = [e for e in events if e["type"] in ("price_down", "price_up") and e.get("company_id") != "B"]
        if priced:
            their = {e["package_id"] for e in priced}
            matches = [m for m in self._by_ids("package_matches", their, "our_package_id,their_package_id,active",
                                               column="their_package_id") if m["active"]]
            ours = {r["id"]: r for r in self._by_ids(
                "packages", (m["our_package_id"] for m in matches), "id,name,external_key")}
            wanted_dates = {str(departures[e["departure_id"]]["date"]) for e in priced if e["departure_id"] in departures}
            our_deps = [d for d in self._by_ids("departures", ours.keys(), "id,date,package_id", column="package_id")
                        if str(d["date"]) in wanted_dates]
            prices = {(r["departure_id"], r["pax"]): r["price_usd"] for r in self._by_ids(
                "price_current", (d["id"] for d in our_deps), "departure_id,pax,price_usd", column="departure_id")}
            for m in matches:
                for d in our_deps:
                    if d["package_id"] != m["our_package_id"]:
                        continue
                    for (dep_id, pax), price in prices.items():
                        if dep_id == d["id"] and price:
                            name = ours[m["our_package_id"]].get("name") or ours[m["our_package_id"]]["external_key"]
                            our_prices[(m["their_package_id"], str(d["date"]), pax)] = (name, price)
        return {"packages": packages, "departures": departures, "our_prices": our_prices}

    # ---- writes ----------------------------------------------------------
    def insert_packages(self, rows: list[dict]) -> list[dict]:
        return self._request("POST", "packages", json=rows, prefer="return=representation")

    def update_package(self, package_id: int, fields: dict):
        self._request("PATCH", "packages", params={"id": f"eq.{package_id}"}, json=fields)

    def insert_departures(self, rows: list[dict]) -> list[dict]:
        return self._request("POST", "departures", json=rows, prefer="return=representation")

    def update_departure(self, departure_id: int, fields: dict):
        self._request("PATCH", "departures", params={"id": f"eq.{departure_id}"}, json=fields)

    def insert_snapshots(self, rows: list[dict]):
        for chunk in self._chunks(rows, 500):
            self._request("POST", "price_snapshots", json=chunk, prefer="return=minimal")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
