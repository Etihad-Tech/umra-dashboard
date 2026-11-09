import unittest
from datetime import datetime, timezone
from unittest import mock

from collector import healthcheck, run as runner

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


class LoopForever(unittest.TestCase):
    def test_a_failing_cycle_does_not_stop_the_loop(self):
        calls, sleeps = [], []

        def cycle():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database is down https://x.test/?key=SECRET")

        def sleep(sec):
            sleeps.append(sec)
            if len(sleeps) == 3:
                raise SystemExit

        with self.assertLogs("collector", "ERROR") as logs, self.assertRaises(SystemExit):
            runner.loop_forever(cycle, lambda: 900, sleep)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [900, 900, 900])
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("SECRET", logs.output[0])

    def test_wait_is_clamped_and_survives_a_broken_interval_lookup(self):
        sleeps = []

        def sleep(sec):
            sleeps.append(sec)
            if len(sleeps) == 3:
                raise SystemExit

        waits = iter([5, 10 ** 9])

        def interval():
            try:
                return next(waits)
            except StopIteration:
                raise OSError("db down")

        with self.assertRaises(SystemExit):
            runner.loop_forever(lambda: None, interval, sleep)
        self.assertEqual(sleeps, [60, 24 * 3600, 900])


class Health(unittest.TestCase):
    def row(self, minutes_ago, **kw):
        from datetime import timedelta
        base = {"company_id": "S", "enabled": True, "interval_min": 15,
                "last_ok_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}
        base.update(kw)
        return base

    def test_fresh_stale_never_and_disabled(self):
        self.assertEqual(healthcheck.stale_sources([self.row(10), self.row(50)], NOW), [])          # limit is 3*15+5 = 50
        self.assertEqual(len(healthcheck.stale_sources([self.row(51)], NOW)), 1)
        self.assertEqual(healthcheck.stale_sources([self.row(0, last_ok_at=None, company_id="F")], NOW),
                         ["MANDARIN: never succeeded"])
        self.assertEqual(healthcheck.stale_sources([self.row(999, enabled=False)], NOW), [])
        self.assertEqual(healthcheck.stale_sources([self.row(100, interval_min=60)], NOW), [])      # own interval counts
        self.assertEqual(healthcheck.stale_sources([self.row(0, last_ok_at="garbage")], NOW), ["SAFO: never succeeded"])

    def test_main_reports_without_leaking_the_key(self):
        env = {"SUPABASE_URL": "https://x.test", "SUPABASE_SERVICE_ROLE_KEY": "TOPSECRET"}
        with mock.patch.dict("os.environ", env), mock.patch("requests.get", side_effect=__import__("requests").ConnectionError("TOPSECRET")):
            with mock.patch("sys.stderr") as err:
                self.assertEqual(healthcheck.main(), 1)
        self.assertNotIn("TOPSECRET", "".join(str(c) for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
