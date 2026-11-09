"""Telegram notifier: turns unsent rows of `events` into short Uzbek messages.

  python -m collector.notify [--dry-run] [--loop]

Rules (TASK section 9.2):
  * one message per company per pass (a run that changes 30 prices sends ONE message);
  * idempotent: only events with telegram_sent_at IS NULL are sent, and it is set right after Telegram accepts the message;
  * 429 -> wait `retry_after` and retry; any other failure leaves the events unsent for the next pass;
  * the bot token lives ONLY in the environment (TELEGRAM_BOT_TOKEN). It is never logged: error messages never contain
    the request URL, because that URL contains the token.
Chat id: app_settings key "telegram" -> {"chat_id": "...", "enabled": true}; falls back to env TELEGRAM_CHAT_ID.
Run ONE notifier process (two at the same time could send the same event twice).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import requests

from .db import RestStore

log = logging.getLogger("collector.notify")

COMPANY = {"B": "ETIHAD", "S": "SAFO", "F": "MANDARIN"}
COMPANY_ORDER = ["B", "S", "F", None]
TIER = {"premium": "Premium", "lux": "Lux", "comfort": "Komfort", "econom": "Ekonom"}
MAX_MESSAGE = 3800            # Telegram's limit is 4096 characters
PER_PASS = 500


class TelegramError(Exception):
    pass


# ------------------------------------------------------------------ formatting
def money(n) -> str:
    return f"${int(n):,}"


def short_date(value) -> str:
    s = str(value)
    return f"{s[8:10]}.{s[5:7]}" if len(s) >= 10 else s


def _where(ev: dict, ctx: dict) -> str:
    parts = [COMPANY.get(ev.get("company_id"), "—")]
    pkg = ctx["packages"].get(ev.get("package_id")) or {}
    dep = ctx["departures"].get(ev.get("departure_id")) or {}
    if pkg.get("tier"):
        parts.append(TIER.get(pkg["tier"], pkg["tier"]))
    if pkg.get("external_key"):
        parts.append(str(pkg["external_key"]))
    if dep.get("date"):
        parts.append(short_date(dep["date"]))
    return " · ".join(parts)


def _our_price(ev: dict, ctx: dict) -> str:
    """' · ETIHAD <name>: $1,990' when the competitor package is matched to one of ours."""
    dep = ctx["departures"].get(ev.get("departure_id")) or {}
    pax = (ev.get("new") or {}).get("pax")
    hit = ctx["our_prices"].get((ev.get("package_id"), str(dep.get("date")), pax))
    return f" · ETIHAD {hit[0]}: {money(hit[1])}" if hit else ""


def format_event(ev: dict, ctx: dict) -> str:
    kind = ev.get("type")
    where = _where(ev, ctx)
    old, new = ev.get("old") or {}, ev.get("new") or {}
    if kind in ("price_down", "price_up"):
        a, b = int(old.get("price_usd")), int(new.get("price_usd"))
        icon, sign = ("🔻", "−") if kind == "price_down" else ("🔺", "+")
        return (f"{icon} {where} — {new.get('pax')} kishi: {money(a)} → {money(b)} ({sign}{money(abs(b - a))})"
                + _our_price(ev, ctx))
    if kind == "new_departure":
        return f"🆕 {where} — yangi sana"
    if kind == "new_package":
        n = new.get("departures")
        return f"📦 {where} — yangi paket" + (f" ({n} ta sana)" if n else "")
    if kind == "sold_out":
        return f"⛔ {where} — sotilib bo'ldi"
    if kind == "back_on_sale":
        return f"✅ {where} — yana sotuvda"
    if kind == "removed":
        return f"➖ {where} — ro'yxatdan olib tashlandi"
    if kind == "source_down":
        return f"⚠️ {COMPANY.get(ev.get('company_id'), '—')} manbasi ishlamayapti ({new.get('failures', '?')} marta ketma-ket xato)"
    if kind == "source_recovered":
        return f"✅ {COMPANY.get(ev.get('company_id'), '—')} manbasi qayta ishlayapti"
    if kind == "manual_entry":
        text = f"✍️ {where} — qo'lda kiritildi"
        if new.get("price_usd"):
            text += f": {new.get('pax')} kishi {money(new['price_usd'])}"
            if old.get("price_usd"):
                text += f" (avto: {money(old['price_usd'])})"
        if new.get("sold_out"):
            text += ", sotilib bo'ldi"
        if new.get("note"):
            text += f" — {str(new['note'])[:100]}"
        return text
    if kind == "manual_conflict":
        text = f"⚠️ {where} — qo'lda kiritilgan narx saytdagidan farq qiladi"
        if old.get("price_usd") and new.get("price_usd"):
            text += f" ({new.get('pax')} kishi): qo'lda {money(old['price_usd'])}, saytda {money(new['price_usd'])}"
        return text
    return f"ℹ️ {where} — {kind}"


def build_messages(events: list[dict], ctx: dict) -> list[tuple[str, list[int]]]:
    """-> [(text, [event ids])]: one message per company, split only if it would not fit in Telegram."""
    by_company: dict = {}
    for ev in sorted(events, key=lambda e: e["id"]):
        by_company.setdefault(ev.get("company_id"), []).append(ev)
    out: list[tuple[str, list[int]]] = []
    for company in sorted(by_company, key=lambda c: COMPANY_ORDER.index(c) if c in COMPANY_ORDER else 99):
        lines: list[str] = []
        ids: list[int] = []
        size = 0
        for ev in by_company[company]:
            try:
                line = format_event(ev, ctx)
            except Exception:  # noqa: BLE001 - one malformed event must never block the queue
                line = f"ℹ️ {COMPANY.get(company, '—')} — {ev.get('type')}"
            if lines and size + len(line) + 1 > MAX_MESSAGE:
                out.append(("\n".join(lines), ids))
                lines, ids, size = [], [], 0
            lines.append(line)
            ids.append(ev["id"])
            size += len(line) + 1
        if lines:
            out.append(("\n".join(lines), ids))
    return out


# --------------------------------------------------------------------- sending
class Telegram:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None,
                 sleep=time.sleep, max_attempts: int = 4):
        self._token, self.chat_id = token, chat_id
        self.session, self.sleep, self.max_attempts = session or requests.Session(), sleep, max_attempts

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        for _ in range(self.max_attempts):
            try:
                r = self.session.post(url, json={"chat_id": self.chat_id, "text": text,
                                                 "disable_web_page_preview": True}, timeout=20)
            except requests.RequestException as e:
                # str(e) would contain the URL, i.e. the token: only the type is reported
                raise TelegramError(f"network error ({type(e).__name__})") from None
            if r.status_code == 200:
                return
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code == 429:
                self.sleep(min(int((body.get("parameters") or {}).get("retry_after", 1)), 60) + 1)
                continue
            raise TelegramError(f"telegram HTTP {r.status_code}: {str(body.get('description', ''))[:100]}")
        raise TelegramError("telegram rate limit: giving up for this pass")


def notify_pass(store, sender, dry_run: bool = False, sleep=time.sleep, pause: float = 1.1) -> dict:
    """One pass over the unsent events. Returns {'messages': n, 'events': n, 'failed': bool}."""
    events = store.unsent_events(PER_PASS)
    result = {"messages": 0, "events": 0, "failed": False}
    if not events:
        return result
    messages = build_messages(events, store.event_context(events))
    for i, (text, ids) in enumerate(messages):
        if dry_run:
            print(text, "\n---")
        else:
            if i:
                sleep(pause)            # Telegram allows about one message per second per chat
            try:
                sender.send(text)
            except TelegramError as e:
                log.error("telegram: %s (events stay unsent, will retry)", e)
                result["failed"] = True
                return result
            store.mark_events_sent(ids)
        result["messages"] += 1
        result["events"] += len(ids)
    return result


def _load_sender(store):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
        return None
    setting = store.get_setting("telegram") or {}
    if setting.get("enabled") is False:
        log.info("telegram is muted in app_settings")
        return None
    chat_id = setting.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        log.error("no chat id: set app_settings 'telegram' or TELEGRAM_CHAT_ID")
        return None
    return Telegram(token, str(chat_id))


def run_from_env(store, dry_run: bool = False) -> dict | None:
    sender = None if dry_run else _load_sender(store)
    if not dry_run and sender is None:
        return None
    return notify_pass(store, sender, dry_run=dry_run)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the messages, send nothing, mark nothing")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--every", type=int, default=60, help="seconds between passes with --loop")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see .env.example)", file=sys.stderr)
        return 2
    store = RestStore(base, key)
    while True:
        res = run_from_env(store, args.dry_run)
        if res is None and not args.dry_run:
            return 2
        if res and res["messages"]:
            log.info("sent %s messages for %s events", res["messages"], res["events"])
        if not args.loop:
            return 1 if res and res["failed"] else 0
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
