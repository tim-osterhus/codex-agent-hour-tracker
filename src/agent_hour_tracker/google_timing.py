"""Measured time from Antigravity conversation metadata, without decoding content."""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_NS = 1_000_000_000
_MAX_RECORD = 8 * 1024 * 1024


@dataclass
class Timing:
    intervals: dict[bytes, set[tuple[int, int]]] = field(default_factory=lambda: defaultdict(set))
    unreadable: int = 0

    def add(self, identity: bytes, start: int | None, end: int | None) -> None:
        if start is not None and end is not None and 0 < start < end:
            self.intervals[identity].add((start, end))

    def daily_hours(self, timezone: ZoneInfo) -> dict[date, float]:
        totals: dict[date, int] = defaultdict(int)
        for intervals in self.intervals.values():
            end = 0
            start = 0
            for a, b in sorted(intervals):
                if a > end:
                    if end:
                        _add_day(totals, start, end, timezone)
                    start, end = a, b
                else:
                    end = max(end, b)
            if end:
                _add_day(totals, start, end, timezone)
        return {day: duration / (_NS * 3600) for day, duration in totals.items()}


def _add_day(totals: dict[date, int], start: int, end: int, timezone: ZoneInfo) -> None:
    try:
        day = datetime.fromtimestamp(start // _NS, timezone).date()
    except (ValueError, OverflowError, OSError):
        return
    totals[day] += end - start


def _antigravity_roots() -> set[Path]:
    configured = os.environ.get("ANTIGRAVITY_DATA_DIR")
    if configured:
        roots = (Path(p.strip()).expanduser() for p in configured.split(",") if p.strip())
        return {root / "conversations" if (root / "conversations").is_dir() else root for root in roots}
    home = Path.home()
    names = ("antigravity", "antigravity-cli", "antigravity-ide", "antigravity-backup", "antigravity-acp")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return {home / ".gemini" / name / "conversations" for name in names} | {
        config / "antigravity" / "conversations"
    }


def antigravity_timing() -> Timing:
    result = Timing()
    seen: set[Path] = set()
    for directory in _antigravity_roots():
        try:
            for path in directory.rglob("*.db"):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    _antigravity_db(resolved, result)
                except (OSError, sqlite3.Error, UnicodeError, ValueError):
                    result.unreadable += 1
        except OSError:
            result.unreadable += 1
    return result


def _antigravity_db(path: Path, result: Timing) -> None:
    # Only query the trajectory ID and bounded CortexStepMetadata. The step
    # payload, error details, prompts, and results are in other columns.
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
    try:
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA query_only=ON")
        tables = {name for (name,) in con.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('steps','trajectory_meta')"
        )}
        if "steps" not in tables:
            result.unreadable += 1
            return
        identity = path.stem
        if "trajectory_meta" in tables:
            rows = con.execute("SELECT trajectory_id FROM trajectory_meta LIMIT 2").fetchall()
            if len(rows) == 1 and isinstance(rows[0][0], str) and rows[0][0]:
                identity = rows[0][0]
        key = identity.encode("utf-8")
        for (blob,) in con.execute(
            "SELECT CASE WHEN length(metadata) <= ? THEN metadata END "
            "FROM steps WHERE metadata IS NOT NULL", (_MAX_RECORD,)
        ):
            if not isinstance(blob, bytes):
                result.unreadable += 1
                continue
            try:
                metadata = _proto_fields(memoryview(blob), {1, 7, 8, 9, 32})
                # Generation starts at creation and ends when generation
                # finishes. For execution steps use their explicit start/end.
                if 9 in metadata:
                    result.add(key, _stamp(metadata.get(1)), _stamp(metadata.get(7)))
                result.add(key, _stamp(metadata.get(32)), _stamp(metadata.get(8)))
            except ValueError:
                result.unreadable += 1
    finally:
        con.close()


def _varint(data: memoryview, at: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if at >= len(data):
            break
        byte = data[at]
        at += 1
        if shift == 63 and byte > 1:
            break
        value |= (byte & 127) << shift
        if byte < 128:
            return value, at
    raise ValueError("invalid protobuf varint")


def _proto_fields(data: memoryview, wanted: set[int]) -> dict[int, int | memoryview]:
    fields: dict[int, int | memoryview] = {}
    at = 0
    while at < len(data):
        tag, at = _varint(data, at)
        field_number, wire = divmod(tag, 8)
        if not field_number:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value, at = _varint(data, at)
            if field_number in wanted:
                fields[field_number] = value
            continue
        if wire == 2:
            length, at = _varint(data, at)
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
        else:
            raise ValueError("unsupported protobuf field")
        end = at + length
        if end > len(data):
            raise ValueError("truncated protobuf field")
        if wire == 2 and field_number in wanted:
            fields[field_number] = data[at:end]
        at = end
    return fields


def _stamp(blob: int | memoryview | None) -> int | None:
    if not isinstance(blob, memoryview):
        return None
    fields = _proto_fields(blob, {1, 2})
    return _ns_timestamp(fields.get(1, 0), fields.get(2, 0))


def _ns_timestamp(seconds: object, nanos: object) -> int | None:
    if type(seconds) is int and type(nanos) is int and 0 <= seconds < 253402300800 and 0 <= nanos < _NS:
        return seconds * _NS + nanos
    return None
