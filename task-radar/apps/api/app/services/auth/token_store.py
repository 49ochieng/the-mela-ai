"""Token storage with at-rest encryption.

Production should swap the local Fernet implementation for Azure Key Vault
references. The interface intentionally exposes only opaque references that
become the values stored in the database — the raw secrets never leave this
module.
"""
from __future__ import annotations

import abc
import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from ...config import get_settings


@dataclass
class StoredToken:
    access_token: str
    refresh_token: Optional[str]
    expires_at: datetime
    scopes: list[str]


class TokenStore(abc.ABC):
    @abc.abstractmethod
    def put(self, key: str, token: StoredToken) -> str: ...

    @abc.abstractmethod
    def get(self, reference: str) -> StoredToken: ...

    @abc.abstractmethod
    def delete(self, reference: str) -> None: ...


class FernetTokenStore(TokenStore):
    """Stores the encrypted blob inline as the 'reference'.

    The reference IS the ciphertext, prefixed with `f1:` so we can rotate
    schemes later. This means the DB stores nothing plaintext.

    Key rotation: ``TOKEN_ENCRYPTION_KEY`` is the *primary* (writer) key.
    ``TOKEN_ENCRYPTION_KEYS_SECONDARY`` (comma-separated) lists older keys
    that are accepted for *decryption only*. To rotate:
      1. Generate a new key. Put it in ``TOKEN_ENCRYPTION_KEY``.
      2. Move the prior primary into ``TOKEN_ENCRYPTION_KEYS_SECONDARY``.
      3. Deploy. New writes use the new key; existing references decrypt
         transparently via MultiFernet's ordered key list.
      4. Once a re-encrypt-in-place job has rewritten all references with
         the new key, drop the old key from secondaries.
    """

    def __init__(self, key: bytes | None = None) -> None:
        s = get_settings()
        keys: list[bytes] = []
        if key is not None:
            keys.append(key if isinstance(key, bytes) else key.encode())
        else:
            primary = s.token_encryption_key
            if not primary:
                if s.app_env != "development":
                    raise RuntimeError("TOKEN_ENCRYPTION_KEY required outside development")
                primary = Fernet.generate_key().decode()
                os.environ["TOKEN_ENCRYPTION_KEY"] = primary
            keys.append(primary.encode() if isinstance(primary, str) else primary)
            for sec in (s.token_encryption_keys_secondary or "").split(","):
                sec = sec.strip()
                if sec:
                    keys.append(sec.encode())
        # MultiFernet always *encrypts* with keys[0] (the primary) and
        # decrypts by trying every key in order. This is exactly what we
        # need for a rolling rotation.
        self._fernet = MultiFernet([Fernet(k) for k in keys])

    def put(self, key: str, token: StoredToken) -> str:
        payload = json.dumps(
            {
                "k": key,
                "a": token.access_token,
                "r": token.refresh_token,
                "e": token.expires_at.replace(tzinfo=timezone.utc).isoformat(),
                "s": token.scopes,
            }
        ).encode("utf-8")
        return "f1:" + self._fernet.encrypt(payload).decode("ascii")

    def get(self, reference: str) -> StoredToken:
        if not reference.startswith("f1:"):
            raise ValueError("Unknown token reference scheme")
        try:
            raw = self._fernet.decrypt(reference[3:].encode("ascii"))
        except InvalidToken as e:
            # Don't include the ciphertext in the message — that would
            # leak it into stack traces / logs.
            raise ValueError("Token decryption failed (key rotated out?)") from e
        d = json.loads(raw)
        return StoredToken(
            access_token=d["a"],
            refresh_token=d.get("r"),
            expires_at=datetime.fromisoformat(d["e"]),
            scopes=d.get("s", []),
        )

    def rotate(self, reference: str) -> str:
        """Re-encrypt a reference with the current primary key.

        Use this from a background worker to migrate stored references off
        a retired secondary key. ``MultiFernet.rotate`` is a no-op when
        the reference is already encrypted with the primary.
        """
        if not reference.startswith("f1:"):
            raise ValueError("Unknown token reference scheme")
        return "f1:" + self._fernet.rotate(reference[3:].encode("ascii")).decode("ascii")

    def delete(self, reference: str) -> None:  # noqa: D401
        # No persistence layer in this implementation; nothing to do.
        return None


_store: TokenStore | None = None


def get_token_store() -> TokenStore:
    global _store
    if _store is None:
        _store = FernetTokenStore()
    return _store
