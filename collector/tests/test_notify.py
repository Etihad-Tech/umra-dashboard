import json
import logging
import os
import unittest
from pathlib import Path
from unittest import mock

import requests

from collector import notify, run as runner
from collector.notify import Telegram, TelegramError, build_messages, format_event, notify_pass
from tests.fakes import FakeStore

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "mandarin_sample.json").read_text())
TOKEN = "123456:SECRET-TOKEN-VALUE"

CTX = {
    "packages": {1: {"id": 1, "external_key": "HYATT-09", "name": "Hyatt", "tier": "lux", "company_id": "F"},
                 2: {"id": 2, "external_key": "SWISS-10", "name": "Swissotel 10", "tier": "comfort", "company_id": "B"}},
    "departures": {10: {"id": 10, "date": "2026-12-12", "package_id": 1}},
    "our_prices": {},
}


def ev(i, kind, company="F", **kw):
    base = {"id": i, "type": kind, "company_id": company, "package_id": 1, "departure_id": 10, "old": None, "new": None,
            "is_manual": False}
    base.update(kw)
    return base


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Formatting(unittest.TestCase):
    def test_price_down_matches_the_spec_example(self):
        e = ev(1, "price_down", old={"pax": 4, "price_usd": 2290}, new={"pax": 4, "price_usd": 2140})
        self.assertEqual(format_event(e, CTX), "🔻 MANDARIN · Lux · HYATT-09 · 12.12 — 4 kishi: $2,290 → $2,140 (−$150)")

    def test_price_up_sign(self):
        e = ev(1, "price_up", old={"pax": 2, "price_usd": 1000}, new={"pax": 2, "price_usd": 1100})
        self.assertTrue(format_event(e, CTX).endswith("$1,000 → $1,100 (+$100)"))

    def test_comparison_with_our_matched_package(self):
        ctx = dict(CTX, our_prices={(1, "2026-12-12", 4): ("Swissotel 10", 1990)})
        e = ev(1, "price_down", old={"pax": 4, "price_usd": 2290}, new={"pax": 4, "price_usd": 2140})
        self.assertTrue(format_event(e, ctx).endswith(" · ETIHAD Swissotel 10: $1,990"))
        e2 = ev(2, "price_down", old={"pax": 3, "price_usd": 2290}, new={"pax": 3, "price_usd": 2140})
        self.assertNotIn("ETIHAD Swiss", format_event(e2, ctx))      # other pax: no comparison

    def test_other_event_types(self):
        self.assertIn("yangi sana", format_event(ev(1, "new_departure"), CTX))
        self.assertIn("yangi paket (3 ta sana)", format_event(ev(1, "new_package", new={"departures": 3}), CTX))
        self.assertIn("sotilib bo'ldi", format_event(ev(1, "sold_out"), CTX))
        self.assertIn("yana sotuvda", format_event(ev(1, "back_on_sale"), CTX))
        self.assertIn("olib tashlandi", format_event(ev(1, "removed"), CTX))
        self.assertIn("ishlamayapti (3 marta", format_event(ev(1, "source_down", package_id=None, departure_id=None, new={"failures": 3}), CTX))
        self.assertIn("qayta ishlayapti", format_event(ev(1, "source_recovered", package_id=None, departure_id=None), CTX))

    def test_manual_entry_from_the_database_trigger(self):
        e = ev(1, "manual_entry", is_manual=True, old={"pax": 4, "price_usd": 2290},
               new={"pax": 4, "price_usd": 2100, "sold_out": None, "note": "sayt ishlamayapti"})
        text = format_event(e, CTX)
        self.assertIn("qo'lda kiritildi: 4 kishi $2,100 (avto: $2,290)", text)
        self.assertIn("sayt ishlamayapti", text)

    def test_unknown_type_does_not_crash(self):
        self.assertIn("something_new", format_event(ev(1, "something_new"), CTX))


class Grouping(unittest.TestCase):
    def test_thirty_changes_of_one_company_are_one_message(self):
        events = [ev(i, "price_down", old={"pax": 4, "price_usd": 2000}, new={"pax": 4, "price_usd": 1900}) for i in range(1, 31)]
        msgs = build_messages(events, CTX)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(len(msgs[0][0].splitlines()), 30)
        self.assertEqual(msgs[0][1], list(range(1, 31)))

    def test_one_message_per_company_in_fixed_order(self):
        events = [ev(1, "sold_out", "F"), ev(2, "sold_out", "S"), ev(3, "sold_out", "B"), ev(4, "sold_out", "F")]
        msgs = build_messages(events, CTX)
        self.assertEqual([m[1] for m in msgs], [[3], [2], [1, 4]])       # ETIHAD, SAFO, MANDARIN

    def test_long_batches_are_split_below_the_telegram_limit(self):
        events = [ev(i, "sold_out") for i in range(1, 400)]
        msgs = build_messages(events, CTX)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(t) <= 4096 for t, _ in msgs))
        self.assertEqual(sorted(i for _, ids in msgs for i in ids), list(range(1, 400)))   # nothing lost or doubled

    def test_a_malformed_event_cannot_block_the_queue(self):
        bad = ev(1, "price_down", old=None, new=None)                    # missing prices
        good = ev(2, "sold_out")
        msgs = build_messages([bad, good], CTX)
        self.assertEqual(msgs[0][1], [1, 2])
        self.assertIn("sotilib bo'ldi", msgs[0][0])


class Sender(unittest.TestCase):
    def test_ok(self):
        s = FakeSession(Resp(200))
        Telegram(TOKEN, "-100", session=s).send("hi")
        self.assertEqual(s.calls[0][1], {"chat_id": "-100", "text": "hi", "disable_web_page_preview": True})

    def test_429_waits_retry_after_then_succeeds(self):
        sleeps = []
        s = FakeSession(Resp(429, {"parameters": {"retry_after": 7}}), Resp(200))
        Telegram(TOKEN, "-100", session=s, sleep=sleeps.append).send("hi")
        self.assertEqual(sleeps, [8])
        self.assertEqual(len(s.calls), 2)

    def test_endless_429_gives_up(self):
        s = FakeSession(*[Resp(429, {"parameters": {"retry_after": 1}})] * 4)
        with self.assertRaises(TelegramError):
            Telegram(TOKEN, "-100", session=s, sleep=lambda x: None).send("hi")

    def test_server_error_raises(self):
        with self.assertRaises(TelegramError) as cm:
            Telegram(TOKEN, "-100", session=FakeSession(Resp(400, {"description": "Bad Request: chat not found"}))).send("hi")
        self.assertIn("chat not found", str(cm.exception))

    def test_network_error_never_leaks_the_token(self):
        err = requests.ConnectionError(f"HTTPSConnectionPool: url: /bot{TOKEN}/sendMessage")
        with self.assertRaises(TelegramError) as cm:
            Telegram(TOKEN, "-100", session=FakeSession(err)).send("hi")
        self.assertNotIn("SECRET-TOKEN", str(cm.exception))
        self.assertNotIn("SECRET-TOKEN", repr(cm.exception.__cause__))
        self.assertIsNone(cm.exception.__cause__)
        self.assertTrue(cm.exception.__suppress_context__, 'the original error (it holds the token URL) must not be chained')


class Pass(unittest.TestCase):
    def store_with_changes(self):
        s = FakeStore()
        with mock.patch.object(runner, "fetch_feed", return_value=SAMPLE):
            runner.run_once(s, "http://x")                                # seed
        feed = json.loads(json.dumps(SAMPLE))
        feed["packets"][2]["prices"][0]["amount"] = "1,090 $"
        feed["packets"][3]["prices"][0]["amount"] = "1,300 $"
        with mock.patch.object(runner, "fetch_feed", return_value=feed):
            runner.run_once(s, "http://x")
        return s

    def test_end_to_end_sync_to_one_message_and_marked_sent(self):
        s = self.store_with_changes()
        sess = FakeSession(Resp(200))
        res = notify_pass(s, Telegram(TOKEN, "-100", session=sess))
        self.assertEqual((res["messages"], res["events"], res["failed"]), (1, 2, False))
        text = sess.calls[0][1]["text"]
        self.assertIn("HYATT-09", text); self.assertIn("🔻", text); self.assertIn("🔺", text)
        self.assertTrue(all(e["telegram_sent_at"] for e in s.events))

    def test_restart_does_not_resend(self):
        s = self.store_with_changes()
        notify_pass(s, Telegram(TOKEN, "-100", session=FakeSession(Resp(200))))
        sess = FakeSession()                                               # any call would raise IndexError
        res = notify_pass(s, Telegram(TOKEN, "-100", session=sess))
        self.assertEqual((res["messages"], sess.calls), (0, []))

    def test_failed_send_leaves_events_unsent_and_next_pass_retries(self):
        s = self.store_with_changes()
        res = notify_pass(s, Telegram(TOKEN, "-100", session=FakeSession(Resp(500, {}))))
        self.assertTrue(res["failed"])
        self.assertTrue(all(e["telegram_sent_at"] is None for e in s.events))
        res = notify_pass(s, Telegram(TOKEN, "-100", session=FakeSession(Resp(200))))
        self.assertEqual(res["events"], 2)

    def test_second_message_failing_keeps_only_the_first_marked(self):
        s = FakeStore()
        s.insert_events([ev(0, "sold_out", "B"), ev(0, "sold_out", "F")])
        sess = FakeSession(Resp(200), Resp(500, {}))
        res = notify_pass(s, Telegram(TOKEN, "-100", session=sess), sleep=lambda x: None)
        self.assertTrue(res["failed"])
        self.assertEqual([bool(e["telegram_sent_at"]) for e in s.events], [True, False])

    def test_dry_run_sends_and_marks_nothing(self):
        s = self.store_with_changes()
        res = notify_pass(s, None, dry_run=True)
        self.assertEqual(res["messages"], 1)
        self.assertTrue(all(e["telegram_sent_at"] is None for e in s.events))

    def test_token_never_appears_in_logs(self):
        s = self.store_with_changes()
        err = requests.ConnectionError(f"url /bot{TOKEN}/sendMessage")
        with self.assertLogs("collector.notify", level="ERROR") as logs:
            notify_pass(s, Telegram(TOKEN, "-100", session=FakeSession(err)))
        self.assertNotIn("SECRET-TOKEN", "\n".join(logs.output))


class Config(unittest.TestCase):
    def test_missing_token(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertLogs("collector.notify", level="ERROR"):
            self.assertIsNone(notify._load_sender(FakeStore()))

    def test_muted_in_settings(self):
        s = FakeStore(); s.settings["telegram"] = {"chat_id": "-1", "enabled": False}
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN}, clear=True):
            self.assertIsNone(notify._load_sender(s))

    def test_chat_id_from_settings_beats_env(self):
        s = FakeStore(); s.settings["telegram"] = {"chat_id": "-111"}
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-222"}, clear=True):
            self.assertEqual(notify._load_sender(s).chat_id, "-111")

    def test_chat_id_from_env_as_fallback(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-222"}, clear=True):
            self.assertEqual(notify._load_sender(FakeStore()).chat_id, "-222")

    def test_no_chat_id_anywhere(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN}, clear=True), self.assertLogs("collector.notify", level="ERROR"):
            self.assertIsNone(notify._load_sender(FakeStore()))


if __name__ == "__main__":
    unittest.main()


class ManualConflictText(unittest.TestCase):
    def test_shows_both_prices(self):
        from collector.notify import format_event
        ev = {"type": "manual_conflict", "package_id": 1, "departure_id": 1,
              "old": {"pax": 4, "price_usd": 1000}, "new": {"pax": 4, "price_usd": 1090}}
        txt = format_event(ev, {"packages": {}, "departures": {}, "our_prices": {}})
        self.assertIn("$1,000", txt)
        self.assertIn("$1,090", txt)
