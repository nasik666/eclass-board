"""GitHub secret encryption of tools/setup_github.py (skipped where PyNaCl is not installed, e.g. in CI)."""
import base64
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

HAS_NACL = importlib.util.find_spec("nacl") is not None


@unittest.skipUnless(HAS_NACL, "pip install pynacl to run")
class SecretEncryption(unittest.TestCase):
    def test_sealed_box_opens_with_the_repository_key(self):
        from nacl import public

        import setup_github

        private = public.PrivateKey.generate()
        public_b64 = base64.b64encode(bytes(private.public_key)).decode("ascii")
        sealed = setup_github.seal(public_b64, "Пароль & co")
        self.assertNotIn("Пароль", sealed)
        opened = public.SealedBox(private).decrypt(base64.b64decode(sealed)).decode("utf-8")
        self.assertEqual(opened, "Пароль & co")


if __name__ == "__main__":
    unittest.main()
