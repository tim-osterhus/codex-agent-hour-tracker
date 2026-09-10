"""Command-line interface for the local agent-hour report."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal

from . import METHODOLOGY_VERSION, __version__
from .metrics import LeverageMetrics, build_leverage, build_report_metrics
from .report import render_csv, render_share, render_text
from .scanner import (
    ScanDiagnostics,
    ScanResult,
    scan_sessions,
)

__all__ = ["main"]

_MAX_MALFORMED_FILE_PATHS = 20
_MAX_DISPLAYED_PATH_LENGTH = 120


@dataclass(frozen=True, slots=True)
class _ReportInput:
    """One private input and its deduplicated report view."""

    label: str
    result: ScanResult


def main(argv: list[str] | None = None) -> int:
    """Run the agent-hour tracker CLI and return a process exit code."""

    parser = _build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if not _validate_arguments(arguments, sys.stderr):
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

    if arguments.export is not None:
        return _export_archive(arguments, sys.stderr)

    try:
        scan_result, report_inputs = _load_report_inputs(arguments)
    except _InputError as error:
        print(str(error), file=sys.stderr)
        return 2

    report = build_report_metrics(
        scan_result.turns,
        start,
        end,
        timezone,
        root_turns=scan_result.root_turns,
    )
    try:
        leverage = _resolve_leverage(arguments, report.total_agent_hours, len(report.days))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    scope = "including-exec" if arguments.include_exec else "interactive-only"
    if arguments.share:
        if arguments.format == "json":
            from .share import render_share_json

            sys.stdout.write(
                render_share_json(
                    report,
                    __version__,
                    METHODOLOGY_VERSION,
                    start=start,
                    end=end,
                    timezone=timezone,
                    scope=scope,
                    leverage=leverage,
                )
            )
        else:
            sys.stdout.write(
                render_share(
                    report,
                    __version__,
                    METHODOLOGY_VERSION,
                    start=start,
                    end=end,
                    timezone=timezone,
                    scope=scope,
                    leverage=leverage,
                )
            )
    elif arguments.format == "csv":
        sys.stdout.write(render_csv(report))
    else:
        output = render_text(
            report,
            start=start,
            end=end,
            timezone=timezone,
            scope=scope,
            methodology_version=METHODOLOGY_VERSION,
            leverage=leverage,
            monthly=arguments.monthly,
        )
        if len(report_inputs) > 1:
            output += _render_input_breakdown(report_inputs, start, end, timezone, arguments)
        sys.stdout.write(output)
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
        action="append",
        default=None,
        help="Codex sessions directory; repeat to combine roots (default: ~/.codex/sessions)",
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
        choices=("text", "csv", "json"),
        default=None,
        help="report format (default: text; JSON is available with --share)",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="render a sanitized Agent-Hour Score card for sharing (canonical 30-day range)",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        metavar="IANA_ZONE",
        help="IANA timezone (default: discovered local timezone)",
    )
    parser.add_argument(
        "--merge",
        type=Path,
        action="append",
        default=None,
        metavar="ARCHIVE",
        help="private archive export to merge; repeatable",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=None,
        metavar="PATH",
        help="write a private metadata archive and exit",
    )
    parser.add_argument(
        "--label",
        default=None,
        metavar="LABEL",
        help="optional label for a private archive export",
    )
    parser.add_argument(
        "--include-exec",
        action="store_true",
        help="include batch/exec turns in aggregate totals",
    )
    parser.add_argument(
        "--human-hours",
        type=float,
        default=None,
        metavar="TOTAL",
        help="reported human-hours for the selected period (for leverage)",
    )
    parser.add_argument(
        "--human-hours-per-week",
        type=float,
        default=None,
        metavar="WEEKLY",
        help="estimated human-hours per week (for leverage)",
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="append a monthly breakdown to text output",
    )
    return parser


class _InputError(ValueError):
    """Safe user-facing input error that does not include private contents."""


def _validate_arguments(arguments: argparse.Namespace, stderr: TextIO) -> bool:
    if arguments.share and (
        arguments.start is not None
        or arguments.end is not None
        or (arguments.format is not None and arguments.format != "json")
    ):
        print(
            "error: --share cannot be combined with --start, --end, or --format",
            file=stderr,
        )
        return False
    if arguments.share and arguments.monthly:
        print("error: --monthly is unavailable with --share", file=stderr)
        return False
    if arguments.format == "json" and not arguments.share:
        print("error: --format json requires --share", file=stderr)
        return False
    if arguments.monthly and arguments.format not in (None, "text"):
        print("error: --monthly is available only for text output", file=stderr)
        return False
    if arguments.label is not None and arguments.export is None:
        print("error: --label requires --export", file=stderr)
        return False
    if arguments.export is not None and (
        arguments.share
        or arguments.start is not None
        or arguments.end is not None
        or arguments.format is not None
        or arguments.monthly
        or arguments.human_hours is not None
        or arguments.human_hours_per_week is not None
        or arguments.merge
    ):
        print(
            "error: --export cannot be combined with report, share, merge, or human-hours options",
            file=stderr,
        )
        return False
    if arguments.human_hours is not None and arguments.human_hours_per_week is not None:
        print(
            "error: --human-hours and --human-hours-per-week are mutually exclusive",
            file=stderr,
        )
        return False
    if (
        arguments.format == "csv"
        and (
            arguments.human_hours is not None
            or arguments.human_hours_per_week is not None
        )
    ):
        print("error: human-hours leverage is available only for text or share output", file=stderr)
        return False
    for value, label in (
        (arguments.human_hours, "human hours"),
        (arguments.human_hours_per_week, "human hours per week"),
    ):
        if value is not None and (not math.isfinite(value) or value <= 0.0):
            print(f"error: {label} must be finite and positive", file=stderr)
            return False
    return True


def _resolve_input_dirs(arguments: argparse.Namespace) -> list[Path]:
    if arguments.sessions_dir is not None:
        return list(arguments.sessions_dir)
    if arguments.merge:
        return []
    return [Path.home() / ".codex" / "sessions"]


def _load_report_inputs(
    arguments: argparse.Namespace,
) -> tuple[ScanResult, list[_ReportInput]]:
    directories = _resolve_input_dirs(arguments)
    merge_paths = list(arguments.merge or [])
    if not directories and not merge_paths:
        raise _InputError("error: no sessions directories or archives were provided")
    for sessions_dir in directories:
        if not sessions_dir.is_dir():
            if arguments.share:
                raise _InputError(
                    "error: sessions directory is missing or not a directory"
                )
            raise _InputError(
                "error: sessions directory is missing or not a directory: "
                f"{sessions_dir}"
            )

    try:
        from .archive import ArchiveError, collect_archive, load_archive, merge_archives
    except ImportError as error:
        raise _InputError("error: archive support is unavailable") from error

    if len(directories) == 1 and not merge_paths and not arguments.include_exec:
        result = scan_sessions(directories[0])
        return result, [_ReportInput(str(directories[0]), result)]

    archives: list[dict] = []
    inputs: list[_ReportInput] = []
    for sessions_dir in directories:
        try:
            archive = collect_archive([sessions_dir])
            result = merge_archives([archive], include_exec=arguments.include_exec)
        except (ArchiveError, OSError, TypeError, ValueError) as error:
            raise _InputError("error: unable to scan sessions directory") from error
        archives.append(archive)
        inputs.append(_ReportInput(_archive_label(archive, sessions_dir), result))
    for archive_path in merge_paths:
        try:
            archive = load_archive(archive_path)
            result = merge_archives([archive], include_exec=arguments.include_exec)
        except (ArchiveError, OSError, TypeError, ValueError) as error:
            raise _InputError("error: unable to load private archive") from error
        archives.append(archive)
        inputs.append(_ReportInput(_archive_label(archive, archive_path), result))
    try:
        result = merge_archives(archives, include_exec=arguments.include_exec)
    except (ArchiveError, OSError, TypeError, ValueError) as error:
        raise _InputError("error: unable to merge report inputs") from error
    return result, inputs


def _archive_label(archive: dict, fallback: Path) -> str:
    label = archive.get("label")
    if isinstance(label, str) and label:
        return label
    return str(fallback)


def _resolve_leverage(
    arguments: argparse.Namespace,
    total_agent_hours: float,
    day_count: int,
) -> LeverageMetrics | None:
    if arguments.human_hours is None and arguments.human_hours_per_week is None:
        return None
    return build_leverage(
        total_agent_hours,
        human_hours=arguments.human_hours,
        human_hours_per_week=arguments.human_hours_per_week,
        calendar_day_count=day_count,
    )


def _export_archive(arguments: argparse.Namespace, stderr: TextIO) -> int:
    directories = _resolve_input_dirs(arguments)
    if not directories:
        print("error: --export requires at least one sessions directory", file=stderr)
        return 2
    for sessions_dir in directories:
        if not sessions_dir.is_dir():
            print(
                "error: sessions directory is missing or not a directory: "
                f"{sessions_dir}",
                file=stderr,
            )
            return 2
    try:
        from .archive import ArchiveError, collect_archive, write_archive

        archive = collect_archive(directories, label=arguments.label or "")
        write_archive(archive, arguments.export)
    except (ArchiveError, OSError, TypeError, ValueError):
        print("error: unable to write private archive", file=stderr)
        return 2
    _write_archive_diagnostics(archive, stderr)
    return 0


def _render_input_breakdown(
    inputs: list[_ReportInput],
    start: date,
    end: date,
    timezone: ZoneInfo,
    arguments: argparse.Namespace,
) -> str:
    scope = arguments.include_exec
    lines = [
        "",
        "PRIVATE INPUT BREAKDOWN",
        "Subtotals are per-input and may overlap; the global total above is deduplicated.",
    ]
    for index, item in enumerate(inputs, start=1):
        report = build_report_metrics(
            item.result.turns,
            start,
            end,
            timezone,
            root_turns=item.result.root_turns,
        )
        label = item.label or f"Input {index}"
        lines.append(
            f"{index}. {label}: {report.total_agent_hours:.2f} agent-hours, "
            f"{sum(day.completed_turns for day in report.days)} completed turns, "
            f"{report.active_days} active days ({'including-exec' if scope else 'interactive-only'})"
        )
    return "\n".join(lines) + "\n"


def _write_archive_diagnostics(archive: dict, stderr: TextIO) -> None:
    """Print aggregate export diagnostics without exposing archive contents."""

    diagnostics = archive.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return
    labels = (
        ("malformed_lines", "Malformed lines"),
        ("malformed_files", "Malformed files"),
        ("incomplete_turns", "Incomplete turns"),
        ("unmatched_completions", "Unmatched completions"),
        ("duration_fallbacks", "Duration fallbacks"),
        ("event_timing_fallbacks", "Event timing fallbacks"),
        ("duplicate_turns", "Duplicate turns"),
        ("excluded_batch_turns", "Excluded batch turns"),
        ("unsupported_paths", "Unsupported paths"),
        ("missing_paths", "Missing paths"),
        ("traversal_errors", "Traversal errors"),
    )
    for field_name, display_name in labels:
        value = diagnostics.get(field_name, 0)
        if isinstance(value, int) and value:
            print(f"{display_name}: {value}", file=stderr)


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
    malformed_file_count = len(diagnostics.malformed_files) + getattr(
        diagnostics, "imported_malformed_files", 0
    )
    if share_safe:
        if diagnostics.malformed_lines:
            print(f"Malformed lines: {diagnostics.malformed_lines}", file=stderr)
        if malformed_file_count:
            print(f"Malformed files: {malformed_file_count}", file=stderr)
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
        _write_boundary_diagnostics(diagnostics, stderr)
        return

    if diagnostics.malformed_lines:
        print(f"Malformed lines: {diagnostics.malformed_lines}", file=stderr)
    if malformed_file_count:
        malformed_files = sorted(diagnostics.malformed_files)
        print(f"Malformed files: {malformed_file_count}", file=stderr)
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
    _write_boundary_diagnostics(diagnostics, stderr)
    if diagnostics.unknown_sources:
        print("Unknown sources:", file=stderr)
        for source, count in sorted(diagnostics.unknown_sources.items()):
            print(f"  {source}: {count}", file=stderr)


def _write_boundary_diagnostics(
    diagnostics: ScanDiagnostics,
    stderr: TextIO,
) -> None:
    for field_name, display_name in (
        ("open_errors", "Open errors"),
        ("traversal_errors", "Traversal errors"),
        ("missing_paths", "Missing paths"),
        ("unsupported_paths", "Unsupported paths"),
    ):
        value = getattr(diagnostics, field_name, 0)
        if value:
            print(f"{display_name}: {value}", file=stderr)


def _display_path(path: Path) -> str:
    escaped = json.dumps(str(path), ensure_ascii=True)
    if len(escaped) <= _MAX_DISPLAYED_PATH_LENGTH:
        return escaped
    return escaped[: _MAX_DISPLAYED_PATH_LENGTH - 4] + '..."'
