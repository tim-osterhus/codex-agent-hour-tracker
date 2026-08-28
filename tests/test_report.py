from __future__ import annotations

import unittest
from collections import OrderedDict
from datetime import date, timedelta

from agent_hour_tracker.metrics import DailyStat, ReportMetrics
from agent_hour_tracker.report import render_csv, render_share, render_text


def report_fixture() -> ReportMetrics:
    return ReportMetrics(
        days=(
            DailyStat(date(2026, 6, 1), 3.0, 2),
            DailyStat(date(2026, 6, 2), 0.0, 0),
            DailyStat(date(2026, 6, 3), 1.0, 1),
        ),
        total_agent_hours=4.0,
        mean_per_calendar_day=4.0 / 3.0,
        mean_per_active_day=2.0,
        median_agent_hours=1.0,
        p95_agent_hours=2.8,
        max_agent_hours=3.0,
        active_days=2,
        zero_days=1,
        days_above_15_hours=0,
        days_above_60_hours=0,
        histogram=OrderedDict(
            (
                ("0", 1),
                (">0 to <1", 0),
                ("1 to <5", 2),
                ("5 to <10", 0),
                ("10 to <15", 0),
                ("15 to <30", 0),
                ("30 to <60", 0),
                ("60+", 0),
            )
        ),
    )


def share_report_fixture() -> ReportMetrics:
    active_hours = (370.20 - 31.80) / 23
    days = tuple(
        DailyStat(
            date(2026, 1, 1) + timedelta(days=index),
            31.8 if index == 17 else active_hours if index < 24 else 0.0,
            36 if index < 2 else 35 if index < 24 else 0,
        )
        for index in range(30)
    )
    return ReportMetrics(
        days=tuple(reversed(days)),
        total_agent_hours=370.20,
        mean_per_calendar_day=12.34,
        mean_per_active_day=15.425,
        median_agent_hours=12.34,
        p95_agent_hours=31.8,
        max_agent_hours=31.80,
        active_days=24,
        zero_days=6,
        days_above_15_hours=1,
        days_above_60_hours=0,
        histogram=OrderedDict(),
    )


class ReportTests(unittest.TestCase):
    def test_share_render_is_a_deterministic_sanitized_archive_score_card(self) -> None:
        output = render_share(share_report_fixture(), "0.1.0", "1")

        self.assertEqual(
            output,
            "CODEX AGENT-HOUR SCORE\n"
            "\n"
            "30 complete calendar days | 2026-01-01 to 2026-01-30\n"
            "-----------------------------------------------------\n"
            "Agent-hours/day: 12.34\n"
            "Total agent-hours: 370.20\n"
            "Peak day: 31.80\n"
            "Completed turns: 842\n"
            "Active days: 24/30\n"
            "\n"
            "Archive Score | methodology v1 | tracker v0.1.0\n"
            "Calculated locally. No conversation content uploaded.\n",
        )
        self.assertNotIn("UTC", output)
        self.assertNotIn("source", output.lower())
        self.assertNotIn("/tmp", output)
        self.assertNotIn("fingerprint", output.lower())
        self.assertNotIn("DailyStat", output)

    def test_text_contains_summary_daily_rows_and_all_buckets(self) -> None:
        output = render_text(report_fixture())

        self.assertIn("AGENT-HOUR SUMMARY", output)
        self.assertIn("Calendar days:               3", output)
        self.assertIn("Total agent-hours:        4.00", output)
        self.assertIn("Mean / calendar day:      1.33 h", output)
        self.assertIn("Mean / active day:", output)
        self.assertIn("Median daily agent-hours:", output)
        self.assertIn("P95 daily agent-hours:", output)
        self.assertIn("Maximum daily agent-hours:", output)
        self.assertIn("Active days:", output)
        self.assertIn("Zero days:", output)
        self.assertIn("Days above 15 hours:", output)
        self.assertIn("Days above 60 hours:", output)
        self.assertIn("DAILY AGENT-HOURS", output)
        self.assertIn("2026-06-01                  3.00          2", output)
        self.assertIn("2026-06-02                  0.00          0", output)
        self.assertIn("2026-06-03                  1.00          1", output)
        self.assertIn("DAILY DISTRIBUTION", output)
        for label, count in report_fixture().histogram.items():
            self.assertIn(f"{label:<28}{count}", output)

    def test_csv_has_stable_header_and_six_decimal_hours(self) -> None:
        self.assertEqual(
            render_csv(report_fixture()),
            "date,agent_hours,completed_turns\n"
            "2026-06-01,3.000000,2\n"
            "2026-06-02,0.000000,0\n"
            "2026-06-03,1.000000,1\n",
        )


if __name__ == "__main__":
    unittest.main()
