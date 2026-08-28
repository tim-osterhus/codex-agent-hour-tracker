"""Stream Codex JSONL sessions into aggregate turn-duration metadata."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "CompletedTurn",
    "ScanDiagnostics",
    "ScanResult",
    "scan_sessions",
]

_MISSING = object()
_SOURCE_FINGERPRINT_LENGTH = 16
_MAX_EVENT_GAP_SECONDS = 1_800.0
_MAX_TURN_ID_BYTES = 256
_MAX_NUMERIC_BYTES = 64
_MAX_SOURCE_BYTES = 256
_MAX_SOURCE_HASH_BYTES = 4_096
_MAX_SOURCE_KEY_BYTES = 256
_MAX_TYPE_BYTES = 64
_MAX_JSON_DEPTH = 128
_TASK_FIELD_LIMITS = {
    b"type": _MAX_TYPE_BYTES,
    b"turn_id": _MAX_TURN_ID_BYTES,
    b"started_at": _MAX_NUMERIC_BYTES,
    b"completed_at": _MAX_NUMERIC_BYTES,
    b"duration_ms": _MAX_NUMERIC_BYTES,
}
_TIMESTAMP_PREFIX = re.compile(
    rb'^\s*\{\s*"timestamp"\s*:\s*"([^"\\]*)"(?=\s*[,}])'
)
_TASK_EVENT_TYPES_BYTES = frozenset((b"task_started", b"task_complete"))


@dataclass(frozen=True, slots=True)
class CompletedTurn:
    """Metadata for one completed, non-batch turn."""

    started_at: float
    duration_seconds: float


@dataclass(slots=True)
class ScanDiagnostics:
    """Counts and file-level issues found while scanning sessions."""

    files_scanned: int = 0
    malformed_lines: int = 0
    incomplete_turns: int = 0
    unmatched_completions: int = 0
    duration_fallbacks: int = 0
    event_timing_fallbacks: int = 0
    duplicate_turns: int = 0
    excluded_batch_turns: int = 0
    unknown_sources: Counter[str] = field(default_factory=Counter)
    malformed_files: set[Path] = field(default_factory=set)


@dataclass(slots=True)
class ScanResult:
    """Completed turns and diagnostics from a session-directory scan."""

    turns: list[CompletedTurn] = field(default_factory=list)
    diagnostics: ScanDiagnostics = field(default_factory=ScanDiagnostics)


def scan_sessions(sessions_dir: Path) -> ScanResult:
    """Scan JSONL session files, retaining only turn timing metadata.

    Candidate lines are inspected with raw structural offsets. Only bounded
    direct metadata scalars are decoded. Transcript, reasoning, tool
    arguments, and tool output are never copied into the result.
    """

    diagnostics = ScanDiagnostics()
    best_turns: dict[object, _ParsedTurn] = {}

    for session_file in sorted(sessions_dir.rglob("*.jsonl")):
        diagnostics.files_scanned += 1
        source_kind, unknown_labels, file_turns = _scan_file(
            session_file, diagnostics
        )
        if source_kind == "batch":
            diagnostics.excluded_batch_turns += len(file_turns)
        else:
            for parsed_turn in file_turns:
                previous = best_turns.get(parsed_turn.turn_id)
                if previous is None:
                    best_turns[parsed_turn.turn_id] = parsed_turn
                    continue
                diagnostics.duplicate_turns += 1
                if _is_better_observation(parsed_turn, previous):
                    best_turns[parsed_turn.turn_id] = parsed_turn
        for source_label in sorted(unknown_labels):
            diagnostics.unknown_sources[source_label] += 1

    ordered_turns = sorted(best_turns.values(), key=_turn_sort_key)
    return ScanResult(
        turns=[parsed_turn.turn for parsed_turn in ordered_turns],
        diagnostics=diagnostics,
    )


@dataclass(frozen=True, slots=True)
class _ParsedTurn:
    turn_id: object
    turn: CompletedTurn
    event_timing_usable: bool


@dataclass(frozen=True, slots=True)
class _RawEnvelope:
    """Structural locations for the small part of a candidate record we use."""

    object_start: int
    top_level_type: bytes | None
    payload_start: int | None
    task_type: bytes | None
    complete: bool

    @property
    def is_candidate(self) -> bool:
        return self.top_level_type in {
            b"session_meta",
            b"task_started",
            b"task_complete",
        } or self.task_type in _TASK_EVENT_TYPES_BYTES


@dataclass(slots=True)
class _OpenTurn:
    started_at: float
    last_event_timestamp: float | None = None
    active_duration_seconds: float = 0.0


def _scan_file(
    session_file: Path,
    diagnostics: ScanDiagnostics,
) -> tuple[str, set[str], list[_ParsedTurn]]:
    starts: dict[object, _OpenTurn] = {}
    turns: list[_ParsedTurn] = []
    source_kind = "unseen"
    unknown_labels: set[str] = set()

    try:
        with session_file.open("rb") as handle:
            for raw_line in handle:
                event_timestamp = _top_level_timestamp(raw_line)
                if event_timestamp is not None:
                    _advance_open_turns(starts, event_timestamp)
                envelope = _describe_envelope(raw_line)
                if not envelope.is_candidate:
                    continue
                line_malformed = not envelope.complete
                if not line_malformed:
                    try:
                        if envelope.top_level_type == b"session_meta":
                            found, source = _extract_session_source(
                                raw_line, envelope
                            )
                            if found:
                                incoming_kind, source_label = (
                                    _classify_source_span(raw_line, source)
                                )
                            else:
                                incoming_kind, source_label = (
                                    "unknown",
                                    "unknown:missing",
                                )
                            source_kind = _merge_source_kind(
                                source_kind, incoming_kind
                            )
                            if source_label is not None:
                                unknown_labels.add(source_label)
                        elif envelope.task_type in _TASK_EVENT_TYPES_BYTES:
                            event, metadata_valid = _extract_task_metadata(
                                raw_line, envelope
                            )
                            if not metadata_valid:
                                line_malformed = True
                            elif envelope.task_type == b"task_started":
                                if not _consume_start(
                                    event, starts, event_timestamp
                                ):
                                    line_malformed = True
                            else:
                                valid, parsed_turn = _consume_completion(
                                    event,
                                    starts,
                                    diagnostics,
                                )
                                if not valid:
                                    line_malformed = True
                                elif parsed_turn is not None:
                                    turns.append(parsed_turn)
                    except (RecursionError, TypeError, ValueError, OverflowError):
                        line_malformed = True
                if line_malformed:
                    diagnostics.malformed_lines += 1
                    diagnostics.malformed_files.add(session_file)
    except OSError:
        diagnostics.malformed_files.add(session_file)

    diagnostics.incomplete_turns += len(starts)
    if source_kind == "unseen":
        source_kind = "unknown"
        unknown_labels.add("unknown:missing")
    return source_kind, unknown_labels, turns


def _describe_envelope(raw_line: bytes) -> _RawEnvelope:
    object_start = _skip_whitespace(raw_line, 0)
    top_level_type = _top_level_string_field(raw_line, b"type")
    payload_start = None
    task_type = None
    if top_level_type in {b"event_msg", b"session_meta"}:
        payload_start = _top_level_object_field_start(raw_line, b"payload")
        if top_level_type == b"event_msg" and payload_start is not None:
            task_type = _top_level_string_field(
                raw_line, b"type", object_start=payload_start
            )
    candidate = top_level_type in {
        b"session_meta",
        b"task_started",
        b"task_complete",
    } or task_type in _TASK_EVENT_TYPES_BYTES
    value_end = _skip_json_value(raw_line, object_start) if candidate else None
    complete = (
        value_end is not None
        and _skip_whitespace(raw_line, value_end) == len(raw_line)
    )
    return _RawEnvelope(
        object_start=object_start,
        top_level_type=top_level_type,
        payload_start=payload_start,
        task_type=(
            task_type
            if top_level_type == b"event_msg"
            else top_level_type
            if top_level_type in _TASK_EVENT_TYPES_BYTES
            else None
        ),
        complete=complete,
    )


def _is_candidate(raw_line: bytes) -> bool:
    """Return whether raw structural markers identify a supported envelope."""

    return _describe_envelope(raw_line).is_candidate


def _direct_field(
    value: Mapping[str, object], field_name: str
) -> tuple[bool, object]:
    if field_name in value:
        return True, value[field_name]
    return False, _MISSING


def _extract_task_metadata(
    raw_line: bytes, envelope: _RawEnvelope
) -> tuple[dict[str, object], bool]:
    """Decode only bounded direct scalar task fields into a tiny mapping."""

    object_start = (
        envelope.payload_start
        if envelope.top_level_type == b"event_msg"
        else envelope.object_start
    )
    if object_start is None or envelope.task_type is None:
        return {}, False
    spans, valid = _collect_task_field_spans(raw_line, object_start)
    type_span = spans.get(b"type")
    if type_span is None or not _matches_task_type(
        raw_line, type_span, envelope.task_type
    ):
        valid = False
    event: dict[str, object] = {
        "type": envelope.task_type.decode("ascii")
    }
    for field_name, max_bytes in _TASK_FIELD_LIMITS.items():
        if field_name == b"type":
            continue
        span = spans.get(field_name)
        if span is None:
            continue
        field_valid, value = _decode_bounded_scalar_span(
            raw_line, span, max_bytes
        )
        if not field_valid:
            valid = False
        else:
            event[field_name.decode("ascii")] = value
    return event, valid


def _collect_task_field_spans(
    raw_line: bytes, object_start: int
) -> tuple[dict[bytes, tuple[int, int]], bool]:
    """Collect direct task-field spans while skipping each other value once."""

    if object_start >= len(raw_line) or raw_line[object_start] != ord("{"):
        return {}, False
    spans: dict[bytes, tuple[int, int]] = {}
    cursor = _skip_whitespace(raw_line, object_start + 1)
    if cursor < len(raw_line) and raw_line[cursor] == ord("}"):
        return spans, True
    while True:
        key_end = _skip_json_string(raw_line, cursor)
        if key_end is None:
            return {}, False
        key_start = cursor + 1
        key_length = key_end - 1 - key_start
        cursor = _skip_whitespace(raw_line, key_end)
        if cursor >= len(raw_line) or raw_line[cursor] != ord(":"):
            return {}, False
        value_start = _skip_whitespace(raw_line, cursor + 1)
        value_end = _skip_json_value(raw_line, value_start)
        if value_end is None:
            return {}, False
        for field_name in _TASK_FIELD_LIMITS:
            if (
                key_length == len(field_name)
                and raw_line[key_start : key_end - 1] == field_name
            ):
                spans.setdefault(field_name, (value_start, value_end))
                break
        cursor = _skip_whitespace(raw_line, value_end)
        if cursor >= len(raw_line):
            return {}, False
        if raw_line[cursor] == ord("}"):
            return spans, True
        if raw_line[cursor] != ord(","):
            return {}, False
        cursor = _skip_whitespace(raw_line, cursor + 1)


def _matches_task_type(
    raw_line: bytes, span: tuple[int, int], expected: bytes
) -> bool:
    value_start, value_end = span
    return (
        value_start < value_end
        and raw_line[value_start] == ord('"')
        and value_end - value_start - 2 <= _MAX_TYPE_BYTES
        and raw_line[value_start + 1 : value_end - 1] == expected
    )


def _decode_bounded_scalar_span(
    raw_line: bytes,
    span: tuple[int, int],
    max_bytes: int,
) -> tuple[bool, object]:
    value_start, value_end = span
    if value_end - value_start > max_bytes:
        return False, _MISSING
    if raw_line[value_start] in b"[{":
        return False, _MISSING
    try:
        value = json.loads(raw_line[value_start:value_end])
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        return False, _MISSING
    if isinstance(value, (Mapping, list)):
        return False, _MISSING
    return True, value


def _extract_session_source(
    raw_line: bytes, envelope: _RawEnvelope
) -> tuple[bool, tuple[int, int] | None]:
    payload_start = envelope.payload_start
    object_start = payload_start or envelope.object_start
    if payload_start is None:
        # Keep accepting the pre-payload fixture shape without recursive lookup.
        object_start = envelope.object_start
    value_start = _top_level_object_field_start(
        raw_line, b"source", object_start=object_start
    )
    if value_start is None:
        return False, None
    value_end = _skip_json_value(raw_line, value_start)
    if value_end is None:
        return False, None
    return True, (value_start, value_end)


def _top_level_string_field(
    raw_line: bytes,
    field_name: bytes,
    *,
    object_start: int | None = None,
) -> bytes | None:
    value_start = _top_level_object_field_start(
        raw_line, field_name, object_start=object_start
    )
    if value_start is None:
        return None
    value_end = _skip_json_string(raw_line, value_start)
    if value_end is None or value_end - value_start - 2 > _MAX_TYPE_BYTES:
        return None
    value_bytes = raw_line[value_start + 1 : value_end - 1]
    if b"\\" in value_bytes:
        return None
    return value_bytes


def _top_level_object_field_start(
    raw_line: bytes,
    field_name: bytes,
    *,
    object_start: int | None = None,
) -> int | None:
    if object_start is None:
        object_start = _skip_whitespace(raw_line, 0)
    if object_start >= len(raw_line) or raw_line[object_start] != ord("{"):
        return None

    cursor = object_start + 1
    while True:
        cursor = _skip_whitespace(raw_line, cursor)
        if cursor >= len(raw_line) or raw_line[cursor] == ord("}"):
            return None
        key_end = _skip_json_string(raw_line, cursor)
        if key_end is None:
            return None
        key_content_start = cursor + 1
        key_content_end = key_end - 1
        cursor = _skip_whitespace(raw_line, key_end)
        if cursor >= len(raw_line) or raw_line[cursor] != ord(":"):
            return None
        cursor = _skip_whitespace(raw_line, cursor + 1)
        value_start = cursor
        if (
            key_content_end - key_content_start == len(field_name)
            and raw_line[key_content_start:key_content_end] == field_name
        ):
            return value_start
        value_end = _skip_json_value(raw_line, value_start)
        if value_end is None:
            return None
        cursor = _skip_whitespace(raw_line, value_end)
        if cursor >= len(raw_line) or raw_line[cursor] == ord("}"):
            return None
        if raw_line[cursor] != ord(","):
            return None
        cursor += 1


def _skip_whitespace(raw_line: bytes, cursor: int) -> int:
    while cursor < len(raw_line) and raw_line[cursor] in b" \t\r\n":
        cursor += 1
    return cursor


def _skip_json_string(raw_line: bytes, cursor: int) -> int | None:
    """Find a valid JSON string's end without copying its contents."""

    if cursor >= len(raw_line) or raw_line[cursor] != ord('"'):
        return None
    cursor += 1
    while cursor < len(raw_line):
        byte = raw_line[cursor]
        if byte == ord('"'):
            return cursor + 1
        if byte == ord("\\"):
            cursor += 1
            if cursor >= len(raw_line):
                return None
            escaped = raw_line[cursor]
            if escaped == ord("u"):
                if cursor + 4 >= len(raw_line):
                    return None
                if any(
                    digit not in b"0123456789abcdefABCDEF"
                    for digit in raw_line[cursor + 1 : cursor + 5]
                ):
                    return None
                cursor += 5
            elif escaped in b'"\\/bfnrt':
                cursor += 1
            else:
                return None
        elif byte < 0x20:
            return None
        elif byte >= 0x80:
            cursor = _skip_utf8_codepoint(raw_line, cursor)
            if cursor is None:
                return None
        else:
            cursor += 1
    return None


def _skip_utf8_codepoint(raw_line: bytes, cursor: int) -> int | None:
    first = raw_line[cursor]
    if 0xC2 <= first <= 0xDF:
        continuation_count = 1
    elif 0xE0 <= first <= 0xEF:
        continuation_count = 2
    elif 0xF0 <= first <= 0xF4:
        continuation_count = 3
    else:
        return None
    end = cursor + continuation_count + 1
    if end > len(raw_line):
        return None
    continuation = raw_line[cursor + 1 : end]
    if any(byte not in range(0x80, 0xC0) for byte in continuation):
        return None
    if first == 0xE0 and continuation[0] < 0xA0:
        return None
    if first == 0xED and continuation[0] >= 0xA0:
        return None
    if first == 0xF0 and continuation[0] < 0x90:
        return None
    if first == 0xF4 and continuation[0] >= 0x90:
        return None
    return end


def _skip_json_value(
    raw_line: bytes, cursor: int, depth: int = 0
) -> int | None:
    """Validate and skip one JSON value without materializing its contents."""

    if depth > _MAX_JSON_DEPTH:
        return None
    cursor = _skip_whitespace(raw_line, cursor)
    if cursor >= len(raw_line):
        return None
    if raw_line[cursor] == ord('"'):
        return _skip_json_string(raw_line, cursor)
    if raw_line[cursor] == ord("{"):
        return _skip_json_object(raw_line, cursor, depth)
    if raw_line[cursor] == ord("["):
        return _skip_json_array(raw_line, cursor, depth)
    for literal in (b"true", b"false", b"null"):
        if raw_line.startswith(literal, cursor):
            return cursor + len(literal)
    return _skip_json_number(raw_line, cursor)


def _skip_json_object(raw_line: bytes, cursor: int, depth: int) -> int | None:
    cursor = _skip_whitespace(raw_line, cursor + 1)
    if cursor < len(raw_line) and raw_line[cursor] == ord("}"):
        return cursor + 1
    while True:
        key_end = _skip_json_string(raw_line, cursor)
        if key_end is None:
            return None
        cursor = _skip_whitespace(raw_line, key_end)
        if cursor >= len(raw_line) or raw_line[cursor] != ord(":"):
            return None
        value_end = _skip_json_value(raw_line, cursor + 1, depth + 1)
        if value_end is None:
            return None
        cursor = _skip_whitespace(raw_line, value_end)
        if cursor >= len(raw_line):
            return None
        if raw_line[cursor] == ord("}"):
            return cursor + 1
        if raw_line[cursor] != ord(","):
            return None
        cursor = _skip_whitespace(raw_line, cursor + 1)


def _skip_json_array(raw_line: bytes, cursor: int, depth: int) -> int | None:
    cursor = _skip_whitespace(raw_line, cursor + 1)
    if cursor < len(raw_line) and raw_line[cursor] == ord("]"):
        return cursor + 1
    while True:
        value_end = _skip_json_value(raw_line, cursor, depth + 1)
        if value_end is None:
            return None
        cursor = _skip_whitespace(raw_line, value_end)
        if cursor >= len(raw_line):
            return None
        if raw_line[cursor] == ord("]"):
            return cursor + 1
        if raw_line[cursor] != ord(","):
            return None
        cursor = _skip_whitespace(raw_line, cursor + 1)


def _skip_json_number(raw_line: bytes, cursor: int) -> int | None:
    start = cursor
    if cursor < len(raw_line) and raw_line[cursor] == ord("-"):
        cursor += 1
    if cursor >= len(raw_line):
        return None
    if raw_line[cursor] == ord("0"):
        cursor += 1
        if cursor < len(raw_line) and raw_line[cursor] in b"0123456789":
            return None
    elif raw_line[cursor] in b"123456789":
        cursor += 1
        while cursor < len(raw_line) and raw_line[cursor] in b"0123456789":
            cursor += 1
    else:
        return None
    if cursor < len(raw_line) and raw_line[cursor] == ord("."):
        cursor += 1
        fraction_start = cursor
        while cursor < len(raw_line) and raw_line[cursor] in b"0123456789":
            cursor += 1
        if cursor == fraction_start:
            return None
    if cursor < len(raw_line) and raw_line[cursor] in b"eE":
        cursor += 1
        if cursor < len(raw_line) and raw_line[cursor] in b"+-":
            cursor += 1
        exponent_start = cursor
        while cursor < len(raw_line) and raw_line[cursor] in b"0123456789":
            cursor += 1
        if cursor == exponent_start:
            return None
    return cursor if cursor > start else None


def _consume_start(
    event: Mapping[str, object],
    starts: dict[object, _OpenTurn],
    event_timestamp: float | None,
) -> bool:
    found_id, turn_id = _direct_field(event, "turn_id")
    found_started, started_at = _direct_field(event, "started_at")
    if not found_id or not _hashable_turn_id(turn_id):
        return False
    valid_started, started_value = _timestamp(started_at if found_started else _MISSING)
    if not valid_started or started_value is None:
        return False
    starts[turn_id] = _OpenTurn(
        started_at=started_value,
        last_event_timestamp=event_timestamp,
    )
    return True


def _consume_completion(
    event: Mapping[str, object],
    starts: dict[object, _OpenTurn],
    diagnostics: ScanDiagnostics,
) -> tuple[bool, _ParsedTurn | None]:
    found_id, turn_id = _direct_field(event, "turn_id")
    if not found_id or not _hashable_turn_id(turn_id):
        return False, None
    has_pending_start = turn_id in starts
    open_turn = starts.get(turn_id)
    start_value = open_turn.started_at if open_turn is not None else None
    found_started, started_at = _direct_field(event, "started_at")
    if found_started:
        valid_started, completion_started = _timestamp(started_at)
        if not valid_started or completion_started is None:
            if start_value is None:
                diagnostics.unmatched_completions += 1
            return False, None
    else:
        completion_started = None
    if start_value is None and completion_started is None:
        diagnostics.unmatched_completions += 1
    valid, turn = _valid_completion_metadata(
        event,
        turn_id,
        open_turn,
        completion_started if completion_started is not None else start_value,
        diagnostics,
    )
    if valid and has_pending_start:
        starts.pop(turn_id, None)
    return valid, turn


def _valid_completion_metadata(
    event: Mapping[str, object],
    turn_id: object,
    open_turn: _OpenTurn | None,
    start_value: float | None,
    diagnostics: ScanDiagnostics,
) -> tuple[bool, _ParsedTurn | None]:
    found_started, started_at = _direct_field(event, "started_at")
    if found_started:
        valid_started, resolved_started = _timestamp(started_at)
        if not valid_started or resolved_started is None:
            return False, None
    else:
        resolved_started = start_value

    found_duration, duration_ms = _direct_field(event, "duration_ms")
    found_completed, completed_at = _direct_field(event, "completed_at")
    if found_completed:
        valid_completed, completed_value = _timestamp(completed_at)
        if not valid_completed or completed_value is None:
            return False, None
    else:
        completed_value = None

    if found_duration:
        valid_duration, duration_value = _duration_seconds(duration_ms)
        if not valid_duration or duration_value is None:
            return False, None
    else:
        duration_value = None

    if resolved_started is None:
        return True, None

    if open_turn is not None and open_turn.active_duration_seconds > 0.0:
        return True, _ParsedTurn(
            turn_id=turn_id,
            turn=CompletedTurn(
                resolved_started, open_turn.active_duration_seconds
            ),
            event_timing_usable=True,
        )

    if duration_value is not None:
        diagnostics.event_timing_fallbacks += 1
        return True, _ParsedTurn(
            turn_id=turn_id,
            turn=CompletedTurn(
                resolved_started,
                min(duration_value, _MAX_EVENT_GAP_SECONDS),
            ),
            event_timing_usable=False,
        )

    if completed_value is None:
        return False, None
    duration_value = completed_value - resolved_started
    if not _nonnegative_finite(duration_value):
        return False, None
    diagnostics.duration_fallbacks += 1
    diagnostics.event_timing_fallbacks += 1
    return True, _ParsedTurn(
        turn_id=turn_id,
        turn=CompletedTurn(
            resolved_started,
            min(duration_value, _MAX_EVENT_GAP_SECONDS),
        ),
        event_timing_usable=False,
    )


def _advance_open_turns(
    starts: Mapping[object, _OpenTurn], event_timestamp: float
) -> None:
    for open_turn in starts.values():
        previous_timestamp = open_turn.last_event_timestamp
        if previous_timestamp is not None:
            elapsed = max(0.0, event_timestamp - previous_timestamp)
            open_turn.active_duration_seconds += min(
                elapsed, _MAX_EVENT_GAP_SECONDS
            )
        open_turn.last_event_timestamp = event_timestamp


def _top_level_timestamp(raw_line: bytes) -> float | None:
    match = _TIMESTAMP_PREFIX.match(raw_line)
    if match is None:
        return None
    try:
        timestamp_text = match.group(1).decode("ascii")
        parsed = datetime.fromisoformat(
            timestamp_text[:-1] + "+00:00"
            if timestamp_text.endswith("Z")
            else timestamp_text
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        converted = parsed.timestamp()
    except (UnicodeDecodeError, ValueError, OverflowError, OSError):
        return None
    return converted if math.isfinite(converted) else None


def _is_better_observation(
    candidate: _ParsedTurn, previous: _ParsedTurn
) -> bool:
    candidate_rank = (
        candidate.event_timing_usable,
        candidate.turn.duration_seconds,
        -candidate.turn.started_at,
    )
    previous_rank = (
        previous.event_timing_usable,
        previous.turn.duration_seconds,
        -previous.turn.started_at,
    )
    return candidate_rank > previous_rank


def _turn_sort_key(parsed_turn: _ParsedTurn) -> tuple[float, str, str, float]:
    return (
        parsed_turn.turn.started_at,
        type(parsed_turn.turn_id).__name__,
        repr(parsed_turn.turn_id),
        parsed_turn.turn.duration_seconds,
    )


def _timestamp(value: object) -> tuple[bool, float | None]:
    if not _numeric(value):
        return False, None
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        return False, None
    if not _nonnegative_finite(converted):
        return False, None
    return True, converted


def _duration_seconds(value: object) -> tuple[bool, float | None]:
    if not _numeric(value):
        return False, None
    try:
        converted = float(value) / 1000.0
    except (TypeError, ValueError, OverflowError):
        return False, None
    if not _nonnegative_finite(converted):
        return False, None
    return True, converted


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nonnegative_finite(value: float) -> bool:
    return math.isfinite(value) and value >= 0


def _hashable_turn_id(value: object) -> bool:
    if value is _MISSING or value is None or isinstance(value, bool):
        return False
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _merge_source_kind(current_kind: str, incoming_kind: str) -> str:
    if current_kind == "batch" or incoming_kind == "batch":
        return "batch"
    if current_kind == "interactive" or incoming_kind == "interactive":
        return "interactive"
    if current_kind == "unknown" or incoming_kind == "unknown":
        return "unknown"
    return "unseen"


def _classify_source_span(
    raw_line: bytes, source_span: tuple[int, int]
) -> tuple[str, str | None]:
    source_start, source_end = source_span
    source_length = source_end - source_start
    if source_start >= source_end:
        return "unknown", _unknown_source_label(
            raw_line, source_start, source_end, "empty"
        )
    first_byte = raw_line[source_start]
    if first_byte == ord('"'):
        if source_length > _MAX_SOURCE_BYTES:
            return "unknown", _unknown_source_label(
                raw_line, source_start, source_end, "string"
            )
        try:
            source = json.loads(raw_line[source_start:source_end])
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
        ):
            return "unknown", _unknown_source_label(
                raw_line, source_start, source_end, "string"
            )
        if isinstance(source, str):
            normalized = source.lower()
            if normalized in {"exec", "batch", "codex_exec"}:
                return "batch", None
            if normalized in {"vscode", "cli", "user"}:
                return "interactive", None
        return "unknown", _unknown_source_label(
            raw_line, source_start, source_end, "string"
        )
    if first_byte == ord("{"):
        keys = _direct_object_keys(raw_line, source_start, source_end)
        if keys is not None:
            if b"exec" in keys or b"batch" in keys:
                return "batch", None
            if b"subagent" in keys:
                return "interactive", None
        return "unknown", _unknown_source_label(
            raw_line, source_start, source_end, "mapping"
        )
    if first_byte == ord("["):
        source_type = "array"
    elif source_length == 4 and raw_line[source_start:source_end] == b"null":
        source_type = "null"
    elif source_length in {4, 5} and raw_line[source_start:source_end] in {
        b"true",
        b"false",
    }:
        source_type = "boolean"
    else:
        source_type = "number"
    return "unknown", _unknown_source_label(
        raw_line, source_start, source_end, source_type
    )


def _direct_object_keys(
    raw_line: bytes, object_start: int, object_end: int
) -> set[bytes] | None:
    if object_start >= object_end or raw_line[object_start] != ord("{"):
        return None
    keys: set[bytes] = set()
    cursor = object_start + 1
    while True:
        cursor = _skip_whitespace(raw_line, cursor)
        if cursor >= object_end:
            return None
        if raw_line[cursor] == ord("}"):
            return keys if cursor + 1 == object_end else None
        key_end = _skip_json_string(raw_line, cursor)
        if key_end is None:
            return None
        key_content_start = cursor + 1
        key_content_end = key_end - 1
        key_length = key_content_end - key_content_start
        if (
            key_length > _MAX_SOURCE_KEY_BYTES
            or raw_line.find(b"\\", key_content_start, key_content_end) != -1
        ):
            return None
        key_bytes = raw_line[key_content_start:key_content_end]
        cursor = _skip_whitespace(raw_line, key_end)
        if cursor >= object_end or raw_line[cursor] != ord(":"):
            return None
        cursor = _skip_whitespace(raw_line, cursor + 1)
        value_end = _skip_json_value(raw_line, cursor)
        if value_end is None or value_end > object_end:
            return None
        cursor = _skip_whitespace(raw_line, value_end)
        if cursor >= object_end:
            return None
        keys.add(key_bytes.lower())
        if raw_line[cursor] == ord("}"):
            return keys if cursor + 1 == object_end else None
        if raw_line[cursor] != ord(","):
            return None
        cursor += 1


def _unknown_source_label(
    raw_line: bytes, source_start: int, source_end: int, source_type: str
) -> str:
    bounded_end = min(source_end, source_start + _MAX_SOURCE_HASH_BYTES)
    digest = hashlib.sha256(raw_line[source_start:bounded_end]).hexdigest()
    return f"unknown:{source_type}:{digest[:_SOURCE_FINGERPRINT_LENGTH]}"
