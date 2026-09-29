import base64
import os
from cryptography.fernet import Fernet, InvalidToken
from config import ENCRYPTION_KEY, SECRET_KEY

# Determine fernet key from ENCRYPTION_KEY or derive from SECRET_KEY
_cipher_suite = None

def _get_cipher() -> Fernet:
    global _cipher_suite
    if _cipher_suite is None:
        key = ENCRYPTION_KEY.strip()
        if not key:
            # Fallback to key derived from SECRET_KEY (padded/hashed to 32 bytes URL-safe base64)
            raw = (SECRET_KEY.encode() + b"0" * 32)[:32]
            key = base64.urlsafe_b64encode(raw).decode()
        _cipher_suite = Fernet(key.encode() if isinstance(key, str) else key)
    return _cipher_suite

def encrypt_value(value: str | None) -> str | None:
    """Encrypt a plaintext string. Returns a Fernet ciphertext prefixed with 'enc:'."""
    if not value:
        return value
    if value.startswith("enc:"):
        return value
    cipher = _get_cipher()
    encrypted = cipher.encrypt(value.encode()).decode()
    return f"enc:{encrypted}"

def decrypt_value(value: str | None) -> str | None:
    """Decrypt a Fernet ciphertext starting with 'enc:'. If plaintext or invalid, returns as-is."""
    if not value:
        return value
    if not value.startswith("enc:"):
        return value
    cipher = _get_cipher()
    try:
        raw_cipher = value[4:]
        decrypted = cipher.decrypt(raw_cipher.encode()).decode()
        return decrypted
    except InvalidToken:
        return value
