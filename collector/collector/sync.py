"""Write parsed Mandarin packets into the database.

The logic talks to a ``store`` object (see db.RestStore), so it can be tested with an in-memory fake.

Rules (TASK sections 5, 8):
  * a price snapshot is written ONLY when the value differs from the last one;
  * NULL = "no data" (never 0); a first-time NULL is not worth a row;
  * a departure missing from the feed gets missing_runs+1, and is deactivated after 2 runs in a row;
  * category is normalised through the category_map table; unknown -> tier NULL (shown in unmapped_categories).
"""
from __future__ import annotations

from dataclasses import dataclass

from .mandarin import Packet

MISSING_RUNS_LIMIT = 2


@dataclass
class SyncStats:
    items_seen: int = 0
    new_packages: int = 0
    updated_packages: int = 0
    new_departures: int = 0
    snapshots: int = 0
    deactivated: int = 0

    @property
    def changes(self) -> int:
        return self.new_packages + self.new_departures + self.snapshots


def tier_for(category_raw: str | None, category_map: dict[str, str]) -> str | None:
    if not category_raw:
        return None
    return category_map.get(category_raw.strip().upper())


def sync_packets(store, source: dict, run_id: int, packets: list[Packet], city: str) -> SyncStats:
    company_id = source["company_id"]
    stats = SyncStats(items_seen=len(packets))
    category_map = store.category_map()
    state = store.load_state(company_id)  # packages, departures, last_prices

    # ---- packages ---------------------------------------------------------
    wanted: dict[tuple[str, str], dict] = {}
    for p in packets:
        wanted.setdefault((p.family, p.city), {
            "company_id": company_id,
            "external_key": p.family,
            "name": p.title,
            "category_raw": p.category_raw,
            "tier": tier_for(p.category_raw, category_map),
            "nights": p.nights,
            "departure_city": p.city,
            "hotels": p.hotels,
            "services": p.services,
            "active": True,
        })

    package_ids: dict[tuple[str, str], int] = {}
    to_insert = []
    for key, row in wanted.items():
        existing = state.packages.get(key)
        if existing is None:
            to_insert.append(row)
            continue
        package_ids[key] = existing["id"]
        diff = {k: v for k, v in row.items() if existing.get(k) != v}
        if diff:
            store.update_package(existing["id"], diff)
            stats.updated_packages += 1
    if to_insert:
        for created in store.insert_packages(to_insert):
            package_ids[(created["external_key"], created["departure_city"])] = created["id"]
        stats.new_packages = len(to_insert)

    # ---- departures -------------------------------------------------------
    departure_ids: dict[tuple[int, object], int] = {}
    new_departures = []
    for p in packets:
        pkg_id = package_ids[(p.family, p.city)]
        existing = state.departures.get((pkg_id, p.date))
        if existing is None:
            row = {"package_id": pkg_id, "date": p.date.isoformat(),
                   "return_date": p.return_date.isoformat() if p.return_date else None}
            if p.sold_out is not None:
                row["sold_out"] = p.sold_out
            new_departures.append(row)
            continue
        departure_ids[(pkg_id, p.date)] = existing["id"]
        diff = {}
        if p.return_date and existing.get("return_date") != p.return_date.isoformat():
            diff["return_date"] = p.return_date.isoformat()
        if p.sold_out is not None and bool(existing.get("sold_out")) != p.sold_out:
            diff["sold_out"] = p.sold_out
        if not existing.get("active"):
            diff["active"] = True
        if existing.get("missing_runs"):
            diff["missing_runs"] = 0
        if diff:
            store.update_departure(existing["id"], diff)
    if new_departures:
        for created in store.insert_departures(new_departures):
            departure_ids[(created["package_id"], _as_date(created["date"]))] = created["id"]
        stats.new_departures = len(new_departures)

    # ---- price snapshots (only on change) ---------------------------------
    snapshots = []
    for p in packets:
        dep_id = departure_ids[(package_ids[(p.family, p.city)], p.date)]
        for pax, price in p.prices.items():
            last = state.last_prices.get((dep_id, pax), "none")
            if last == "none":
                if price is None:
                    continue            # never seen, still no price: nothing to record
            elif last == price:
                continue
            snapshots.append({"departure_id": dep_id, "pax": pax, "price_usd": price,
                              "source_id": source["id"], "run_id": run_id})
    if snapshots:
        store.insert_snapshots(snapshots)
        stats.snapshots = len(snapshots)

    # ---- departures that disappeared from the feed ------------------------
    seen_ids = set(departure_ids.values())
    for (pkg_id, _d), dep in state.departures.items():
        if dep["id"] in seen_ids or not dep.get("active"):
            continue
        if state.package_city.get(pkg_id) != city:
            continue                    # other departure city is not part of this run
        missing = (dep.get("missing_runs") or 0) + 1
        if missing >= MISSING_RUNS_LIMIT:
            store.update_departure(dep["id"], {"missing_runs": missing, "active": False})
            stats.deactivated += 1
        else:
            store.update_departure(dep["id"], {"missing_runs": missing})
    return stats


def _as_date(value):
    from datetime import date
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
