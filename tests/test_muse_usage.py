import csv
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent_hour_tracker.cli import main
from agent_hour_tracker.muse_usage import scan_muse

_DAY = date(2026, 11, 18)


def _record(kind, timestamp, payload, *, version=1):
    return {
        "schema_version": 1,
        "record_type": "event",
        "recorded_at": timestamp,
        "stream": {"kind": "session", "id": "session"},
        "payload_type": kind,
        "payload_schema_version": version,
        "payload": payload,
    }


def _model(timestamp, run, source, *, tokens=None, duration=None, model="muse-spark", version=1):
    return _record(
        "runtime.session", timestamp,
        {
            "kind": "run", "run_id": run, "source_run_record_id": source,
            "event": {
                "kind": "model_completed",
                "model": model,
                "usage": tokens,
                "duration_ms": duration,
                "private_content": '{"payload_type":"tool_batch.effect.started"}',
            },
        },
        version=version,
    )


def _effect(timestamp, run, effect, kind, *, tool="shell", approval=False):
    payload = {"kind": "tool_batch_effect", "run_id": run, "record": {
        "kind": kind, "effect_id": effect, "tool_name": tool,
        "outcome": {"kind": "completed"},
    }}
    family = "approval_wait" if approval else "tool_batch"
    return _record(f"{family}.effect.{kind}", timestamp, payload)


class MuseUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _log(self, name, records):
        log = self.root / name / "session.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return log

    def _report(self, start=_DAY, end=_DAY, output_format="text"):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(["usage", "--provider", "muse", "--muse-sessions-dir", str(self.root),
                           "--start", start.isoformat(), "--end", end.isoformat(), "--timezone", "UTC",
                           "--format", output_format])
        return status, out.getvalue(), err.getvalue()

    def test_counts_each_model_use_once_and_sums_parallel_agent_intervals(self):
        all_tokens = {"input_tokens": 100, "output_tokens": 20, "cached_tokens": 90,
                      "cache_read_tokens": 90, "reasoning_tokens": 14}
        records = [
            _model("2026-11-18T08:10:00Z", "parent", "step-1", tokens=all_tokens, duration=600_000),
            _effect("2026-11-18T08:05:00Z", "parent", "batch", "started"),
            _effect("2026-11-18T08:20:00Z", "parent", "batch", "terminal"),
            _model("2026-11-18T08:15:00Z", "child", "step-1",
                   tokens={"total_tokens": 40}, duration=600_000),
        ]
        existing = self._log("first", records)
        self._log("second", [])
        shutil.copyfile(existing, self.root / "second" / "session.jsonl")
        # A projection recorded later still references the same original model call.
        self._log("mirror", [
            _model("2026-11-18T08:13:00Z", "parent", "step-1", tokens=all_tokens, duration=600_000),
        ])
        with patch("json.loads", side_effect=AssertionError("Must not decode private session records")):
            result = scan_muse([self.root, existing], ZoneInfo("UTC"))
        self.assertEqual(result.tokens, {_DAY: 160})
        self.assertAlmostEqual(result.hours[_DAY], 30 / 60)
        self.assertEqual((result.files_scanned, result.unreadable, result.unsupported), (3, 0, 0))

    def test_waits_leave_overlapping_tool_and_model_work_intact(self):
        self._log("session", [
            _effect("2026-11-18T08:00:00Z", "parent", "wait", "started", tool="wait_for_agents"),
            _effect("2026-11-18T08:02:00Z", "parent", "approval", "started", approval=True),
            _effect("2026-11-18T08:05:00Z", "parent", "tool", "started"),
            _effect("2026-11-18T08:10:00Z", "parent", "tool", "terminal"),
            _model("2026-11-18T08:17:00Z", "parent", "model", tokens={"total_tokens": 10}, duration=120_000),
            _effect("2026-11-18T08:20:00Z", "parent", "wait", "terminal"),
            _effect("2026-11-18T08:22:00Z", "parent", "approval", "terminal", approval=True),
        ])
        result = scan_muse([self.root], ZoneInfo("UTC"))
        self.assertEqual(result.tokens, {_DAY: 10})
        self.assertAlmostEqual(result.hours[_DAY], 7 / 60)

    def test_excludes_recovery_of_unfinished_effects(self):
        records = [
            _effect("2026-11-18T08:00:00Z", "parent", "old-process", "started"),
            _record("session.opened.observed", "2026-11-18T10:00:00Z", {}),
            _effect("2026-11-18T10:01:00Z", "parent", "old-process", "terminal"),
            _effect("2026-11-18T10:02:00Z", "parent", "new-process", "started"),
            _effect("2026-11-18T10:05:00Z", "parent", "new-process", "terminal"),
        ]
        self._log("session", records)
        result = scan_muse([self.root], ZoneInfo("UTC"))
        self.assertEqual(result.tokens, {})
        self.assertAlmostEqual(result.hours[_DAY], 3 / 60)

    def test_local_days_split_runtime_and_use_end_date_for_tokens(self):
        # November 18 09:30 UTC is November 17 23:30 in Honolulu.
        completion = datetime.fromisoformat("2026-11-18T10:30:00Z")
        completion_us = int(completion.timestamp()) * 1_000_000
        self._log("session", [_model(completion_us, "run", "call", tokens={"total_tokens": 42},
                                     duration=3_600_000)])
        result = scan_muse([self.root], ZoneInfo("Pacific/Honolulu"))
        self.assertEqual(result.tokens, {_DAY: 42})
        self.assertEqual(result.hours, {date(2026, 11, 17): 0.5, _DAY: 0.5})

    def test_model_without_usage_reports_unavailable_tokens_and_keeps_hours(self):
        model = _model("2026-11-18T08:30:00Z", "run", "missing", duration=1_800_000)
        del model["payload"]["event"]["usage"]
        self._log("missing", [model])

        status, out, err = self._report()
        self.assertEqual(status, 1)
        self.assertIn("Tokens: unavailable", out)
        self.assertIn("Measured agent hours: 0.5000", out)
        self.assertIn("no Muse Code tokens read; skipped 1", err)

        day_before = date(2026, 11, 17)
        status, out, err = self._report(day_before, day_before)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("Tokens: 0 total", out)

    def test_missing_tokens_are_scoped_to_the_requested_completion_dates(self):
        day_before = date(2026, 11, 17)
        missing = _model("2026-11-18T08:30:00Z", "run", "missing", duration=1_800_000)
        del missing["payload"]["event"]["usage"]
        self._log("session", [
            _model("2026-11-17T08:30:00Z", "run", "counted", tokens={"total_tokens": 100},
                   duration=1_800_000),
            missing,
        ])
        self._log("copy", [missing])

        status, out, err = self._report()
        self.assertEqual(status, 1)
        self.assertIn("Tokens: unavailable", out)
        self.assertIn("Measured agent hours: 0.5000", out)
        self.assertIn("no Muse Code tokens read; skipped 1", err)

        status, out, err = self._report(day_before, _DAY, "csv")
        self.assertEqual(status, 0)
        self.assertIn("warning: skipped 1", err)
        self.assertEqual(list(csv.DictReader(io.StringIO(out))), [
            {"date": "2026-11-17", "muse_tokens": "100", "muse_measured_agent_hours": "0.5"},
            {"date": "2026-11-18", "muse_tokens": "", "muse_measured_agent_hours": "0.5"},
        ])

        status, out, err = self._report(day_before, day_before)
        self.assertEqual((status, err), (0, ""))
        self.assertIn("Tokens: 100 total", out)

    def test_model_without_usage_warns_when_another_log_has_token_counts(self):
        model = _model("2026-11-18T08:30:00Z", "run", "missing", duration=1_800_000)
        del model["payload"]["event"]["usage"]
        self._log("missing", [model])
        self._log("counted", [
            _model("2026-11-18T09:30:00Z", "run", "counted", tokens={"total_tokens": 42},
                   duration=1_800_000),
        ])

        status, out, err = self._report()
        self.assertEqual(status, 0)
        self.assertIn("Tokens: 42 total", out)
        self.assertIn("Measured agent hours: 1.0000", out)
        self.assertIn("warning: skipped 1", err)
        self.assertIn("Muse Code totals are partial", err)

    def test_keeps_token_records_with_missing_timing_and_skips_unsupported_or_invalid_metadata(self):
        log = self._log("session", [
            _model("2026-11-18T08:10:00Z", "supported", "one", tokens={"total_tokens": 11}),
            _model("2026-11-18T08:10:00Z", "provider", "one", model="other",
                   tokens={"input_tokens": 50, "output_tokens": 50}),
            _model("2026-11-18T08:10:00Z", "version", "one", tokens={"total_tokens": 99}, version=2),
            _model("invalid", "invalid", "one", tokens={"total_tokens": 10}, duration=3_600_000),
        ])
        with log.open("ab") as file:
            file.write(b"invalid structure\n")
        result = scan_muse([self.root], ZoneInfo("UTC"))
        self.assertEqual(result.tokens, {_DAY: 11})
        self.assertEqual(result.hours, {})
        self.assertEqual(result.missing_tokens, {_DAY: 1})
        self.assertEqual((result.unreadable, result.unsupported), (2, 1))
