"""Write parsed packets into the database and record what changed.

The logic talks to a ``store`` object (see db.RestStore), so it can be tested with an in-memory fake.

Rules (TASK sections 5, 8, 9):
  * a price snapshot is written ONLY when the value differs from the last one;
  * NULL = "no data" (never 0); a first-time NULL is not worth a row;
  * a departure missing from the feed gets missing_runs+1, and is deactivated ("removed") after 2 runs in a row;
  * category is normalised through the category_map table; unknown -> tier NULL (shown in unmapped_categories);
  * events (price_up/down, new_package, new_departure, sold_out, back_on_sale, removed, manual_conflict) are recorded for the notifier.
    The very first successful run of a source is a "seed": it fills the database but creates NO events,
    otherwise the first run would announce every package as new.
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
    events: int = 0

    @property
    def changes(self) -> int:
        return self.new_packages + self.new_departures + self.snapshots


def tier_for(category_raw: str | None, category_map: dict[str, str]) -> str | None:
    if not category_raw:
        return None
    return category_map.get(category_raw.strip().upper())


def sync_packets(store, source: dict, run_id: int, packets: list[Packet], city: str,
                 emit_events: bool = True) -> SyncStats:
    company_id = source["company_id"]
    stats = SyncStats(items_seen=len(packets))
    category_map = store.category_map()
    state = store.load_state(company_id)  # packages, departures, last_prices

    def emit(rows: list[dict]) -> None:
        """Record events right after the database write they describe."""
        if emit_events and rows:
            store.insert_events(rows)
            stats.events += len(rows)

    def event(kind, package_id=None, departure_id=None, old=None, new=None) -> dict:
        return {"type": kind, "company_id": company_id, "package_id": package_id,
                "departure_id": departure_id, "old": old, "new": new, "is_manual": False}

    # ---- packages ---------------------------------------------------------
    wanted: dict[tuple[str, str], dict] = {}
    dep_count: dict[tuple[str, str], int] = {}
    for p in packets:
        dep_count[(p.family, p.city)] = dep_count.get((p.family, p.city), 0) + 1
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
    new_package_ids: set[int] = set()
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
        events = []
        for created in store.insert_packages(to_insert):
            key = (created["external_key"], created["departure_city"])
            package_ids[key] = created["id"]
            new_package_ids.add(created["id"])
            events.append(event("new_package", created["id"], new={"departures": dep_count[key]}))
        stats.new_packages = len(to_insert)
        emit(events)

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
            emit([event("sold_out" if p.sold_out else "back_on_sale", pkg_id, existing["id"],
                        old={"sold_out": not p.sold_out}, new={"sold_out": p.sold_out})])
        if not existing.get("active"):
            diff["active"] = True
        if existing.get("missing_runs"):
            diff["missing_runs"] = 0
        if diff:
            store.update_departure(existing["id"], diff)
    if new_departures:
        events = []
        for created in store.insert_departures(new_departures):
            departure_ids[(created["package_id"], _as_date(created["date"]))] = created["id"]
            if created["package_id"] not in new_package_ids:   # a new package is announced once, not per date
                events.append(event("new_departure", created["package_id"], created["id"]))
        stats.new_departures = len(new_departures)
        emit(events)

    # ---- price snapshots (only on change) ---------------------------------
    snapshots = []
    price_events = []
    for p in packets:
        pkg_id = package_ids[(p.family, p.city)]
        dep_id = departure_ids[(pkg_id, p.date)]
        for pax, price in p.prices.items():
            last = state.last_prices.get((dep_id, pax), "none")
            if last == "none":
                if price is None:
                    continue            # never seen, still no price: nothing to record
            elif last == price:
                continue
            manual = state.manual_prices.get((dep_id, pax))
            if manual is not None and price is not None and price != manual:
                # a person entered a price; the source now says something different: show both, never overwrite silently
                price_events.append(event("manual_conflict", pkg_id, dep_id,
                                          old={"pax": pax, "price_usd": manual},
                                          new={"pax": pax, "price_usd": price}))
            if last != "none" and last is not None and price is not None:
                price_events.append(event("price_down" if price < last else "price_up", pkg_id, dep_id,
                                          old={"pax": pax, "price_usd": last},
                                          new={"pax": pax, "price_usd": price}))
            snapshots.append({"departure_id": dep_id, "pax": pax, "price_usd": price,
                              "source_id": source["id"], "run_id": run_id})
    emit(price_events)                  # before the snapshots: a crash may repeat an alert, never lose one
    if snapshots:
        store.insert_snapshots(snapshots)
        stats.snapshots = len(snapshots)

    # ---- departures that disappeared from the feed ------------------------
    seen_ids = set(departure_ids.values())
    removed = []
    for (pkg_id, _d), dep in state.departures.items():
        if dep["id"] in seen_ids or not dep.get("active") or dep.get("is_manual"):
            continue                    # hand-added dates are not the source's to remove
        if state.package_city.get(pkg_id) != city:
            continue                    # other departure city is not part of this run
        missing = (dep.get("missing_runs") or 0) + 1
        if missing >= MISSING_RUNS_LIMIT:
            store.update_departure(dep["id"], {"missing_runs": missing, "active": False})
            stats.deactivated += 1
            removed.append(event("removed", pkg_id, dep["id"]))
        else:
            store.update_departure(dep["id"], {"missing_runs": missing})
    emit(removed)
    return stats


def _as_date(value):
    from datetime import date
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
