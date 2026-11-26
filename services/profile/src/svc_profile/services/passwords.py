"""Password hashing with argon2id (docs/harness/13). Pure functions, no I/O."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's recommended parameters
# Verified against when the email is unknown, so unknown and wrong-password cost the same time.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_time() -> None:
    verify_password(_DUMMY_HASH, "x")
