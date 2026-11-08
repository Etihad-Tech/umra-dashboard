import unittest

from collector.db import DbError, RestStore


class FakeResponse:
    def __init__(self, status=200, data=None, text=None):
        self.status_code, self._data = status, data
        self.text = text if text is not None else ("x" if data is not None else "")

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.headers, self.calls, self.responses = {}, [], list(responses)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, params, json, headers))
        return self.responses.pop(0)


class RestStoreTests(unittest.TestCase):
    def test_pagination_reads_until_short_page(self):
        full = [{"raw": str(i), "tier": "lux"} for i in range(1000)]
        sess = FakeSession([FakeResponse(data=full), FakeResponse(data=[{"raw": "LAST", "tier": "econom"}])])
        got = RestStore("https://x.supabase.co", "KEY", session=sess).category_map()
        self.assertEqual(len(got), 1001)
        self.assertEqual([c[2]["offset"] for c in sess.calls], [0, 1000])

    def test_error_does_not_leak_the_key(self):
        sess = FakeSession([FakeResponse(status=401, data={}, text='{"message":"bad"}')])
        store = RestStore("https://x.supabase.co", "SECRET-KEY", session=sess)
        with self.assertRaises(DbError) as cm:
            store.category_map()
        self.assertNotIn("SECRET-KEY", str(cm.exception))

    def test_key_is_sent_as_headers_only(self):
        sess = FakeSession([])
        RestStore("https://x.supabase.co", "SECRET-KEY", session=sess)
        self.assertEqual(sess.headers["apikey"], "SECRET-KEY")

    def test_snapshots_are_inserted_in_chunks(self):
        sess = FakeSession([FakeResponse(data=None, text="")] * 3)
        store = RestStore("https://x.supabase.co", "K", session=sess)
        store.insert_snapshots([{"departure_id": 1, "pax": 4, "price_usd": 100}] * 1100)
        self.assertEqual([len(c[3]) for c in sess.calls], [500, 500, 100])


class NotifierQueries(unittest.TestCase):
    def store(self, *responses):
        sess = FakeSession(responses)
        return RestStore("https://x.supabase.co", "K", session=sess), sess

    def test_unsent_events_query(self):
        st, sess = self.store(FakeResponse(data=[]))
        st.unsent_events(500)
        method, url, params, *_ = sess.calls[0]
        self.assertEqual((method, params["telegram_sent_at"], params["order"], params["limit"]), ("GET", "is.null", "id.asc", 500))

    def test_mark_sent_only_touches_unsent_rows(self):
        st, sess = self.store(FakeResponse(data=None, text=""))
        st.mark_events_sent([3, 1, 2])
        method, url, params, body, _ = sess.calls[0]
        self.assertEqual((method, params["id"], params["telegram_sent_at"]), ("PATCH", "in.(1,2,3)", "is.null"))
        self.assertIn("telegram_sent_at", body)

    def test_insert_events_chunks_and_returns_minimal(self):
        st, sess = self.store(*[FakeResponse(data=None, text="")] * 2)
        st.insert_events([{"type": "sold_out"}] * 600)
        self.assertEqual([len(c[3]) for c in sess.calls], [500, 100])
        self.assertEqual(sess.calls[0][4], {"Prefer": "return=minimal"})

    def test_has_ok_run(self):
        st, sess = self.store(FakeResponse(data=[{"id": 1}]), FakeResponse(data=[]))
        self.assertTrue(st.has_ok_run(5))
        self.assertFalse(st.has_ok_run(5))
        self.assertEqual(sess.calls[0][2]["status"], "eq.ok")

    def test_get_setting(self):
        st, _ = self.store(FakeResponse(data=[{"value": {"chat_id": "-1"}}]), FakeResponse(data=[]))
        self.assertEqual(st.get_setting("telegram"), {"chat_id": "-1"})
        self.assertIsNone(st.get_setting("nothing"))

    def test_mark_source_failed_counts_and_keeps_down_since(self):
        st, sess = self.store(FakeResponse(data=[{"consecutive_failures": 2, "down_since": "2026-11-08T00:00:00Z"}]),
                              FakeResponse(data=None, text=""))
        self.assertEqual(st.mark_source_failed({"id": 1}, "boom"), 3)
        body = sess.calls[1][3]
        self.assertEqual((body["consecutive_failures"], body["down_since"]), (3, "2026-11-08T00:00:00Z"))

    def test_event_context_finds_the_matched_etihad_price(self):
        ev = {"id": 1, "type": "price_down", "company_id": "F", "package_id": 1, "departure_id": 10,
              "old": {"pax": 4, "price_usd": 2290}, "new": {"pax": 4, "price_usd": 2140}}
        st, sess = self.store(
            FakeResponse(data=[{"id": 1, "external_key": "HYATT-09", "name": "Hyatt", "tier": "lux", "company_id": "F"}]),
            FakeResponse(data=[{"id": 10, "date": "2026-12-12", "package_id": 1}]),
            FakeResponse(data=[{"our_package_id": 2, "their_package_id": 1, "active": True}]),
            FakeResponse(data=[{"id": 2, "name": "Swissotel 10", "external_key": "SWISS-10"}]),
            FakeResponse(data=[{"id": 20, "date": "2026-12-12", "package_id": 2},
                               {"id": 21, "date": "2027-01-01", "package_id": 2}]),
            FakeResponse(data=[{"departure_id": 20, "pax": 4, "price_usd": 1990}]))
        ctx = st.event_context([ev])
        self.assertEqual(ctx["our_prices"], {(1, "2026-12-12", 4): ("Swissotel 10", 1990)})
        self.assertEqual(ctx["packages"][1]["external_key"], "HYATT-09")

    def test_inactive_match_is_ignored(self):
        ev = {"id": 1, "type": "price_up", "company_id": "S", "package_id": 1, "departure_id": 10,
              "old": {"pax": 4, "price_usd": 1}, "new": {"pax": 4, "price_usd": 2}}
        st, _ = self.store(
            FakeResponse(data=[{"id": 1, "external_key": "X", "name": "x", "tier": None, "company_id": "S"}]),
            FakeResponse(data=[{"id": 10, "date": "2026-12-12", "package_id": 1}]),
            FakeResponse(data=[{"our_package_id": 2, "their_package_id": 1, "active": False}]),
            FakeResponse(data=[]), FakeResponse(data=[]), FakeResponse(data=[]))
        self.assertEqual(st.event_context([ev])["our_prices"], {})


if __name__ == "__main__":
    unittest.main()
