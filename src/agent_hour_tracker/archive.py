"""Private, timing-only archives for merging local session scans."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from . import METHODOLOGY_VERSION
from .scanner import (
    CompletedTurn,
    ScanDiagnostics,
    ScanResult,
    _is_better_observation,
    _ParsedTurn,
    _scan_file,
    _turn_id_digest,
    _turn_sort_key,
)

__all__ = [
    "ArchiveError",
    "collect_archive",
    "load_archive",
    "merge_archives",
    "write_archive",
]

_SCHEMA = "agent-hours-archive"
_SCHEMA_VERSION = 1
_ALLOWED_SOURCE_KINDS = frozenset(
    ("root", "delegated", "unknown", "conflicting", "batch")
)
_ARCHIVE_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "methodology_version",
        "label",
        "observations",
        "diagnostics",
    )
)
_OBSERVATION_KEYS = frozenset(
    (
        "turn_id_sha256",
        "started_at",
        "duration_seconds",
        "event_timing_usable",
        "source_kind",
    )
)
_DIAGNOSTIC_COUNTERS = (
    "files_scanned",
    "malformed_lines",
    "incomplete_turns",
    "unmatched_completions",
    "duration_fallbacks",
    "event_timing_fallbacks",
    "duplicate_turns",
    "excluded_batch_turns",
    "malformed_files",
    "open_errors",
    "traversal_errors",
    "missing_paths",
    "unsupported_paths",
)
_DIAGNOSTIC_KEYS = frozenset((*_DIAGNOSTIC_COUNTERS, "unknown_sources"))
_UNKNOWN_SOURCE_LABEL = re.compile(
    r"unknown:(?:missing|malformed|empty|string|mapping|array|null|"
    r"boolean|number):[0-9a-f]{16}$"
)
_UNKNOWN_FINGERPRINT = re.compile(r"unknown:(?:missing|malformed)$")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


class ArchiveError(ValueError):
    """Raised when a private archive cannot be read or fails validation."""


def collect_archive(paths: list[Path], *, label: str = "") -> dict:
    """Collect timing metadata from JSONL roots or files into an archive.

    The session parser only materializes bounded timing/source scalars. Turn
    IDs are replaced with typed SHA256 digests before the archive object is
    returned, and observations are deduplicated independently for each source
    kind using the scanner's existing ranking.
    """

    _validate_label(label, allow_empty=True)
    scan_diagnostics = ScanDiagnostics()
    path_diagnostics = {
        "traversal_errors": 0,
        "missing_paths": 0,
        "unsupported_paths": 0,
    }
    best_by_source: dict[tuple[str, str], _ParsedTurn] = {}

    for session_file in _iter_session_files(paths, path_diagnostics):
        scan_diagnostics.files_scanned += 1
        source_kind, unknown_labels, file_turns = _scan_file(
            session_file, scan_diagnostics
        )
        for source_label in sorted(unknown_labels):
            scan_diagnostics.unknown_sources[source_label] += 1
        for parsed_turn in file_turns:
            turn_digest = _turn_id_digest(parsed_turn.turn_id)
            key = (source_kind, turn_digest)
            previous = best_by_source.get(key)
            if previous is not None:
                scan_diagnostics.duplicate_turns += 1
                if not _is_better_observation(parsed_turn, previous):
                    continue
            best_by_source[key] = parsed_turn

    observations = [
        _observation_from_parsed(source_kind, turn_digest, parsed_turn)
        for (source_kind, turn_digest), parsed_turn in sorted(
            best_by_source.items(), key=_archive_observation_sort_key
        )
    ]
    diagnostics = _diagnostics_to_dict(
        scan_diagnostics,
        path_diagnostics,
    )
    archive: dict[str, object] = {
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "observations": observations,
        "diagnostics": diagnostics,
    }
    if label:
        archive["label"] = label
    return _validate_archive(archive)


def load_archive(path: Path) -> dict:
    """Load and validate one private archive without exposing its contents."""

    try:
        with Path(path).open("rb") as handle:
            value = json.load(handle, parse_constant=_reject_json_constant)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        raise ArchiveError("unable to read archive") from None
    return _validate_archive(value)


def merge_archives(
    archives: list[dict], *, include_exec: bool = False
) -> ScanResult:
    """Merge validated archive observations with scanner-compatible ranking.

    Batch observations are filtered before all-turn deduplication unless
    ``include_exec`` is true. Root observations are deduplicated separately,
    so a delegated or batch duplicate cannot hide a root sample.
    """

    if not isinstance(include_exec, bool):
        raise ArchiveError("invalid archive: include_exec must be boolean")

    best_turns: dict[str, _ParsedTurn] = {}
    best_root_turns: dict[str, _ParsedTurn] = {}
    diagnostics = ScanDiagnostics()

    for archive in archives:
        normalized = _validate_archive(archive)
        _merge_diagnostics(
            diagnostics,
            normalized["diagnostics"],
        )
        for observation in normalized["observations"]:
            source_kind = observation["source_kind"]
            if source_kind == "batch" and not include_exec:
                diagnostics.excluded_batch_turns += 1
                continue

            turn_digest = observation["turn_id_sha256"]
            parsed_turn = _parsed_from_observation(observation)
            previous = best_turns.get(turn_digest)
            if previous is not None:
                diagnostics.duplicate_turns += 1
                if _is_better_observation(parsed_turn, previous):
                    best_turns[turn_digest] = parsed_turn
            else:
                best_turns[turn_digest] = parsed_turn

            if source_kind != "root":
                continue
            previous_root = best_root_turns.get(turn_digest)
            if previous_root is None or _is_better_observation(
                parsed_turn, previous_root
            ):
                best_root_turns[turn_digest] = parsed_turn

    ordered_turns = sorted(best_turns.values(), key=_turn_sort_key)
    ordered_root_turns = sorted(best_root_turns.values(), key=_turn_sort_key)
    return ScanResult(
        turns=[parsed_turn.turn for parsed_turn in ordered_turns],
        root_turns=[parsed_turn.turn for parsed_turn in ordered_root_turns],
        diagnostics=diagnostics,
    )


def write_archive(archive: dict, path: Path) -> None:
    """Validate and write a deterministic private archive JSON document."""

    normalized = _validate_archive(archive)
    descriptor = -1
    try:
        open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW
        descriptor = os.open(path, open_flags, 0o600)
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            descriptor = -1
            json.dump(
                normalized,
                handle,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
    except OSError:
        raise ArchiveError("unable to write archive") from None
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _iter_session_files(
    paths: list[Path], diagnostics: dict[str, int]
) -> Iterator[Path]:
    try:
        candidates = sorted((Path(path) for path in paths), key=str)
    except (TypeError, ValueError):
        raise ArchiveError("invalid archive input paths") from None

    for candidate in candidates:
        try:
            if candidate.is_dir():
                yield from _walk_session_directory(candidate, diagnostics)
            elif candidate.is_file():
                if candidate.suffix == ".jsonl":
                    yield candidate
                else:
                    diagnostics["unsupported_paths"] += 1
            else:
                diagnostics["missing_paths"] += 1
        except OSError:
            diagnostics["traversal_errors"] += 1


def _walk_session_directory(
    directory: Path, diagnostics: dict[str, int]
) -> Iterator[Path]:
    def on_error(_error: OSError) -> None:
        diagnostics["traversal_errors"] += 1

    try:
        for root, directory_names, file_names in os.walk(
            directory, onerror=on_error
        ):
            directory_names.sort()
            for file_name in sorted(file_names):
                if file_name.endswith(".jsonl"):
                    yield Path(root) / file_name
    except OSError:
        diagnostics["traversal_errors"] += 1


def _observation_from_parsed(
    source_kind: str,
    turn_digest: str,
    parsed_turn: _ParsedTurn,
) -> dict[str, object]:
    return {
        "turn_id_sha256": turn_digest,
        "started_at": parsed_turn.turn.started_at,
        "duration_seconds": parsed_turn.turn.duration_seconds,
        "event_timing_usable": parsed_turn.event_timing_usable,
        "source_kind": source_kind,
    }


def _parsed_from_observation(observation: Mapping[str, object]) -> _ParsedTurn:
    turn_digest = observation["turn_id_sha256"]
    started_at = observation["started_at"]
    duration_seconds = observation["duration_seconds"]
    event_timing_usable = observation["event_timing_usable"]
    if not isinstance(turn_digest, str):
        raise ArchiveError("invalid archive: malformed observation")
    if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
        raise ArchiveError("invalid archive: malformed observation")
    if not isinstance(duration_seconds, (int, float)) or isinstance(
        duration_seconds, bool
    ):
        raise ArchiveError("invalid archive: malformed observation")
    if not isinstance(event_timing_usable, bool):
        raise ArchiveError("invalid archive: malformed observation")
    return _ParsedTurn(
        turn_id=turn_digest,
        turn=CompletedTurn(float(started_at), float(duration_seconds)),
        event_timing_usable=event_timing_usable,
    )


def _archive_observation_sort_key(
    item: tuple[tuple[str, str], _ParsedTurn]
) -> tuple[float, str, str, float, bool]:
    (source_kind, turn_digest), parsed_turn = item
    return (
        parsed_turn.turn.started_at,
        source_kind,
        turn_digest,
        parsed_turn.turn.duration_seconds,
        not parsed_turn.event_timing_usable,
    )


def _diagnostics_to_dict(
    diagnostics: ScanDiagnostics,
    path_diagnostics: Mapping[str, int],
) -> dict[str, object]:
    values: dict[str, object] = {
        field_name: (
            len(diagnostics.malformed_files)
            if field_name == "malformed_files"
            else int(getattr(diagnostics, field_name))
        )
        for field_name in _DIAGNOSTIC_COUNTERS
    }
    values["unknown_sources"] = dict(
        sorted(diagnostics.unknown_sources.items())
    )
    for field_name, value in path_diagnostics.items():
        values[field_name] = int(value)
    return values


def _merge_diagnostics(
    target: ScanDiagnostics,
    raw_diagnostics: object,
) -> None:
    if not isinstance(raw_diagnostics, Mapping):
        raise ArchiveError("invalid archive: malformed diagnostics")
    for field_name in _DIAGNOSTIC_COUNTERS:
        value = raw_diagnostics[field_name]
        if field_name == "malformed_files":
            target.imported_malformed_files += value
            continue
        setattr(
            target,
            field_name,
            getattr(target, field_name) + value,
        )
    for source_label, count in raw_diagnostics["unknown_sources"].items():
        target.unknown_sources[source_label] += count


def _validate_archive(value: object) -> dict:
    if not isinstance(value, Mapping):
        _invalid("archive must be an object")
    if set(value) - _ARCHIVE_KEYS:
        _invalid("unsupported archive fields")
    if value.get("schema") != _SCHEMA:
        _invalid("unknown archive schema")
    if value.get("schema_version") != _SCHEMA_VERSION or isinstance(
        value.get("schema_version"), bool
    ):
        _invalid("unsupported archive schema version")
    if value.get("methodology_version") != METHODOLOGY_VERSION:
        _invalid("incompatible archive methodology")

    if "label" in value:
        label = value["label"]
        _validate_label(label, allow_empty=True)
    else:
        label = None

    observations_value = value.get("observations")
    if not isinstance(observations_value, list):
        _invalid("malformed observations")
    observations = [
        _validate_observation(observation)
        for observation in observations_value
    ]
    diagnostics = _validate_diagnostics(value.get("diagnostics", {}))
    result: dict[str, object] = {
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "observations": sorted(observations, key=_observation_value_sort_key),
        "diagnostics": diagnostics,
    }
    if label is not None:
        result["label"] = label
    return result


def _validate_observation(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _invalid("malformed observation")
    if set(value) - _OBSERVATION_KEYS:
        _invalid("unsupported observation fields")
    if "turn_id_sha256" not in value:
        _invalid("malformed observation ID")
    turn_digest = value["turn_id_sha256"]
    if not isinstance(turn_digest, str) or _HEX_DIGEST.fullmatch(turn_digest) is None:
        _invalid("malformed observation ID")

    started_at = _finite_nonnegative_number(
        value.get("started_at"), timestamp=True
    )
    duration_seconds = _finite_nonnegative_number(
        value.get("duration_seconds")
    )
    if not isinstance(value.get("event_timing_usable"), bool):
        _invalid("malformed observation timing flag")
    source_kind = value.get("source_kind")
    if not isinstance(source_kind, str) or source_kind not in _ALLOWED_SOURCE_KINDS:
        _invalid("unsupported observation source")
    return {
        "turn_id_sha256": turn_digest,
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "event_timing_usable": value["event_timing_usable"],
        "source_kind": source_kind,
    }


def _validate_diagnostics(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _invalid("malformed diagnostics")
    if set(value) - _DIAGNOSTIC_KEYS:
        _invalid("unsupported diagnostic fields")
    result: dict[str, object] = {}
    for field_name in _DIAGNOSTIC_COUNTERS:
        raw_count = value.get(field_name, 0)
        if (
            not isinstance(raw_count, int)
            or isinstance(raw_count, bool)
            or raw_count < 0
        ):
            _invalid("malformed diagnostic count")
        result[field_name] = raw_count

    raw_sources = value.get("unknown_sources", {})
    if not isinstance(raw_sources, Mapping):
        _invalid("malformed source diagnostics")
    source_counts: dict[str, int] = {}
    for source_label, raw_count in raw_sources.items():
        if not isinstance(source_label, str) or not _safe_source_label(
            source_label
        ):
            _invalid("malformed source diagnostics")
        if (
            not isinstance(raw_count, int)
            or isinstance(raw_count, bool)
            or raw_count < 0
        ):
            _invalid("malformed source diagnostics")
        source_counts[source_label] = raw_count
    result["unknown_sources"] = dict(sorted(source_counts.items()))
    return result


def _observation_value_sort_key(
    observation: Mapping[str, object],
) -> tuple[float, str, str, float, bool]:
    return (
        observation["started_at"],
        observation["source_kind"],
        observation["turn_id_sha256"],
        observation["duration_seconds"],
        not observation["event_timing_usable"],
    )


def _finite_nonnegative_number(
    value: object, *, timestamp: bool = False
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _invalid("malformed observation number")
    try:
        converted = float(value)
    except (OverflowError, ValueError, TypeError):
        _invalid("invalid observation number")
    if not math.isfinite(converted) or converted < 0.0:
        _invalid("invalid observation number")
    if timestamp:
        try:
            datetime.fromtimestamp(converted, UTC)
        except (OverflowError, OSError, ValueError):
            _invalid("invalid observation timestamp")
    return converted


def _validate_label(value: object, *, allow_empty: bool) -> None:
    if not isinstance(value, str):
        _invalid("malformed archive label")
    if not allow_empty and not value:
        _invalid("malformed archive label")
    if len(value) > 256:
        _invalid("archive label is too long")
    if any(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    ):
        _invalid("archive label contains control characters")


def _safe_source_label(value: str) -> bool:
    return bool(_UNKNOWN_FINGERPRINT.fullmatch(value)) or bool(
        _UNKNOWN_SOURCE_LABEL.fullmatch(value)
    )


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _invalid(reason: str) -> NoReturn:
    raise ArchiveError(f"invalid archive: {reason}")
