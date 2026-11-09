"""Binary rollout input shared by scans and private archive collection."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

try:
    from compression import zstd
except ImportError:  # Python 3.11–3.13, or 3.14 built without zstd
    try:
        from backports import zstd
    except ImportError:
        zstd = None


class ZstdUnavailableError(RuntimeError):
    """Raised when compressed input has no available decompression backend."""


DECOMPRESSION_ERRORS = (ZstdUnavailableError, EOFError)
if zstd is not None:
    DECOMPRESSION_ERRORS += (zstd.ZstdError,)


def is_session_file(path: Path) -> bool:
    return path.name.endswith((".jsonl", ".jsonl.zst"))


def prefer_plain_session(path: Path) -> Path:
    """Treat plain and compressed twins as a single logical rollout."""

    if path.name.endswith(".jsonl.zst"):
        plain = path.with_suffix("")
        if plain.is_file():
            return plain
    return path


def open_session(path: Path) -> BinaryIO:
    """Stream bytes, with frame validation and no transcript text decoding."""

    if path.name.endswith(".jsonl.zst"):
        if zstd is None:
            raise ZstdUnavailableError("Zstandard support is unavailable")
        return zstd.open(path, "rb")
    return path.open("rb")
