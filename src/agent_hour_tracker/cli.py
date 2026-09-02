"""Command-line interface for the local agent-hour report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal

from . import METHODOLOGY_VERSION, __version__
from .metrics import build_report_metrics
from .report import render_csv, render_share, render_text
from .scanner import ScanDiagnostics, scan_sessions

__all__ = ["main"]

_MAX_MALFORMED_FILE_PATHS = 20
_MAX_DISPLAYED_PATH_LENGTH = 120


def main(argv: list[str] | None = None) -> int:
    """Run the agent-hour tracker CLI and return a process exit code."""

    parser = _build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if arguments.share and (
        arguments.start is not None
        or arguments.end is not None
        or arguments.format is not None
    ):
        print(
            "error: --share cannot be combined with --start, --end, or --format",
            file=sys.stderr,
        )
        return 2

    timezone = _resolve_timezone(arguments.timezone, sys.stderr)
    if timezone is None:
        return 2
    report_range = _resolve_report_range(
        None if arguments.share else arguments.start,
        None if arguments.share else arguments.end,
        _today_in_timezone(timezone),
        sys.stderr,
    )
    if report_range is None:
        return 2
    start, end = report_range

    sessions_dir = arguments.sessions_dir
    if not sessions_dir.is_dir():
        if arguments.share:
            print(
                "error: sessions directory is missing or not a directory",
                file=sys.stderr,
            )
        else:
            print(
                f"error: sessions directory is missing or not a directory: {sessions_dir}",
                file=sys.stderr,
            )
        return 2

    scan_result = scan_sessions(sessions_dir)
    report = build_report_metrics(
        scan_result.turns,
        start,
        end,
        timezone,
        root_turns=scan_result.root_turns,
    )
    if arguments.share:
        sys.stdout.write(render_share(report, __version__, METHODOLOGY_VERSION))
    elif arguments.format == "csv":
        sys.stdout.write(render_csv(report))
    else:
        sys.stdout.write(render_text(report))
    _write_diagnostics(scan_result.diagnostics, sys.stderr, share_safe=arguments.share)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-hours",
        description="Report cumulative Codex agent-hours from local session metadata.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path.home() / ".codex" / "sessions",
        help="Codex sessions directory (default: ~/.codex/sessions)",
    )
    parser.add_argument(
        "--start",
        default=None,
        metavar="YYYY-MM-DD",
        help="first report date, inclusive (default: 30 completed days)",
    )
    parser.add_argument(
        "--end",
        default=None,
        metavar="YYYY-MM-DD",
        help="last report date, inclusive (default: yesterday)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "csv"),
        default=None,
        help="report format (default: text; unavailable with --share)",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="render a sanitized Archive Score card for sharing (canonical 30-day range)",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        metavar="IANA_ZONE",
        help="IANA timezone (default: discovered local timezone)",
    )
    return parser


def _parse_date(value: str, label: str, stderr: TextIO) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        print(f"error: invalid {label} date: {value!r}", file=stderr)
        return None


def _resolve_report_range(
    start_value: str | None,
    end_value: str | None,
    today: date,
    stderr: TextIO,
) -> tuple[date, date] | None:
    start = (
        _parse_date(start_value, "start", stderr)
        if start_value is not None
        else None
    )
    if start_value is not None and start is None:
        return None

    end = (
        _parse_date(end_value, "end", stderr)
        if end_value is not None
        else None
    )
    if end_value is not None and end is None:
        return None

    if end is None:
        end = today - timedelta(days=1)
    if start is None:
        start = end - timedelta(days=29)

    if end < start:
        print("error: end date must not precede start date", file=stderr)
        return None
    return start, end


def _resolve_timezone(value: str | None, stderr: TextIO) -> ZoneInfo | None:
    if value is not None:
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            print(f"error: invalid timezone: {value!r}", file=stderr)
            return None

    timezone_name, warning = _discover_timezone_name()
    if warning:
        print(
            f"warning: using {timezone_name} for the local timezone",
            file=stderr,
        )
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        print("warning: local timezone is unavailable; using UTC", file=stderr)
        return ZoneInfo("UTC")


def _discover_timezone_name() -> tuple[str, bool]:
    try:
        timezone_name = tzlocal.get_localzone_name()
    except Exception:  # noqa: BLE001 - platform-specific discovery may fail broadly
        return "UTC", True
    if not isinstance(timezone_name, str) or not timezone_name:
        return "UTC", True
    return timezone_name, False


def _today_in_timezone(timezone: ZoneInfo) -> date:
    return datetime.now(timezone).date()


def _write_diagnostics(
    diagnostics: ScanDiagnostics,
    stderr: TextIO,
    share_safe: bool = False,
) -> None:
    if share_safe:
        if diagnostics.malformed_lines:
            print(f"Malformed lines: {diagnostics.malformed_lines}", file=stderr)
        if diagnostics.malformed_files:
            print(f"Malformed files: {len(diagnostics.malformed_files)}", file=stderr)
        if diagnostics.incomplete_turns:
            print(f"Incomplete turns: {diagnostics.incomplete_turns}", file=stderr)
        if diagnostics.unmatched_completions:
            print(
                f"Unmatched completions: {diagnostics.unmatched_completions}",
                file=stderr,
            )
        if diagnostics.duration_fallbacks:
            print(f"Duration fallbacks: {diagnostics.duration_fallbacks}", file=stderr)
        if diagnostics.event_timing_fallbacks:
            print(
                f"Event timing fallbacks: {diagnostics.event_timing_fallbacks}",
                file=stderr,
            )
        if diagnostics.duplicate_turns:
            print(f"Duplicate turns: {diagnostics.duplicate_turns}", file=stderr)
        if diagnostics.excluded_batch_turns:
            print(
                f"Excluded batch turns: {diagnostics.excluded_batch_turns}",
                file=stderr,
            )
        return

    if diagnostics.malformed_lines:
        print(f"Malformed lines: {diagnostics.malformed_lines}", file=stderr)
    if diagnostics.malformed_files:
        malformed_files = sorted(diagnostics.malformed_files)
        print(f"Malformed files: {len(malformed_files)}", file=stderr)
        for path in malformed_files[:_MAX_MALFORMED_FILE_PATHS]:
            print(f"  {_display_path(path)}", file=stderr)
        omitted = len(malformed_files) - _MAX_MALFORMED_FILE_PATHS
        if omitted > 0:
            print(
                f"  ... {omitted} malformed file paths omitted",
                file=stderr,
            )
    if diagnostics.incomplete_turns:
        print(f"Incomplete turns: {diagnostics.incomplete_turns}", file=stderr)
    if diagnostics.unmatched_completions:
        print(
            f"Unmatched completions: {diagnostics.unmatched_completions}",
            file=stderr,
        )
    if diagnostics.duration_fallbacks:
        print(f"Duration fallbacks: {diagnostics.duration_fallbacks}", file=stderr)
    if diagnostics.event_timing_fallbacks:
        print(
            f"Event timing fallbacks: {diagnostics.event_timing_fallbacks}",
            file=stderr,
        )
    if diagnostics.duplicate_turns:
        print(f"Duplicate turns: {diagnostics.duplicate_turns}", file=stderr)
    if diagnostics.excluded_batch_turns:
        print(
            f"Excluded batch turns: {diagnostics.excluded_batch_turns}",
            file=stderr,
        )
    if diagnostics.unknown_sources:
        print("Unknown sources:", file=stderr)
        for source, count in sorted(diagnostics.unknown_sources.items()):
            print(f"  {source}: {count}", file=stderr)


def _display_path(path: Path) -> str:
    escaped = json.dumps(str(path), ensure_ascii=True)
    if len(escaped) <= _MAX_DISPLAYED_PATH_LENGTH:
        return escaped
    return escaped[: _MAX_DISPLAYED_PATH_LENGTH - 4] + '..."'
