# core/security/crypto.py
import os
import secrets
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from passlib.context import CryptContext


class JarvisCrypto:
    """
    Envelope encryption.
    - Master key derived from passphrase via Argon2id
    - Each field encrypted with a unique DEK (Data Encryption Key)
    - DEKs wrapped (encrypted) with the master key
    """
    ARGON2_MEMORY      = 65536   # 64 MB
    ARGON2_ITERATIONS  = 4
    ARGON2_PARALLELISM = 2
    ARGON2_HASH_LEN    = 32

    def __init__(self, master_passphrase: bytes, salt: Optional[bytes] = None):
        self.salt = salt or os.urandom(16)
        self.master_key = self._derive(master_passphrase, self.salt)

    def _derive(self, passphrase: bytes, salt: bytes) -> bytes:
        kdf = Argon2id(
            memory_cost=self.ARGON2_MEMORY,
            iterations=self.ARGON2_ITERATIONS,
            lanes=self.ARGON2_PARALLELISM,
            length=self.ARGON2_HASH_LEN,
            salt=salt,
        )
        return kdf.derive(passphrase)

    def verify_passphrase(self, passphrase: bytes, salt: bytes, stored_key: bytes) -> bool:
        """Return True if passphrase re-derives to stored_key."""
        try:
            candidate = self._derive(passphrase, salt)
            return secrets.compare_digest(candidate, stored_key)
        except Exception:
            return False

    # ---------- DEK helpers ----------

    def generate_dek(self) -> bytes:
        return secrets.token_bytes(32)

    def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes, bytes]:
        """Encrypt DEK with master key. Returns (ciphertext, iv, tag)."""
        iv = secrets.token_bytes(12)
        aesgcm = AESGCM(self.master_key)
        blob = aesgcm.encrypt(iv, dek, None)   # ciphertext + 16-byte tag appended
        return blob[:-16], iv, blob[-16:]

    def unwrap_dek(self, wrapped: bytes, iv: bytes, tag: bytes) -> bytes:
        aesgcm = AESGCM(self.master_key)
        return aesgcm.decrypt(iv, wrapped + tag, None)

    # ---------- Field helpers ----------

    def encrypt_field(self, plaintext: str, dek: bytes) -> tuple[bytes, bytes, bytes]:
        """Returns (ciphertext, iv, tag)."""
        iv = secrets.token_bytes(12)
        aesgcm = AESGCM(dek)
        blob = aesgcm.encrypt(iv, plaintext.encode(), None)
        return blob[:-16], iv, blob[-16:]

    def decrypt_field(self, ciphertext: bytes, iv: bytes, tag: bytes, dek: bytes) -> str:
        aesgcm = AESGCM(dek)
        return aesgcm.decrypt(iv, ciphertext + tag, None).decode()


# Password hashing for login
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
