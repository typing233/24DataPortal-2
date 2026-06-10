"""Ed25519 signature verification for plugins."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from dataclasses import dataclass

from nacl.signing import VerifyKey, SigningKey
from nacl.exceptions import BadSignatureError


@dataclass
class SignatureResult:
    valid: bool
    reason: str = ""


def generate_keypair() -> tuple[str, str]:
    """Generate a new Ed25519 keypair. Returns (private_key_b64, public_key_b64)."""
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return (
        base64.b64encode(bytes(signing_key)).decode(),
        base64.b64encode(bytes(verify_key)).decode(),
    )


def sign_package(package_path: str, private_key_b64: str) -> str:
    """Sign a package file and return the base64-encoded signature."""
    signing_key = SigningKey(base64.b64decode(private_key_b64))
    file_hash = _hash_file(package_path)
    signed = signing_key.sign(file_hash)
    return base64.b64encode(signed.signature).decode()


def verify_signature(
    package_path: str, signature_b64: str, public_key_b64: str
) -> SignatureResult:
    """Verify a package signature against a public key."""
    try:
        verify_key = VerifyKey(base64.b64decode(public_key_b64))
        file_hash = _hash_file(package_path)
        signature = base64.b64decode(signature_b64)
        verify_key.verify(file_hash, signature)
        return SignatureResult(valid=True)
    except BadSignatureError:
        return SignatureResult(valid=False, reason="Signature does not match")
    except Exception as e:
        return SignatureResult(valid=False, reason=f"Verification error: {e}")


def verify_against_trusted_keys(
    package_path: str, signature_b64: str, trusted_keys: list[str]
) -> SignatureResult:
    """Try verifying against any of the trusted public keys."""
    if not trusted_keys:
        return SignatureResult(valid=False, reason="No trusted keys configured")

    for key in trusted_keys:
        result = verify_signature(package_path, signature_b64, key)
        if result.valid:
            return result

    return SignatureResult(valid=False, reason="No trusted key matched the signature")


def _hash_file(path: str) -> bytes:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.digest()
