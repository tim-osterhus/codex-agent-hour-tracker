"""Read native Claude Code turn timers without decoding conversation content.

The internal JSONL ``system/turn_duration`` record stores elapsed milliseconds
and its end timestamp. Completed foreground Agent results also carry exact
worker run telemetry. Other records are intentionally not timed.
Main-session records do not establish human origin and remain unknown sources.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

from .scanner import (
    CompletedTurn,
    ScanDiagnostics,
    _ParsedTurn,
    _decode_bounded_scalar_span,
    _duration_seconds,
    _skip_json_string,
    _skip_json_value,
    _skip_whitespace,
    _top_level_object_field_start,
    _top_level_string_field,
)
from .session_io import DECOMPRESSION_ERRORS, open_session

_FIELD_LIMITS = {
    b"type": 64,
    b"subtype": 64,
    b"durationMs": 64,
    b"timestamp": 64,
    b"uuid": 64,
    b"isSidechain": 8,
}

_AGENT_FIELD_LIMITS = {b"status": 64, b"agentId": 128, b"totalDurationMs": 64}
_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _metadata(
    raw: bytes, *, object_start: int = 0,
    limits: dict[bytes, int] = _FIELD_LIMITS,
    validate_document: bool = True,
) -> tuple[dict[str, object], bool]:
    """Collect approved direct scalar fields; skip every other value intact."""
    start = _skip_whitespace(raw, object_start)
    end = _skip_json_value(raw, start)
    if end is None or (validate_document and _skip_whitespace(raw, end) != len(raw)):
        return {}, False
    cursor = _skip_whitespace(raw, start + 1)
    result: dict[str, object] = {}
    result_object_seen = False
    while cursor < len(raw) and raw[cursor] != ord("}"):
        key_end = _skip_json_string(raw, cursor)
        if key_end is None:
            return {}, False
        # Keys are compared as raw bytes, never decoded from transcript data.
        key_start = cursor + 1
        key_length = key_end - 1 - key_start
        if key_length == len(b"toolUseResult") and raw[key_start:key_end - 1] == b"toolUseResult":
            if result_object_seen:
                return {}, False
            result_object_seen = True
        cursor = _skip_whitespace(raw, key_end)
        value_start = _skip_whitespace(raw, cursor + 1)
        value_end = _skip_json_value(raw, value_start)
        if value_end is None:
            return {}, False
        for key, limit in limits.items():
            if key_length != len(key) or raw[key_start:key_end - 1] != key:
                continue
            name = key.decode("ascii")
            if name in result:
                return {}, False
            valid, value = _decode_bounded_scalar_span(
                raw, (value_start, value_end), limit
            )
            if not valid:
                return {}, False
            result[name] = value
            break
        cursor = _skip_whitespace(raw, value_end)
        if cursor < len(raw) and raw[cursor] == ord(","):
            cursor = _skip_whitespace(raw, cursor + 1)
        else:
            break
    return result, True


def _completion(
    metadata: dict[str, object], *, agent_id: str | None = None,
) -> _ParsedTurn | None:
    duration_valid, duration = _duration_seconds(metadata.get("durationMs"))
    timestamp = metadata.get("timestamp")
    identifier = metadata.get("uuid")
    if not duration_valid or duration is None:
        return None
    if not isinstance(timestamp, str) or not isinstance(identifier, str):
        return None
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        # A local timezone guess would make imports disagree between machines.
        if parsed_timestamp.tzinfo is None:
            return None
        ended_at = parsed_timestamp.timestamp()
        canonical_uuid = str(UUID(identifier))
    except (ValueError, OverflowError, OSError):
        return None
    started_at = ended_at - duration
    if not math.isfinite(started_at) or started_at < 0:
        return None
    return _ParsedTurn(
        # Forks copy historical records into a different session. Their immutable
        # event UUID, scoped by provider during archive merging, stays identical.
        turn_id=(
            f"claude-agent:{canonical_uuid}:{agent_id}"
            if agent_id is not None else f"claude-turn:{canonical_uuid}"
        ),
        turn=CompletedTurn(started_at, duration, provider="claude"),
        event_timing_usable=True,
    )


def _scan_file(
    session_file: Path, diagnostics: ScanDiagnostics
) -> list[tuple[str, _ParsedTurn]]:
    """Read main timers and exact foreground Agent completion telemetry.

    Sidechain timers are excluded: foreground Agent summaries already measure
    those workers. Background launches contain no elapsed runtime and are not
    estimated from their transcript messages.
    """
    observations: list[tuple[str, _ParsedTurn]] = []
    native_records = 0
    child_transcript = (
        "subagents" in session_file.parts
        or session_file.name.startswith("agent-")
        and session_file.name.endswith((".jsonl", ".jsonl.zst"))
    )
    unverified_main = False
    try:
        with open_session(session_file) as handle:
            for raw in handle:
                record_type = _top_level_string_field(raw, b"type")
                if record_type == b"system":
                    if _top_level_string_field(raw, b"subtype") != b"turn_duration":
                        continue
                    native_records += 1
                    metadata, valid = _metadata(raw)
                    sidechain = metadata.get("isSidechain")
                    if valid and (sidechain is True or child_transcript):
                        diagnostics.unknown_sources["claude:ignored-sidechain-duration"] += 1
                        continue
                    parsed = _completion(metadata) if valid else None
                    if not valid or parsed is None or (
                        sidechain is not None and not isinstance(sidechain, bool)
                    ):
                        diagnostics.malformed_lines += 1
                        diagnostics.malformed_files.add(session_file)
                        continue
                    unverified_main = True
                    observations.append(("unknown", parsed))
                elif record_type == b"user":
                    result_start = _top_level_object_field_start(raw, b"toolUseResult")
                    if result_start is None or raw[result_start:result_start + 1] != b"{":
                        continue
                    if _top_level_string_field(
                        raw, b"status", object_start=result_start
                    ) not in {b"completed", b"async_launched"}:
                        continue
                    # Inspect only direct run telemetry. content, prompt, usage,
                    # tool outputs and all other values remain structurally skipped.
                    result, valid = _metadata(
                        raw, object_start=result_start, limits=_AGENT_FIELD_LIMITS,
                        validate_document=False,
                    )
                    agent_id = result.get("agentId")
                    if not valid:
                        if (_top_level_string_field(raw, b"status", object_start=result_start)
                                == b"completed"
                                and _top_level_object_field_start(
                                    raw, b"agentId", object_start=result_start
                                ) is not None):
                            diagnostics.malformed_lines += 1
                            diagnostics.malformed_files.add(session_file)
                        continue
                    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
                        continue
                    status = result.get("status")
                    if status == "async_launched":
                        diagnostics.unknown_sources["claude:async-agent-unmeasured"] += 1
                        continue
                    if status != "completed":
                        continue
                    metadata, outer_valid = _metadata(raw)
                    metadata["durationMs"] = result.get("totalDurationMs")
                    parsed = (
                        _completion(metadata, agent_id=agent_id)
                        if outer_valid else None
                    )
                    if parsed is None:
                        diagnostics.malformed_lines += 1
                        diagnostics.malformed_files.add(session_file)
                        continue
                    observations.append(("delegated", parsed))
    except DECOMPRESSION_ERRORS:
        diagnostics.decompression_errors += 1
        diagnostics.malformed_files.add(session_file)
    except OSError:
        diagnostics.open_errors += 1
        diagnostics.malformed_files.add(session_file)
    if native_records == 0:
        diagnostics.unknown_sources["claude:no-native-duration"] += 1
    if unverified_main:
        diagnostics.unknown_sources["claude:unverified-main-origin"] += 1
    return observations
