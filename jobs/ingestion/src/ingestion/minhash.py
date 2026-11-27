"""MinHash signatures with a fixed seed: the same text gives the same signature on every machine.

No clock, no global randomness, no ``hash()`` (which Python salts per process). Shingles are hashed
with BLAKE2b, then ``num_perm`` universal hash functions ``(a*x + b) mod p`` with coefficients drawn
from ``random.Random(seed)`` give the signature.
"""

import hashlib
import random

_PRIME = (1 << 61) - 1


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Exact Jaccard similarity; two empty sets are 0.0 (nothing to compare is not a match)."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class MinHasher:
    def __init__(self, *, num_perm: int, seed: int) -> None:
        if num_perm < 1:
            raise ValueError(f"num_perm must be at least 1, got {num_perm}")
        rng = random.Random(seed)  # noqa: S311 (reproducible hashing, not security)
        self._coeffs = [
            (rng.randrange(1, _PRIME), rng.randrange(0, _PRIME)) for _ in range(num_perm)
        ]

    @staticmethod
    def _base(shingle: str) -> int:
        return int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")

    def signature(self, shingles: frozenset[str]) -> tuple[int, ...]:
        bases = [self._base(s) for s in sorted(shingles)]
        if not bases:
            return (_PRIME,) * len(self._coeffs)
        return tuple(min((a * x + b) % _PRIME for x in bases) for a, b in self._coeffs)
