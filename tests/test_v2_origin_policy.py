"""Safari sends "Origin: null" on same-site POSTs under a strict referrer policy; writes must still work there."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request


def request(headers: list[tuple[str, str]], path: str = "/api/script/assist") -> Request:
    return Request({"type": "http", "method": "POST", "path": path, "query_string": b"",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
                    "client": ("172.17.0.1", 40000), "server": ("goldmic.example.test", 443), "scheme": "https"})


class OriginPolicyTests(unittest.TestCase):
    def setUp(self):
        from backend import main
        self.main = main
        settings = SimpleNamespace(cors_origins=["https://goldmic.example.test"])
        self.enterContext(patch.object(main, "settings", settings))
        self.enterContext(patch.object(main, "trusted_local_request", lambda *a, **k: False))

    def allowed(self, *headers: tuple[str, str]) -> bool:
        return self.main._has_allowed_origin(request([("host", "goldmic.example.test"), *headers]))

    def test_configured_origin_is_allowed(self):
        self.assertTrue(self.allowed(("origin", "https://goldmic.example.test")))

    def test_null_origin_is_allowed_only_with_browser_same_origin_proof(self):
        self.assertTrue(self.allowed(("origin", "null"), ("sec-fetch-site", "same-origin")))
        for extra in ([], [("sec-fetch-site", "cross-site")], [("sec-fetch-site", "same-site")], [("sec-fetch-site", "none")],
                      [("sec-fetch-site", "same-origin"), ("sec-fetch-site", "same-origin")]):
            self.assertFalse(self.allowed(("origin", "null"), *extra), extra)

    def test_foreign_or_missing_origin_is_rejected_even_with_same_origin_claim(self):
        self.assertFalse(self.allowed(("origin", "https://evil.example.test"), ("sec-fetch-site", "same-origin")))
        self.assertFalse(self.allowed(("sec-fetch-site", "same-origin")))
        self.assertFalse(self.allowed(("origin", "null"), ("origin", "null"), ("sec-fetch-site", "same-origin")))


if __name__ == "__main__":
    unittest.main()
