"""What leaves the runner for the public site: the encrypted envelope, or data without grades.

Run: python -m unittest discover -s tests
"""
import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))

from cryptography.exceptions import InvalidTag  # noqa: E402

import envelope  # noqa: E402
import fetch  # noqa: E402

DATA = {
    "courses": [{"id": 2604, "name": "Operating System"}],
    "tasks": [],
    "schedule": [],
    "stats": [{"courseId": 2604, "grades": [{"name": "Midterm Manual", "grade": 6.0, "max": 25.0}]}],
}
PASSPHRASE = "correct horse battery staple"


class Envelope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = envelope.seal(DATA, PASSPHRASE)  # PBKDF2 is slow on purpose: seal once

    def test_round_trip(self):
        self.assertEqual(envelope.unseal(self.sealed, PASSPHRASE), DATA)

    def test_format_read_by_the_page(self):
        sealed = self.sealed
        self.assertEqual((sealed["encrypted"], sealed["v"], sealed["kdf"], sealed["iterations"]),
                         (True, 1, "PBKDF2-SHA256", 600_000))
        self.assertEqual(len(base64.b64decode(sealed["salt"])), 16)
        self.assertEqual(len(base64.b64decode(sealed["iv"])), 12)  # the GCM nonce size WebCrypto expects
        self.assertNotIn("Midterm", json.dumps(sealed))

    def test_wrong_passphrase(self):
        with self.assertRaises(InvalidTag):
            envelope.unseal(self.sealed, PASSPHRASE + "!")

    def test_fresh_salt_and_iv_every_time(self):
        again = envelope.seal(DATA, PASSPHRASE)
        self.assertNotEqual(again["salt"], self.sealed["salt"])
        self.assertNotEqual(again["iv"], self.sealed["iv"])


class Publishing(unittest.TestCase):
    def test_without_passphrase_grades_stay_off_the_site(self):
        published = fetch.for_publishing(DATA, "")
        self.assertEqual((published["stats"], published["privateOmitted"]), ([], True))
        self.assertEqual(published["courses"], DATA["courses"])
        self.assertEqual(len(DATA["stats"]), 1)  # the input is not modified

    def test_short_passphrase_is_refused(self):
        with self.assertRaises(SystemExit):
            fetch.for_publishing(DATA, "short")

    def test_with_passphrase_nothing_is_in_the_clear(self):
        published = fetch.for_publishing(DATA, PASSPHRASE)
        self.assertEqual(set(published), {"encrypted", "v", "kdf", "iterations", "salt", "iv", "data"})


if __name__ == "__main__":
    unittest.main()
