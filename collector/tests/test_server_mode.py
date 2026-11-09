import unittest
from datetime import datetime, timezone
from unittest import mock

from collector import healthcheck, run as runner
from tests.fakes import FakeStore

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


    def test_the_watchdog_hears_every_finished_cycle(self):
        beats = []

        class Dog:
            def beat(self, interval):
                beats.append(interval)

        def sleep(sec):
            if len(beats) == 2:
                raise SystemExit

        with self.assertRaises(SystemExit):
            runner.loop_forever(lambda: 1 / 0, lambda: 300, sleep, watchdog=Dog())   # a failed cycle still counts
        self.assertEqual(beats, [300, 300])


class Isolation(unittest.TestCase):
    """One hiccup skips only that piece of the cycle."""

    def test_a_database_error_starting_one_source_does_not_skip_the_others_or_telegram(self):
        store, done = FakeStore(), []
        with mock.patch.object(runner, "run_once", side_effect=OSError("db blip")), \
             mock.patch.object(runner, "run_safo", side_effect=lambda *a, **kw: done.append("safo")), \
             mock.patch.object(runner, "run_etihad", side_effect=lambda *a, **kw: done.append("etihad")), \
             mock.patch.object(runner.notify, "run_from_env", side_effect=lambda *a, **kw: done.append("notify")), \
             self.assertLogs("collector", "ERROR") as logs:
            runner.run_cycle(store, ["mandarin", "safo", "etihad"], {"mandarin": "m", "safo": "s", "etihad": "e"},
                             "k", do_notify=True)
        self.assertEqual(done, ["safo", "etihad", "notify"])
        self.assertIn("mandarin: run aborted: OSError", logs.output[0])

    def test_get_source_failing_is_caught(self):
        store = FakeStore()
        store.get_source = mock.Mock(side_effect=RuntimeError("https://x.test/?key=SECRET"))
        with self.assertLogs("collector", "ERROR") as logs:
            runner.run_cycle(store, ["mandarin"], {"mandarin": "m"})
        self.assertNotIn("SECRET", "\n".join(logs.output))

    def test_a_failing_notifier_is_caught(self):
        with mock.patch.object(runner.notify, "run_from_env", side_effect=KeyError("x")), \
             self.assertLogs("collector", "ERROR") as logs:
            runner.run_cycle(FakeStore(), [], {}, do_notify=True)
        self.assertIn("notify: pass aborted: KeyError", logs.output[0])


class WatchdogTests(unittest.TestCase):
    def dog(self, interval=900):
        self.now, self.exits = 0.0, []
        dog = runner.Watchdog(clock=lambda: self.now, exit_process=self.exits.append)
        dog.beat(interval)
        return dog

    def test_limit_is_three_intervals_or_twenty_minutes(self):
        dog = self.dog(900)
        self.now = 45 * 60
        self.assertTrue(dog.check())
        self.now = 45 * 60 + 1
        self.assertFalse(dog.check())
        self.assertEqual(self.exits, [70])
        dog = self.dog(60)                                          # 3 x 1 min < 20 min: 20 min wins
        self.now = 20 * 60 + 1
        dog.check()
        self.assertEqual(self.exits, [70])

    def test_a_beat_resets_the_timer(self):
        dog = self.dog(900)
        self.now = 40 * 60
        dog.beat(900)
        self.now = 80 * 60
        self.assertTrue(dog.check())
        self.assertEqual(self.exits, [])

    def test_the_thread_exits_a_stuck_process(self):
        clock = iter([0, 0, 10 ** 6])                               # created, first check fine, then stuck
        exits, sleeps = [], []
        dog = runner.Watchdog(clock=lambda: next(clock), exit_process=exits.append, sleep=sleeps.append)
        t = dog.start()
        t.join(timeout=5)
        self.assertEqual((exits, sleeps), ([70], [60]))
        self.assertTrue(t.daemon)


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

    def test_manual_source_is_not_a_collector(self):
        self.assertEqual(healthcheck.stale_sources([self.row(0, last_ok_at=None, kind="manual"), self.row(5)], NOW), [])

    def test_trailing_slash_in_the_url_is_ignored(self):
        env = {"SUPABASE_URL": "https://x.supabase.co/", "SUPABASE_SERVICE_ROLE_KEY": "K"}
        # main() reads the real clock, so the row must be fresh against it (not against the fixed NOW)
        fresh = dict(self.row(0), last_ok_at=datetime.now(timezone.utc).isoformat())
        resp = mock.Mock(**{"json.return_value": [fresh], "raise_for_status.return_value": None})
        with mock.patch.dict("os.environ", env), mock.patch("requests.get", return_value=resp) as get:
            self.assertEqual(healthcheck.main(), 0)
        url = get.call_args.args[0]
        self.assertTrue(url.startswith("https://x.supabase.co/rest/v1/sources?"), url)
        self.assertIn("kind", url)

    def test_main_reports_without_leaking_the_key(self):
        env = {"SUPABASE_URL": "https://x.test", "SUPABASE_SERVICE_ROLE_KEY": "TOPSECRET"}
        with mock.patch.dict("os.environ", env), mock.patch("requests.get", side_effect=__import__("requests").ConnectionError("TOPSECRET")):
            with mock.patch("sys.stderr") as err:
                self.assertEqual(healthcheck.main(), 1)
        self.assertNotIn("TOPSECRET", "".join(str(c) for c in err.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
