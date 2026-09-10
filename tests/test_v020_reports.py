from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_hour_tracker.cli import main
from agent_hour_tracker.metrics import (
    DailyStat,
    ReportMetrics,
    build_leverage,
    monthly_breakdown,
)
from agent_hour_tracker.report import render_monthly
from agent_hour_tracker.share import build_public_share, render_share_json


def report_fixture() -> ReportMetrics:
    days = tuple(
        DailyStat(
            date(2026, 1, 30) + timedelta(days=index),
            2.0 if index == 0 else 1.0 if index == 2 else 0.0,
            4 if index == 0 else 2 if index == 2 else 0,
        )
        for index in range(30)
    )
    return ReportMetrics(
        days=days,
        total_agent_hours=3.0,
        mean_per_calendar_day=0.1,
        mean_per_active_day=1.5,
        median_agent_hours=0.0,
        p95_agent_hours=1.9,
        max_agent_hours=2.0,
        active_days=2,
        zero_days=28,
        days_above_15_hours=0,
        days_above_60_hours=0,
        histogram=OrderedDict(),
        human_initiated_top_level_turns=2,
        mean_human_initiated_turn_seconds=90.0,
        median_human_initiated_turn_seconds=90.0,
    )


def short_report_fixture() -> ReportMetrics:
    days = (
        DailyStat(date(2026, 1, 30), 2.0, 4),
        DailyStat(date(2026, 1, 31), 0.0, 0),
        DailyStat(date(2026, 2, 1), 1.0, 2),
    )
    return ReportMetrics(
        days=days,
        total_agent_hours=3.0,
        mean_per_calendar_day=1.0,
        mean_per_active_day=1.5,
        median_agent_hours=1.0,
        p95_agent_hours=1.9,
        max_agent_hours=2.0,
        active_days=2,
        zero_days=1,
        days_above_15_hours=0,
        days_above_60_hours=0,
        histogram=OrderedDict(),
        human_initiated_top_level_turns=2,
        mean_human_initiated_turn_seconds=90.0,
        median_human_initiated_turn_seconds=90.0,
    )


class MetricsV020Tests(unittest.TestCase):
    def test_weekly_leverage_uses_requested_calendar_day_count(self) -> None:
        leverage = build_leverage(
            total_agent_hours=30.0,
            human_hours_per_week=35.0,
            calendar_day_count=14,
        )

        self.assertEqual(leverage.basis, "estimated-weekly")
        self.assertEqual(leverage.human_hours, 70.0)
        self.assertEqual(leverage.human_hours_per_week, 35.0)
        self.assertEqual(leverage.ratio, 3.0 / 7.0)

    def test_leverage_rejects_missing_or_conflicting_inputs(self) -> None:
        with self.assertRaises(ValueError):
            build_leverage(total_agent_hours=3.0)
        with self.assertRaises(ValueError):
            build_leverage(
                total_agent_hours=3.0,
                human_hours=10.0,
                human_hours_per_week=35.0,
                calendar_day_count=7,
            )
        with self.assertRaises(ValueError):
            build_leverage(total_agent_hours=3.0, human_hours=float("nan"))

    def test_monthly_breakdown_keeps_partial_month_bounds_and_day_count(self) -> None:
        months = monthly_breakdown(short_report_fixture())

        self.assertEqual([month.month for month in months], ["2026-01", "2026-02"])
        self.assertEqual(months[0].start, date(2026, 1, 30))
        self.assertEqual(months[0].end, date(2026, 1, 31))
        self.assertEqual(months[0].calendar_days, 2)
        self.assertEqual(months[0].agent_hours, 2.0)
        self.assertEqual(months[0].mean_per_calendar_day, 1.0)
        self.assertEqual(months[1].calendar_days, 1)
        self.assertEqual(months[1].mean_per_calendar_day, 1.0)

        output = render_monthly(short_report_fixture())
        self.assertIn("Agent-hours/day", output)
        self.assertIn("2026-01", output)
        self.assertIn("1.00", output)


class ShareV020Tests(unittest.TestCase):
    def test_public_share_has_exact_versioned_schema(self) -> None:
        payload = build_public_share(
            report_fixture(),
            tracker_version="0.2.0",
            methodology_version="1",
            start=date(2026, 1, 30),
            end=date(2026, 2, 28),
            timezone=ZoneInfo("Pacific/Honolulu"),
            scope="interactive-only",
            leverage=build_leverage(
                total_agent_hours=3.0,
                human_hours=10.0,
            ),
        )

        self.assertEqual(
            set(payload),
            {
                "schema",
                "schema_version",
                "tracker_version",
                "methodology_version",
                "window",
                "scope",
                "metrics",
                "leverage",
            },
        )
        self.assertEqual(payload["window"]["days"], 30)
        self.assertEqual(payload["window"]["timezone"], "Pacific/Honolulu")
        self.assertEqual(payload["metrics"]["completed_turns"], 6)
        self.assertEqual(payload["leverage"]["basis"], "reported-period")
        self.assertNotIn("days", payload["metrics"])

    def test_share_json_is_deterministic_and_sanitized(self) -> None:
        output = render_share_json(
            report_fixture(),
            tracker_version="0.2.0",
            methodology_version="1",
            start=date(2026, 1, 30),
            end=date(2026, 2, 28),
            timezone=ZoneInfo("UTC"),
        )

        parsed = json.loads(output)
        self.assertEqual(parsed["schema"], "agent-hours-score")
        self.assertEqual(parsed["scope"], "interactive-only")
        self.assertIsNone(parsed["leverage"])
        self.assertEqual(output, render_share_json(
            report_fixture(),
            tracker_version="0.2.0",
            methodology_version="1",
            start=date(2026, 1, 30),
            end=date(2026, 2, 28),
            timezone=ZoneInfo("UTC"),
        ))
        self.assertNotIn("DailyStat", output)
        self.assertNotIn("agent_hour_tracker", output)


class CliV020Tests(unittest.TestCase):
    def test_share_json_flag_emits_public_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.jsonl"
            started = datetime(2026, 6, 1, tzinfo=UTC)
            records = [
                {"type": "session_meta", "source": "cli"},
                {
                    "timestamp": started.isoformat().replace("+00:00", "Z"),
                    "type": "event_msg",
                    "payload": {
                        "type": "task_started",
                        "turn_id": "v020",
                        "started_at": started.timestamp(),
                    },
                },
                {
                    "timestamp": (started + timedelta(seconds=60))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "v020",
                        "duration_ms": 60_000,
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(
                    [
                        "--sessions-dir",
                        str(root),
                        "--timezone",
                        "UTC",
                        "--share",
                        "--format",
                        "json",
                    ]
                )

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schema"], "agent-hours-score")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["window"]["days"], 30)

    def test_monthly_flag_adds_partial_month_section_to_text(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "--sessions-dir",
                    ".",
                    "--timezone",
                    "UTC",
                    "--start",
                    "2026-01-30",
                    "--end",
                    "2026-02-01",
                    "--monthly",
                ]
            )

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertIn("MONTHLY AGENT-HOURS", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
