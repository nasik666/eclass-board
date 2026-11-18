"""Passphrase encryption of the published data.json.

GitHub Pages serves every file to anyone who has the link, and the Pages
artifact of a public repository can be downloaded by any GitHub user, so
grades and attendance leave the runner only inside this envelope.

site/app.js decrypts it with WebCrypto using the parameters stored in the
envelope: PBKDF2-HMAC-SHA256 -> AES-256-GCM, with the 16-byte tag appended to
the ciphertext (the layout both `cryptography` and WebCrypto use).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

VERSION = 1
ITERATIONS = 600_000  # OWASP (2023) recommendation for PBKDF2-HMAC-SHA256


def _cipher(passphrase: str, salt: bytes, iterations: int):
    # Imported here: only publishing needs the dependency, plain local runs do not.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)
    return AESGCM(key)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def seal(data: dict, passphrase: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = _cipher(passphrase, salt, ITERATIONS).encrypt(iv, plaintext, None)
    return {
        "encrypted": True,
        "v": VERSION,
        "kdf": "PBKDF2-SHA256",
        "iterations": ITERATIONS,
        "salt": _b64(salt),
        "iv": _b64(iv),
        "data": _b64(ciphertext),
    }


def unseal(envelope: dict, passphrase: str) -> dict:
    """Inverse of seal(); a wrong passphrase raises cryptography.exceptions.InvalidTag."""
    salt, iv, ciphertext = (base64.b64decode(envelope[k]) for k in ("salt", "iv", "data"))
    return json.loads(_cipher(passphrase, salt, envelope["iterations"]).decrypt(iv, ciphertext, None))
