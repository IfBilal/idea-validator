"""sha256 of an exported model, with a ``<file>.sha256`` sidecar (docs/harness/11 §3)."""

import hashlib
from pathlib import Path


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_checksum(path: Path | str) -> Path:
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{sha256_file(path)}  {path.name}\n", encoding="utf-8")
    return sidecar
