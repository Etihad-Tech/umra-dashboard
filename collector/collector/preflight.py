"""Checks BEFORE deploy, so a wrong key never reaches the public site.

    python -m collector.preflight ../deploy/.env .env      (run from the collector folder; deploy.sh does this)

Prints problems (never values) and exits 1 if there are any.
"""
from __future__ import annotations

import base64
import json
import re
import sys


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _jwt_role(token: str):
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except (ValueError, TypeError):
        return "unreadable"


def public_key_problem(key: str):
    """The browser key must be public: sb_publishable_... or a legacy JWT whose role is 'anon'."""
    if not key:
        return "SUPABASE_ANON_KEY is empty"
    if key.startswith("sb_secret_"):
        return "SUPABASE_ANON_KEY is a SECRET key - it would be published to every visitor"
    role = _jwt_role(key)
    if role == "service_role":
        return "SUPABASE_ANON_KEY is the service_role key - it would be published to every visitor"
    if role is not None and role != "anon":
        return "SUPABASE_ANON_KEY is a JWT with role %r, expected 'anon'" % role
    if role is None and not key.startswith("sb_publishable_"):
        return "SUPABASE_ANON_KEY does not look like a publishable or anon key"
    return None


def problems(web: dict, collector: dict) -> list:
    out = []
    domain = web.get("SITE_DOMAIN", "")
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", domain):
        out.append("SITE_DOMAIN must be a bare domain such as prices.example.com (no https://, no slash)")
    port = web.get("WEB_PORT", "8744")
    if not (port.isdigit() and 1024 <= int(port) <= 65535):
        out.append("WEB_PORT must be a number from 1024 to 65535 (the loopback port nginx proxies to)")
    url = web.get("SUPABASE_URL", "")
    if not re.fullmatch(r"https://[a-z0-9]+\.supabase\.co", url):
        out.append("SUPABASE_URL must look like https://<project>.supabase.co")
    bad = public_key_problem(web.get("SUPABASE_ANON_KEY", ""))
    if bad:
        out.append(bad)
    secret = collector.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not secret:
        out.append("collector/.env: SUPABASE_SERVICE_ROLE_KEY is missing")
    elif secret == web.get("SUPABASE_ANON_KEY"):
        out.append("collector/.env: the service key equals the public key")
    if collector.get("SUPABASE_URL") and url and collector["SUPABASE_URL"].rstrip("/") != url.rstrip("/"):
        out.append("SUPABASE_URL differs between deploy/.env and collector/.env")
    for name in ("MANDARIN_URL", "SAFO_SITE_URL", "ETIHAD_API_URL", "ETIHAD_API_KEY"):
        if not collector.get(name):
            out.append("collector/.env: %s is missing" % name)
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m collector.preflight <deploy/.env> <collector/.env>", file=sys.stderr)
        return 2
    try:
        web, col = (parse_env(open(p, encoding="utf-8").read()) for p in argv)
    except OSError as e:
        print("cannot read %s" % e.filename, file=sys.stderr)
        return 2
    found = problems(web, col)
    for p in found:
        print("PROBLEM:", p, file=sys.stderr)
    print("preflight: %s" % ("FAILED" if found else "ok"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
