import unittest

from backend.anonymous_access import (
    csrf_token_matches,
    issue_anonymous_session,
    verify_anonymous_session,
)


class AnonymousSessionTokenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "unit-test-session-secret-with-at-least-32-bytes"

    def test_round_trip_and_expiration(self) -> None:
        token, issued = issue_anonymous_session(self.secret, 3600, now=1_000)

        verified = verify_anonymous_session(token, self.secret, 3600, now=1_001)

        self.assertEqual(verified, issued)
        assert verified is not None
        self.assertTrue(csrf_token_matches(verified, issued.csrf_token))
        self.assertFalse(csrf_token_matches(verified, "wrong-token-value-12345"))
        self.assertIsNone(verify_anonymous_session(token, self.secret, 3600, now=4_600))

    def test_tampering_and_wrong_secret_are_rejected(self) -> None:
        token, _ = issue_anonymous_session(self.secret, 3600, now=1_000)
        payload, signature = token.split(".", maxsplit=1)
        tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]

        self.assertIsNone(
            verify_anonymous_session(
                f"{tampered_payload}.{signature}",
                self.secret,
                3600,
                now=1_001,
            )
        )
        self.assertIsNone(
            verify_anonymous_session(
                token,
                "different-session-secret-with-at-least-32-bytes",
                3600,
                now=1_001,
            )
        )

    def test_short_signing_secret_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "32"):
            issue_anonymous_session("too-short", 3600, now=1_000)


if __name__ == "__main__":
    unittest.main()