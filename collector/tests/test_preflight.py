import base64
import json
import unittest

from collector import preflight


def jwt(role):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{b({'alg': 'HS256'})}.{b({'role': role})}.sig"


WEB = {"SITE_DOMAIN": "prices.example.com", "SUPABASE_URL": "https://abc123.supabase.co",
       "SUPABASE_ANON_KEY": "sb_publishable_abc"}
COL = {"SUPABASE_URL": "https://abc123.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_xyz",
       "MANDARIN_URL": "https://m.test/x", "SAFO_SITE_URL": "https://s.test", "ETIHAD_API_URL": "https://e.test", "ETIHAD_API_KEY": "k"}


class Preflight(unittest.TestCase):
    def test_good_setup(self):
        self.assertEqual(preflight.problems(WEB, COL), [])

    def test_a_secret_key_in_the_browser_config_is_refused_in_every_form(self):
        for key in ["sb_secret_abc", jwt("service_role"), jwt("authenticated"), "garbage", ""]:
            found = preflight.problems(dict(WEB, SUPABASE_ANON_KEY=key), COL)
            self.assertTrue(found, key)
            self.assertFalse(any(key and key in p for p in found), "problem text must not echo the key")
        self.assertEqual(preflight.problems(dict(WEB, SUPABASE_ANON_KEY=jwt("anon")), COL), [])   # legacy anon is fine

    def test_domain_url_and_missing_collector_settings(self):
        for d in ["https://prices.example.com", "prices.example.com/", "localhost", ""]:
            self.assertTrue(preflight.problems(dict(WEB, SITE_DOMAIN=d), COL), d)
        self.assertTrue(preflight.problems(dict(WEB, SUPABASE_URL="http://abc.supabase.co"), COL))
        self.assertTrue(preflight.problems(dict(WEB, SUPABASE_URL="https://evil.example.com"), COL))
        self.assertTrue(preflight.problems(WEB, dict(COL, ETIHAD_API_KEY="")))
        self.assertTrue(preflight.problems(WEB, dict(COL, SUPABASE_SERVICE_ROLE_KEY="")))
        self.assertTrue(preflight.problems(WEB, dict(COL, SUPABASE_URL="https://other.supabase.co")))
        self.assertTrue(preflight.problems(WEB, dict(COL, SUPABASE_SERVICE_ROLE_KEY=WEB["SUPABASE_ANON_KEY"])))

    def test_env_parser(self):
        self.assertEqual(preflight.parse_env('# c\nA=1\nB = "two"\n\nC=\nbad line\nD=x=y\n'),
                         {"A": "1", "B": "two", "C": "", "D": "x=y"})


if __name__ == "__main__":
    unittest.main()
