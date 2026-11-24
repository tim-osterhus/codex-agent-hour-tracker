import csv
import io
import json
import sqlite3
import subprocess
import sys
import types
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent_hour_tracker.cli import main
from agent_hour_tracker.google_timing import antigravity_timing

_DAY = date(2026, 11, 23)
_TIME = int(datetime(2026, 11, 23, 12, tzinfo=UTC).timestamp())
_UTC = ZoneInfo("UTC")


def varint(number):
    out = bytearray()
    while number > 127:
        out.append((number & 127) | 128)
        number >>= 7
    out.append(number)
    return bytes(out)


def field(number, value):
    if isinstance(value, bytes):
        return varint(number * 8 + 2) + varint(len(value)) + value
    return varint(number * 8) + varint(value)


def stamp(offset):
    return field(1, _TIME + offset)


class GoogleUsageTests(unittest.TestCase):
    def test_antigravity_deduplicates_mirrors_and_unions_overlapping_generation_and_tools(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            conversations = root / "conversations"
            conversations.mkdir()
            original = conversations / "a.db"
            with closing(sqlite3.connect(original)) as db, db:
                db.executescript(
                    "CREATE TABLE trajectory_meta(trajectory_id TEXT);"
                    "INSERT INTO trajectory_meta VALUES ('one');"
                    "CREATE TABLE steps(metadata BLOB, step_payload BLOB);"
                )
                records = [
                    field(1, stamp(0)) + field(7, stamp(60)) + field(9, b""),
                    field(32, stamp(30)) + field(8, stamp(90)),
                    field(1, stamp(0)) + field(8, stamp(600)),  # No measured start.
                    b"\x00",  # Invalid protobuf is skipped.
                ]
                db.executemany("INSERT INTO steps VALUES (?, 'private')", [(v,) for v in records])
            (conversations / "copy.db").write_bytes(original.read_bytes())
            with patch.dict("os.environ", {"ANTIGRAVITY_DATA_DIR": str(root)}):
                timing = antigravity_timing()
        self.assertEqual(timing.daily_hours(_UTC), {_DAY: 90 / 3600})
        self.assertEqual(timing.unreadable, 2)

    def test_cli_renders_antigravity_and_combined_reports_without_exposing_ccusage_fields(self):
        def invoke(args, **kwargs):
            assert args[1:3] == ["antigravity", "daily"]
            assert "--offline" in args and "--no-cost" in args
            assert args[args.index("--timezone") + 1] == "UTC"
            data = {"daily": [{"date": _DAY.isoformat(), "totalTokens": 123, "model": "private"}],
                    "totals": {"totalTokens": 123}}
            return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")

        for command, expected_muse in (
            (["google-usage"], False),
            (["usage", "--provider", "antigravity"], False),
            (["usage", "--provider", "all"], True),
        ):
            with self.subTest(command=command), TemporaryDirectory() as folder:
                modules, calls = fake_muse(tokens={_DAY: 456}, hours={_DAY: 0.05})
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    modules,
                    patch("agent_hour_tracker.google_usage.shutil.which", return_value="/stub/ccusage"),
                    patch("agent_hour_tracker.google_usage.subprocess.run", side_effect=invoke) as ccusage,
                    patch.dict("os.environ", {"ANTIGRAVITY_DATA_DIR": folder}),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    ret = main([*command, "--start", "2026-11-23", "--end", "2026-11-24",
                                "--timezone", "UTC", "--format", "csv"])
                self.assertEqual((ret, stderr.getvalue(), ccusage.call_count), (0, "", 1))
                self.assertEqual(calls, [(None, _UTC)] if expected_muse else [])
                rows = [
                    {"date": "2026-11-23", "antigravity_tokens": "123", "antigravity_measured_agent_hours": ""},
                    {"date": "2026-11-24", "antigravity_tokens": "0", "antigravity_measured_agent_hours": ""},
                ]
                if expected_muse:
                    rows[0].update(muse_tokens="456", muse_measured_agent_hours="0.05")
                    rows[1].update(muse_tokens="0", muse_measured_agent_hours="")
                self.assertEqual(list(csv.DictReader(io.StringIO(stdout.getvalue()))), rows)

    def test_google_telemetry_and_gemini_provider_are_rejected(self):
        for command in ("usage", "google-usage"):
            for args in (["--provider", "gemini"], ["--gemini-telemetry", "telemetry.log"]):
                with self.subTest(command=command, args=args), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([command, *args]), 2)

    def test_cli_does_not_convert_failed_token_reads_to_zero_or_echo_errors(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("agent_hour_tracker.google_usage.shutil.which", return_value="/stub/ccusage"),
            patch("agent_hour_tracker.google_usage.subprocess.run",
                  side_effect=subprocess.CalledProcessError(1, [], stderr=b"private path")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            ret = main(["google-usage", "--provider", "antigravity", "--end", "2026-11-23"])
        self.assertEqual(ret, 1)
        self.assertIn("Tokens: unavailable", stdout.getvalue())
        self.assertNotIn("private path", stderr.getvalue())


def fake_muse(**scan):
    calls = []

    def scan_muse(roots, timezone):
        calls.append((roots, timezone))
        return types.SimpleNamespace(**{"tokens": {}, "hours": {}, "unreadable": 0, "files_scanned": 1, **scan})

    module = types.ModuleType("agent_hour_tracker.muse_usage")
    module.scan_muse = scan_muse
    return patch.dict(sys.modules, {"agent_hour_tracker.muse_usage": module}), calls


class MuseUsageCliTests(unittest.TestCase):
    def run_cli(self, args, **scan):
        modules, calls = fake_muse(**scan)
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            modules,
            TemporaryDirectory() as home,
            patch("agent_hour_tracker.google_usage.Path.home", return_value=Path(home)),
            patch("agent_hour_tracker.google_usage.shutil.which", return_value=None),
            patch("agent_hour_tracker.google_usage.subprocess.run", side_effect=AssertionError),
            patch.dict("os.environ", {"ANTIGRAVITY_DATA_DIR": "/nonexistent-agent-hours-test"}),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            ret = main(["usage", "--start", "2026-11-23", "--end", "2026-11-24", "--timezone", "UTC", *args])
        return ret, stdout.getvalue(), stderr.getvalue(), calls

    def test_muse_only_dispatch_runs_without_ccusage(self):
        with TemporaryDirectory() as folder:
            ret, out, err, calls = self.run_cli(
                ["--provider", "muse", "--format", "csv", "--muse-sessions-dir", folder],
                tokens={_DAY: 500, date(2026, 11, 1): 9}, hours={_DAY: 0.25})
        self.assertEqual((ret, err), (0, ""))
        self.assertEqual(calls, [([Path(folder)], _UTC)])
        self.assertEqual(list(csv.DictReader(io.StringIO(out))), [
            {"date": "2026-11-23", "muse_tokens": "500", "muse_measured_agent_hours": "0.25"},
            {"date": "2026-11-24", "muse_tokens": "0", "muse_measured_agent_hours": ""},
        ])

    def test_all_sources_render_muse_when_ccusage_is_missing_and_mark_partial_tokens(self):
        ret, out, err, calls = self.run_cli(["--provider", "all"], tokens={_DAY: 500})
        self.assertEqual(ret, 1)
        self.assertEqual(calls, [(None, _UTC)])
        self.assertIn("ccusage not found", err)
        self.assertEqual(out.count("Tokens: unavailable"), 1)
        self.assertIn("Tokens: 500 total", out)
        self.assertIn("Combined tokens: unavailable (no token counts for Antigravity)", out)

    def test_unsupported_or_missing_muse_data_is_not_reported_as_zero(self):
        ret, out, err, _ = self.run_cli(["--provider", "muse"], files_scanned=2, unsupported=2)
        self.assertEqual(ret, 1)
        self.assertIn("Tokens: unavailable", out)
        ret, out, err, _ = self.run_cli(["--provider", "muse"], files_scanned=0)
        self.assertEqual(ret, 0)
        self.assertIn("Tokens: unavailable", out)
        self.assertIn("no retained Muse Code session logs", err)

    def test_unreadable_muse_logs_fail_with_unavailable_usage(self):
        ret, out, err, _ = self.run_cli(["--provider", "muse"], files_scanned=0, unreadable=2)
        self.assertEqual(ret, 1)
        self.assertIn("Tokens: unavailable", out)
        self.assertIn("Measured agent hours: unavailable", out)
        self.assertEqual(err, "error: unable to read Muse Code session logs\n")

    def test_explicit_muse_root_must_exist_and_google_usage_rejects_muse(self):
        ret, _, err, calls = self.run_cli(["--muse-sessions-dir", "/nonexistent-agent-hours-test"])
        self.assertEqual((ret, calls), (2, []))
        self.assertIn("does not exist", err)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["google-usage", "--provider", "muse"]), 2)
