"""Bounded line reading: untrusted files are read in binary, split on ``\\n`` only, with caps.

``str.splitlines()`` also splits on U+2028, U+2029, U+0085, \\v, \\f and \\x1c-\\x1e, which JSON
allows inside strings, so it would cut a valid row in two. A line ends at ``\\n`` only.
A line over ``MAX_LINE_BYTES`` is yielded as ``None`` (skipped, the next line is still read) and the
whole input may not exceed ``MAX_TOTAL_BYTES`` after decompression (a gzip bomb stops the run).
"""

import gzip
from collections.abc import Iterator
from pathlib import Path

MAX_LINE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 256 * 1024 * 1024


class InputTooLarge(Exception):
    """The decompressed input is larger than ``MAX_TOTAL_BYTES``."""


def iter_lines(path: Path) -> Iterator[bytes | None]:
    """Lines (without the newline) of a plain or gzip file; ``None`` for an oversized line."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        total = 0
        while True:
            chunk = handle.readline(MAX_LINE_BYTES + 1)
            if not chunk:
                return
            total += len(chunk)
            oversized = len(chunk) > MAX_LINE_BYTES and not chunk.endswith(b"\n")
            while oversized:  # drain the rest of the long line without keeping it
                rest = handle.readline(MAX_LINE_BYTES)
                total += len(rest)
                if total > MAX_TOTAL_BYTES:
                    raise InputTooLarge(f"input too large (over {MAX_TOTAL_BYTES} bytes)")
                if not rest or rest.endswith(b"\n"):
                    break
            if total > MAX_TOTAL_BYTES:
                raise InputTooLarge(f"input too large (over {MAX_TOTAL_BYTES} bytes)")
            yield None if oversized else chunk.rstrip(b"\n").rstrip(b"\r")
