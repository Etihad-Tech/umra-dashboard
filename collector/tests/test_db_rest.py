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


if __name__ == "__main__":
    unittest.main()
