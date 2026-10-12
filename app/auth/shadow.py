"""Read an account's password hash from /etc/shadow.

Used by the BLE gateway's hash login: a client that holds the config bundle
applied to the device can send pi's hash from its `userconf.txt` instead of the
password, as long as the password has not been changed since. Reading shadow
needs membership in the `shadow` group, which only the gateway's own user
(`tsconfig-ble`) has - see docs/security.md.
"""

import hmac
from typing import Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

# A module constant so tests can point it at a temporary file.
SHADOW_PATH = "/etc/shadow"


def read_hash(user: str) -> Optional[str]:
    """Return `user`'s password hash, or None if the account has none that
    could match (missing user, unreadable file, locked or empty entry).

    The file is read on every call, so a changed password applies at once.
    """
    try:
        with open(SHADOW_PATH, encoding="utf-8") as f:
            for line in f:
                fields = line.rstrip("\n").split(":")
                if fields[0] == user and len(fields) > 1:
                    password_hash = fields[1]
                    # Locked (`!...`, `*`) or empty: no hash can log in
                    if not password_hash or password_hash[0] in "!*":
                        return None
                    return password_hash
    except OSError as e:
        logger.error(f"Cannot read {SHADOW_PATH}: {e}")
    return None


def hash_matches(user: str, token: str) -> bool:
    """Check `token` against `user`'s hash in constant time."""
    password_hash = read_hash(user)
    if password_hash is None or not isinstance(token, str):
        return False
    return hmac.compare_digest(password_hash.encode("utf-8"), token.encode("utf-8"))
