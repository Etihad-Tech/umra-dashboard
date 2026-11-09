"""In-memory store with the same interface as db.RestStore."""
from datetime import date

from collector.db import State

CATEGORY_MAP = {"PREMIUM": "premium", "LUX": "lux", "LUXE": "lux", "KOMFORT": "comfort",
                "KOMFORT PLUS": "comfort", "COMFORT": "comfort", "EKONOM": "econom", "STANDART": "econom",
                "STANDART PLUS": "econom"}


class FakeStore:
    def __init__(self):
        self.source = {"id": 1, "company_id": "F", "kind": "mandarin_json", "enabled": True,
                       "config": {"departure_city": "TOSHKENT"}, "consecutive_failures": 0, "down_since": None}
        self.packages, self.departures, self.snapshots, self.runs, self.events = [], [], [], [], []
        self.source_ok = self.source_failed = 0
        self.settings, self.our_prices = {}, {}
        self.manual_prices = {}   # (departure_id, pax) -> price of an active manual entry
        self._ids = {"p": 0, "d": 0}

    # reference data
    def category_map(self):
        return dict(CATEGORY_MAP)

    def load_state(self, company_id):
        st = State()
        for p in self.packages:
            st.packages[(p["external_key"], p["departure_city"])] = dict(p)
            st.package_city[p["id"]] = p["departure_city"]
        for d in self.departures:
            st.departures[(d["package_id"], date.fromisoformat(d["date"]))] = dict(d)
        for s in reversed(self.snapshots):  # newest first
            st.last_prices.setdefault((s["departure_id"], s["pax"]), s["price_usd"])
            if s["price_usd"] is not None:
                st.last_known_prices.setdefault((s["departure_id"], s["pax"]), s["price_usd"])
        st.manual_prices = dict(self.manual_prices)
        return st

    # writes
    def insert_packages(self, rows):
        out = []
        for r in rows:
            self._ids["p"] += 1
            row = {**r, "id": self._ids["p"]}
            self.packages.append(row)
            out.append(dict(row))
        return out

    def update_package(self, pid, fields):
        next(p for p in self.packages if p["id"] == pid).update(fields)

    def insert_departures(self, rows):
        out = []
        for r in rows:
            self._ids["d"] += 1
            row = {"active": True, "missing_runs": 0, **r, "id": self._ids["d"]}
            self.departures.append(row)
            out.append(dict(row))
        return out

    def update_departure(self, did, fields):
        next(d for d in self.departures if d["id"] == did).update(fields)

    def insert_snapshots(self, rows):
        self.snapshots.extend(dict(r) for r in rows)

    # runs
    def get_source(self, kind):
        return self.source

    def start_run(self, source_id):
        self.runs.append({"id": len(self.runs) + 1, "status": "running"})
        return self.runs[-1]["id"]

    def finish_run(self, run_id, status, items_seen=None, changes=None, error=None):
        self.runs[run_id - 1].update(status=status, items_seen=items_seen, changes=changes, error=error)

    def mark_source_ok(self, source):
        self.source_ok += 1
        self.source["consecutive_failures"] = 0

    def mark_source_failed(self, source, error):
        self.source_failed += 1
        self.source["consecutive_failures"] = (self.source.get("consecutive_failures") or 0) + 1
        return self.source["consecutive_failures"]

    def has_ok_run(self, source_id):
        return any(r["status"] == "ok" for r in self.runs)

    def insert_events(self, rows):
        for r in rows:
            self.events.append({**r, "id": len(self.events) + 1, "telegram_sent_at": None})

    # notifier side
    def unsent_events(self, limit):
        return [dict(e) for e in self.events if e["telegram_sent_at"] is None][:limit]

    def mark_events_sent(self, ids):
        for e in self.events:
            if e["id"] in ids:
                e["telegram_sent_at"] = "now"

    def get_setting(self, key):
        return self.settings.get(key)

    def event_context(self, events):
        return {"packages": {p["id"]: p for p in self.packages},
                "departures": {d["id"]: d for d in self.departures},
                "our_prices": self.our_prices}
