from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_hour_tracker.cli import _display_path, _resolve_report_range, main


def timestamp(day: int) -> float:
    return datetime(2026, 6, day, tzinfo=timezone.utc).timestamp()


def event_rollout(source: object = "cli") -> list[dict[str, object]]:
    records: list[dict[str, object]] = [{"type": "session_meta", "source": source}]
    for index, day in enumerate((1, 3), start=1):
        started_timestamp = timestamp(day)
        records.extend(
            (
                {
                    "timestamp": datetime.fromtimestamp(
                        started_timestamp, timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                    "type": "event_msg",
                    "payload": {
                        "type": "task_started",
                        "turn_id": f"turn-{index}",
                        "started_at": timestamp(day),
                    },
                },
                {
                    "timestamp": datetime.fromtimestamp(
                        started_timestamp + 1_800, timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                    "type": "message",
                },
                {
                    "timestamp": datetime.fromtimestamp(
                        started_timestamp + 3_600, timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": f"turn-{index}",
                        "duration_ms": 3_600_000,
                    },
                },
            )
        )
    return records


def write_rollout(root: Path, name: str, records: list[object]) -> Path:
    path = root / name
    with path.open("wb") as handle:
        for record in records:
            if isinstance(record, bytes):
                handle.write(record + b"\n")
            elif isinstance(record, str):
                handle.write((record + "\n").encode("utf-8"))
            else:
                handle.write((json.dumps(record) + "\n").encode("utf-8"))
    return path


class CliTests(unittest.TestCase):
    def test_resolve_report_range_defaults_to_thirty_completed_days(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            None,
            None,
            date(2026, 3, 1),
            stderr,
        )

        self.assertEqual(
            report_range,
            (date(2026, 1, 30), date(2026, 2, 28)),
        )
        self.assertEqual((report_range[1] - report_range[0]).days + 1, 30)
        self.assertEqual(stderr.getvalue(), "")

    def test_resolve_report_range_with_start_only_ends_yesterday(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            "2026-02-10",
            None,
            date(2026, 3, 1),
            stderr,
        )

        self.assertEqual(
            report_range,
            (date(2026, 2, 10), date(2026, 2, 28)),
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_resolve_report_range_with_end_only_starts_thirty_days_prior(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            None,
            "2026-03-01",
            date(2026, 3, 1),
            stderr,
        )

        self.assertEqual(
            report_range,
            (date(2026, 1, 31), date(2026, 3, 1)),
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_resolve_report_range_handles_leap_day_and_month_rollover(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            None,
            "2024-03-01",
            date(2024, 3, 1),
            stderr,
        )

        self.assertEqual(
            report_range,
            (date(2024, 2, 1), date(2024, 3, 1)),
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_resolve_report_range_rejects_invalid_start(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            "not-a-date",
            None,
            date(2026, 3, 1),
            stderr,
        )

        self.assertIsNone(report_range)
        self.assertIn("error: invalid start date: 'not-a-date'", stderr.getvalue())

    def test_resolve_report_range_rejects_invalid_end(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            None,
            "not-a-date",
            date(2026, 3, 1),
            stderr,
        )

        self.assertIsNone(report_range)
        self.assertIn("error: invalid end date: 'not-a-date'", stderr.getvalue())

    def test_resolve_report_range_rejects_reversed_explicit_range(self) -> None:
        stderr = io.StringIO()

        report_range = _resolve_report_range(
            "2026-03-02",
            "2026-03-01",
            date(2026, 3, 1),
            stderr,
        )

        self.assertIsNone(report_range)
        self.assertIn("error: end date must not precede start date", stderr.getvalue())

    def _run(self, root: Path, *extra: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            "--sessions-dir",
            str(root),
            "--start",
            "2026-06-01",
            "--end",
            "2026-06-03",
            "--timezone",
            "UTC",
            *extra,
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(arguments)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def _run_unbounded(self, root: Path, *extra: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            "--sessions-dir",
            str(root),
            "--timezone",
            "UTC",
            *extra,
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(arguments)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_share_uses_canonical_thirty_day_range_ending_yesterday(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(root, "interactive.jsonl", event_rollout())
            with patch(
                "agent_hour_tracker.cli._today_in_timezone",
                return_value=date(2026, 6, 30),
            ):
                exit_code, stdout, stderr = self._run_unbounded(root, "--share")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn(
            "30 complete calendar days | 2026-05-31 to 2026-06-29",
            stdout,
        )
        self.assertNotIn("DAILY AGENT-HOURS", stdout)

    def test_share_rejects_explicit_range_and_format_before_scanning(self) -> None:
        conflict_arguments = (
            ("--start", "2026-06-01"),
            ("--end", "2026-06-03"),
            ("--format", "text"),
            ("--format", "csv"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for conflicting in conflict_arguments:
                with self.subTest(conflicting=conflicting):
                    with patch("agent_hour_tracker.cli.scan_sessions") as scan:
                        exit_code, stdout, stderr = self._run_unbounded(
                            root, "--share", *conflicting
                        )

                    self.assertEqual(exit_code, 2)
                    self.assertEqual(stdout, "")
                    self.assertEqual(
                        stderr,
                        "error: --share cannot be combined with --start, --end, or --format\n",
                    )
                    scan.assert_not_called()

    def test_omitted_format_defaults_to_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(root, "interactive.jsonl", event_rollout())
            exit_code, stdout, stderr = self._run_unbounded(
                root,
                "--start",
                "2026-06-01",
                "--end",
                "2026-06-03",
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("AGENT-HOUR SUMMARY", stdout)

    def test_share_diagnostics_are_aggregate_only_and_redact_markers(self) -> None:
        path_marker = "SHARE_PRIVATE_PATH_MARKER"
        source_marker = "SHARE_UNKNOWN_SOURCE_MARKER"
        transcript_marker = "SHARE_TRANSCRIPT_MARKER"
        reasoning_marker = "SHARE_REASONING_MARKER"
        tool_marker = "SHARE_TOOL_OUTPUT_MARKER"
        instructions_marker = "SHARE_BASE_INSTRUCTIONS_MARKER"
        last_message_marker = "SHARE_LAST_AGENT_MESSAGE_MARKER"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(root, "batch.jsonl", event_rollout({"exec": "batch"}))
            write_rollout(
                root,
                f"{path_marker}.jsonl",
                [
                    {"type": "session_meta", "source": source_marker},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "turn_id": "incomplete",
                            "started_at": timestamp(1),
                        },
                    },
                    b'{"type":"event_msg","payload":{"type":"task_complete"',
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "missing",
                            "duration_ms": 1000,
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "fallback",
                            "started_at": timestamp(1),
                            "completed_at": timestamp(1) + 30,
                        },
                    },
                    {
                        "type": "message",
                        "transcript": transcript_marker,
                        "reasoning": reasoning_marker,
                        "tool_output": tool_marker,
                        "base_instructions": instructions_marker,
                        "last_agent_message": last_message_marker,
                    },
                ],
            )
            with patch(
                "agent_hour_tracker.cli._today_in_timezone",
                return_value=date(2026, 6, 30),
            ):
                exit_code, stdout, stderr = self._run_unbounded(root, "--share")

        self.assertEqual(exit_code, 0)
        self.assertIn("Excluded batch turns: 2", stderr)
        self.assertIn("Malformed lines: 1", stderr)
        self.assertIn("Malformed files: 1", stderr)
        self.assertIn("Incomplete turns: 1", stderr)
        self.assertIn("Unmatched completions: 1", stderr)
        self.assertIn("Duration fallbacks: 1", stderr)
        self.assertIn("Event timing fallbacks: 1", stderr)
        self.assertNotIn("Unknown sources:", stderr)
        self.assertNotIn("SHARE_UNKNOWN", stderr)
        self.assertNotIn(str(root), stderr)
        self.assertNotIn("SHARE_PRIVATE_PATH_MARKER", stderr)
        for marker in (
            transcript_marker,
            reasoning_marker,
            tool_marker,
            instructions_marker,
            last_message_marker,
        ):
            self.assertNotIn(marker, stdout + stderr)

    def test_share_missing_sessions_directory_redacts_path(self) -> None:
        marker = "SHARE_MISSING_DIRECTORY_PRIVATE_MARKER"
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / marker
            exit_code, stdout, stderr = self._run_unbounded(missing, "--share")

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            "error: sessions directory is missing or not a directory\n",
        )
        self.assertNotIn(marker, stderr)
        self.assertNotIn(str(missing), stderr)

    def test_interactive_rollout_includes_zero_day_in_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(root, "interactive.jsonl", event_rollout())
            exit_code, stdout, stderr = self._run(root)

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Calendar days:               3", stdout)
        self.assertIn("2026-06-02                  0.00          0", stdout)

    def test_csv_output_contains_header_and_three_daily_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(root, "interactive.jsonl", event_rollout())
            exit_code, stdout, _ = self._run(root, "--format", "csv")

        rows = stdout.splitlines()
        self.assertEqual(exit_code, 0)
        self.assertEqual(rows[0], "date,agent_hours,completed_turns")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2], "2026-06-02,0.000000,0")

    def test_missing_sessions_directory_returns_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            exit_code, stdout, stderr = self._run(missing)

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("sessions directory", stderr.lower())
        self.assertIn(str(missing), stderr)

    def test_reverse_date_range_returns_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = [
                "--sessions-dir",
                str(root),
                "--start",
                "2026-06-03",
                "--end",
                "2026-06-01",
                "--timezone",
                "UTC",
            ]
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(arguments)

        self.assertEqual(exit_code, 2)
        self.assertIn("end date", stderr.getvalue().lower())

    def test_invalid_date_returns_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = [
                "--sessions-dir",
                str(root),
                "--start",
                "not-a-date",
                "--end",
                "2026-06-03",
                "--timezone",
                "UTC",
            ]
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(arguments)

        self.assertEqual(exit_code, 2)
        self.assertIn("start date", stderr.getvalue().lower())

    def test_invalid_end_date_returns_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = [
                "--sessions-dir",
                str(root),
                "--start",
                "2026-06-01",
                "--end",
                "not-a-date",
                "--timezone",
                "UTC",
            ]
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(arguments)

        self.assertEqual(exit_code, 2)
        self.assertIn("end date", stderr.getvalue().lower())

    def test_invalid_timezone_returns_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exit_code, _, stderr = self._run(root, "--timezone", "Not/A-Timezone")

        self.assertEqual(exit_code, 2)
        self.assertIn("timezone", stderr.lower())

    def test_diagnostics_are_safe_and_include_nonzero_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(
                root,
                "batch.jsonl",
                event_rollout({"exec": "batch"}),
            )
            write_rollout(
                root,
                "diagnostics.jsonl",
                [
                    {"type": "session_meta", "source": "future-ui"},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "turn_id": "incomplete",
                            "started_at": timestamp(1),
                        },
                    },
                    b'{"type":"event_msg","payload":{"type":"task_complete"',
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "missing",
                            "duration_ms": 1000,
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "fallback",
                            "started_at": timestamp(1),
                            "completed_at": timestamp(1) + 30,
                        },
                    },
                    {
                        "type": "message",
                        "response_item": {"output": "TOP_SECRET_TRANSCRIPT"},
                    },
                ],
            )
            exit_code, stdout, stderr = self._run(root)

        self.assertEqual(exit_code, 0)
        self.assertIn("Excluded batch turns: 2", stderr)
        self.assertIn("Unknown sources:", stderr)
        self.assertIn("unknown:string:", stderr)
        self.assertNotIn("future-ui", stderr)
        self.assertIn("Malformed lines: 1", stderr)
        self.assertIn("diagnostics.jsonl", stderr)
        self.assertIn("Incomplete turns: 1", stderr)
        self.assertIn("Unmatched completions: 1", stderr)
        self.assertIn("Duration fallbacks: 1", stderr)
        self.assertIn("Event timing fallbacks: 1", stderr)
        self.assertNotIn("TOP_SECRET_TRANSCRIPT", stdout + stderr)

    def test_diagnostics_report_duplicates_and_event_timing_fallbacks(self) -> None:
        records = [
            {"type": "session_meta", "source": "cli"},
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "duplicate",
                    "started_at": timestamp(1),
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "duplicate",
                    "duration_ms": 1_000,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_rollout(root, "one.jsonl", records)
            write_rollout(root, "two.jsonl", records)
            exit_code, _, stderr = self._run(root)

        self.assertEqual(exit_code, 0)
        self.assertIn("Duplicate turns: 1", stderr)
        self.assertIn("Event timing fallbacks: 2", stderr)

    def test_malformed_paths_are_bounded_and_capped(self) -> None:
        file_count = 23
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(file_count):
                name = f"bad-{index:02d}-{'x' * 180}.jsonl"
                write_rollout(root, name, [b'{"type":"task_complete",'])
            exit_code, _, stderr = self._run(root)

        self.assertEqual(exit_code, 0)
        self.assertIn(f"Malformed lines: {file_count}", stderr)
        self.assertRegex(stderr, r"3 malformed file paths omitted")
        path_lines = [line for line in stderr.splitlines() if line.startswith('  "')]
        self.assertEqual(len(path_lines), 20)
        self.assertTrue(all(len(line) <= 130 for line in path_lines))

    def test_display_path_escapes_control_characters_and_caps_length(self) -> None:
        path = Path("/tmp") / f"bad-\n\x1b[31m-{'x' * 150}.jsonl"

        displayed = _display_path(path)

        self.assertNotIn("\n", displayed)
        self.assertNotIn("\x1b", displayed)
        self.assertIn("\\n", displayed)
        self.assertIn("\\u001b", displayed)
        self.assertLessEqual(len(displayed), 130)
        self.assertTrue(displayed.endswith('..."'))


if __name__ == "__main__":
    unittest.main()
