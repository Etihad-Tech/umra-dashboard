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

    def mark_source_failed(self, source: dict, error: str):
        fresh = self._request("GET", "sources", params={"id": f"eq.{source['id']}", "select": "*"})[0]
        self._request("PATCH", "sources", params={"id": f"eq.{source['id']}"}, json={
            "last_error": error[:500],
            "consecutive_failures": (fresh.get("consecutive_failures") or 0) + 1,
            "down_since": fresh.get("down_since") or _now(),
        })

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
                "select": "id,package_id,date,return_date,sold_out,active,missing_runs"})
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
        return st

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
