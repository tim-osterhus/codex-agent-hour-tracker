"""Read native Muse Code usage metadata without decoding session content."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

from .scanner import _skip_json_value, _skip_whitespace, _top_level_object_field_start, _top_level_string_field

_MAX_RECORD = 8 * 1024 * 1024
_MICROSECOND = 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_STAMP = 253402128000 * _MICROSECOND
_UINT = re.compile(rb"(?:0|[1-9][0-9]{0,18})(?=[,} \r\n\t])")
_LIFECYCLE = (b"session.started", b"session.opened.observed", b"session.end")
_TOOL_START = b"tool_batch.effect.started"
_TOOL_END = b"tool_batch.effect.terminal"
_MODEL = b"model_completed"
_TOOL_EFFECTS = (_TOOL_START, _TOOL_END)
_MARKERS = tuple(b'"' + value + b'"' for value in (_MODEL, *_TOOL_EFFECTS, *_LIFECYCLE))
_WAIT_TOOLS = {b"wait_agent", b"wait_agents", b"wait_for_agent", b"wait_for_agents",
               b"wait_task", b"wait_for_task"}

Interval = tuple[int, int]
Identity = tuple[bytes, bytes]


@dataclass
class MuseUsage:
    tokens: dict[date, int] = field(default_factory=dict)
    hours: dict[date, float] = field(default_factory=dict)
    missing_tokens: dict[date, int] = field(default_factory=dict)
    unreadable: int = 0
    unsupported: int = 0
    files_scanned: int = 0


@dataclass(frozen=True)
class _ModelCall:
    end: int
    tokens: int | None
    duration_ms: int | None


def scan_muse(roots: list[Path] | None, timezone: ZoneInfo) -> MuseUsage:
    """Sum recorded calls by completion date and split measured time at local midnight."""
    result = MuseUsage()
    models: dict[Identity, _ModelCall] = {}
    tools: dict[Identity, Interval] = {}
    seen: set[Path] = set()
    for root in _default_roots() if roots is None else roots:
        try:
            paths = [root] if root.is_file() else root.rglob("session.jsonl")
            for path in paths:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    with resolved.open("rb") as log:
                        _scan_file(log, models, tools, result)
                        result.files_scanned += 1
                except OSError:
                    result.unreadable += 1
        except (OSError, RuntimeError):
            result.unreadable += 1

    work_by_run: dict[bytes, list[Interval]] = defaultdict(list)
    for (run, _), call in models.items():
        day = _as_datetime(call.end, timezone).date()
        if call.tokens is None:
            result.missing_tokens[day] = result.missing_tokens.get(day, 0) + 1
        else:
            result.tokens[day] = result.tokens.get(day, 0) + call.tokens
        if call.duration_ms is not None:
            start = call.end - call.duration_ms * 1000
            if 0 < start < call.end:
                work_by_run[run].append((start, call.end))
            elif call.duration_ms == 0:
                result.hours.setdefault(day, 0.0)
            else:
                result.unreadable += 1
    for (run, _), interval in tools.items():
        work_by_run[run].append(interval)
    daily: dict[date, int] = defaultdict(int)
    for work in work_by_run.values():
        for start, end in _merge(work):
            while start < end:
                day = _as_datetime(start, timezone).date()
                midnight = datetime.combine(day + timedelta(days=1), time(), tzinfo=timezone)
                boundary = min(end, _epoch_us(midnight))
                daily[day] += boundary - start
                start = boundary
    result.hours.update({day: elapsed / (_MICROSECOND * 3600) for day, elapsed in daily.items()})
    return result


def _default_roots() -> list[Path]:
    default = Path.home() / ".local" / "share" / "muse" / "sessions"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    return [Path(xdg_data).expanduser() / "muse" / "sessions", default] if xdg_data else [default]


def _records(log: BinaryIO) -> Iterator[bytes | None]:
    while line := log.readline(_MAX_RECORD + 1):
        if len(line) <= _MAX_RECORD:
            yield line
            continue
        while not line.endswith(b"\n"):
            line = log.readline(_MAX_RECORD + 1)
            if not line:
                break
        yield None


def _scan_file(
    log: BinaryIO,
    models: dict[Identity, _ModelCall],
    tools: dict[Identity, Interval],
    result: MuseUsage,
) -> None:
    # Effect endpoints must come from a single journal and process incarnation.
    # Recovery can close an interrupted effect in a later process.
    # Muse emits a tool start after approval. Approval waits are separate facts.
    open_effects: dict[Identity, int] = {}
    for line in _records(log):
        if line is None:
            result.unreadable += 1
            continue
        if not any(marker in line for marker in _MARKERS):
            if line.strip() and not line.lstrip().startswith(b"{"):
                result.unreadable += 1
            continue
        kind = _string(line, b"payload_type")
        if kind in _LIFECYCLE:
            open_effects.clear()
            continue
        payload = _field(line, b"payload")
        if kind == b"runtime.session" and payload is not None and _string(line, b"kind", payload) == b"run":
            event = _field(line, b"event", payload)
            if event is None or _string(line, b"kind", event) != _MODEL:
                continue
        elif kind in _TOOL_EFFECTS and payload is not None:
            event = _field(line, b"record", payload)
        else:
            continue
        end = _skip_json_value(line, 0)
        stamp = _stamp(line)
        run = _string(line, b"run_id", payload)
        if end is None or _skip_whitespace(line, end) != len(line) or not (event and run and stamp):
            result.unreadable += 1
            continue
        if _number(line, b"schema_version") != 1 or _number(line, b"payload_schema_version") != 1:
            result.unsupported += 1
            continue
        if kind == b"runtime.session":
            source = _string(line, b"source_run_record_id", payload)
            if not source:
                result.unreadable += 1
                continue
            call = _model_call(line, event, stamp)
            key = run, source
            previous = models.get(key)
            if previous is None or call.end < previous.end:
                models[key] = call
            continue

        effect = _string(line, b"effect_id", event)
        if not effect:
            result.unreadable += 1
            continue
        key = run, effect
        if kind == _TOOL_START:
            tool = _string(line, b"tool_name", event)
            if tool is None:
                result.unreadable += 1
            elif tool not in _WAIT_TOOLS:
                open_effects.setdefault(key, stamp)
            continue
        start = open_effects.pop(key, None)
        if start is None:
            continue
        if stamp < start:
            result.unreadable += 1
            continue
        outcome = _field(line, b"outcome", event)
        if outcome is None or _string(line, b"kind", outcome) not in (b"completed", b"failed"):
            result.unsupported += 1
            continue
        previous_interval = tools.get(key)
        if previous_interval is None or stamp < previous_interval[1]:
            tools[key] = start, stamp


def _model_call(line: bytes, event: int, stamp: int) -> _ModelCall:
    usage = _field(line, b"usage", event)
    tokens = None
    if usage is not None:
        tokens = _number(line, b"total_tokens", usage)
        model = _string(line, b"model", event)
        if tokens is None and model and model.startswith(b"muse-"):
            # Meta counts cached input and reasoning output inside the respective totals.
            input_tokens = _number(line, b"input_tokens", usage)
            output_tokens = _number(line, b"output_tokens", usage)
            if input_tokens is not None and output_tokens is not None:
                tokens = input_tokens + output_tokens
    duration_ms = _number(line, b"duration_ms", event)
    return _ModelCall(stamp, tokens, duration_ms)


def _field(line: bytes, name: bytes, scope: int | None = None) -> int | None:
    return _top_level_object_field_start(line, name, object_start=scope)


def _string(line: bytes, name: bytes, scope: int | None = None) -> bytes | None:
    return _top_level_string_field(line, name, object_start=scope)


def _number(line: bytes, name: bytes, scope: int | None = None) -> int | None:
    start = _field(line, name, scope)
    if start is None:
        return None
    match = _UINT.match(line, start)
    return int(match[0]) if match else None


def _stamp(line: bytes) -> int | None:
    value = _number(line, b"recorded_at")
    if value is None:
        stamp = _string(line, b"recorded_at")
        if stamp is None:
            return None
        try:
            dt = datetime.fromisoformat(stamp.decode("ascii"))
            if dt.utcoffset() is None:
                return None
            value = _epoch_us(dt)
        except (UnicodeError, ValueError, OverflowError):
            return None
    return value if 0 < value < _MAX_STAMP else None


def _epoch_us(value: datetime) -> int:
    delta = value.astimezone(UTC) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * _MICROSECOND + delta.microseconds


def _as_datetime(stamp: int, timezone: ZoneInfo) -> datetime:
    return (_EPOCH + timedelta(microseconds=stamp)).astimezone(timezone)


def _merge(intervals: Iterable[Interval]) -> Iterator[Interval]:
    merged: Interval | None = None
    for start, end in sorted(intervals):
        if merged is None:
            merged = start, end
        elif start <= merged[1]:
            merged = merged[0], max(end, merged[1])
        else:
            yield merged
            merged = start, end
    if merged is not None:
        yield merged
