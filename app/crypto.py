import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.config import get_settings


def _get_aesgcm(master_key_hex: str | None = None) -> AESGCM:
    """Initialize AESGCM instance with 32-byte master key."""
    key_hex = master_key_hex or get_settings().MASTER_ENCRYPTION_KEY
    key_bytes = bytes.fromhex(key_hex.strip())
    if len(key_bytes) != 32:
        raise ValueError("Master key must decode to exactly 32 bytes for AES-256-GCM")
    return AESGCM(key_bytes)


def encrypt_secret(plaintext: str, master_key_hex: str | None = None) -> str:
    """Encrypt plaintext string using AES-256-GCM.

    A 12-byte cryptographically secure random nonce is generated and
    prepended to the ciphertext before Base64 encoding.
    """
    if plaintext is None:
        raise ValueError("Plaintext cannot be None")

    aesgcm = _get_aesgcm(master_key_hex)
    nonce = os.urandom(12)
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)
    payload = nonce + ciphertext
    return base64.b64encode(payload).decode("utf-8")


def decrypt_secret(token: str, master_key_hex: str | None = None) -> str:
    """Decrypt a Base64-encoded AES-256-GCM token back to plaintext string.

    Expects the first 12 bytes of the decoded payload to be the nonce.
    """
    if not token:
        raise ValueError("Token cannot be empty")

    try:
        payload = base64.b64decode(token.encode("utf-8"))
    except Exception as err:
        raise ValueError("Invalid Base64 token") from err

    if len(payload) < 28:
        # 12 bytes nonce + at least 16 bytes auth tag
        raise ValueError("Payload too short to contain valid nonce and authentication tag")

    nonce = payload[:12]
    ciphertext = payload[12:]
    aesgcm = _get_aesgcm(master_key_hex)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as err:
        raise ValueError("Decryption failed: corrupted ciphertext, bad tag, or invalid master key") from err

    return decrypted_bytes.decode("utf-8")
