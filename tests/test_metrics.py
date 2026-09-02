from __future__ import annotations

import unittest
from collections import OrderedDict
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from agent_hour_tracker.metrics import DailyStat, build_report_metrics
from agent_hour_tracker.scanner import CompletedTurn


def timestamp(year: int, month: int, day: int, hour: int = 0) -> float:
    return datetime(year, month, day, hour, tzinfo=UTC).timestamp()


class MetricsTests(unittest.TestCase):
    def test_inclusive_zero_days_and_calendar_mean(self) -> None:
        turns = [
            CompletedTurn(timestamp(2026, 6, 1, 12), 7_200),
            CompletedTurn(timestamp(2026, 6, 1, 13), 3_600),
            CompletedTurn(timestamp(2026, 6, 3, 12), 3_600),
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 1), date(2026, 6, 3), ZoneInfo("UTC")
        )

        self.assertEqual(
            report.days,
            (
                DailyStat(date(2026, 6, 1), 3.0, 2),
                DailyStat(date(2026, 6, 2), 0.0, 0),
                DailyStat(date(2026, 6, 3), 1.0, 1),
            ),
        )
        self.assertAlmostEqual(report.total_agent_hours, 4.0)
        self.assertAlmostEqual(report.mean_per_calendar_day, 4.0 / 3.0)
        self.assertEqual(report.active_days, 2)
        self.assertEqual(report.zero_days, 1)

    def test_active_mean_uses_only_days_with_activity(self) -> None:
        turns = [
            CompletedTurn(timestamp(2026, 6, 1), 3_600),
            CompletedTurn(timestamp(2026, 6, 3), 10_800),
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 1), date(2026, 6, 4), ZoneInfo("UTC")
        )

        self.assertAlmostEqual(report.mean_per_active_day, 2.0)

    def test_attributes_turn_to_local_start_date(self) -> None:
        turn = CompletedTurn(timestamp(2026, 6, 2, 5), 7_200)

        report = build_report_metrics(
            [turn], date(2026, 6, 1), date(2026, 6, 1), ZoneInfo("Pacific/Honolulu")
        )

        self.assertEqual(report.days[0].date, date(2026, 6, 1))
        self.assertEqual(report.days[0].agent_hours, 2.0)

    def test_root_statistics_use_root_turns_without_changing_daily_metrics(self) -> None:
        all_turns = [
            CompletedTurn(timestamp(2026, 6, 1), 10),
            CompletedTurn(timestamp(2026, 6, 2), 20),
            CompletedTurn(timestamp(2026, 6, 3), 30),
        ]
        root_turns = [
            CompletedTurn(timestamp(2026, 6, 1), 60),
            CompletedTurn(timestamp(2026, 6, 2), 120),
            CompletedTurn(timestamp(2026, 6, 3), 180),
        ]

        report = build_report_metrics(
            all_turns,
            date(2026, 6, 1),
            date(2026, 6, 3),
            ZoneInfo("UTC"),
            root_turns=root_turns,
        )

        self.assertEqual(report.days[0].completed_turns, 1)
        self.assertAlmostEqual(report.total_agent_hours, 60 / 3600)
        self.assertEqual(report.human_initiated_top_level_turns, 3)
        self.assertAlmostEqual(report.mean_human_initiated_turn_seconds, 120)
        self.assertAlmostEqual(report.median_human_initiated_turn_seconds, 120)

    def test_root_statistics_use_even_sample_median_and_include_zero_duration(self) -> None:
        root_turns = [
            CompletedTurn(timestamp(2026, 6, 1), 0),
            CompletedTurn(timestamp(2026, 6, 2), 120),
            CompletedTurn(timestamp(2026, 6, 3), 240),
            CompletedTurn(timestamp(2026, 6, 4), 360),
        ]

        report = build_report_metrics(
            [],
            date(2026, 6, 1),
            date(2026, 6, 4),
            ZoneInfo("UTC"),
            root_turns=root_turns,
        )

        self.assertEqual(report.human_initiated_top_level_turns, 4)
        self.assertAlmostEqual(report.mean_human_initiated_turn_seconds, 180)
        self.assertAlmostEqual(report.median_human_initiated_turn_seconds, 180)
        self.assertEqual(report.total_agent_hours, 0.0)

    def test_root_statistics_filter_by_local_start_date(self) -> None:
        root_turns = [
            CompletedTurn(timestamp(2026, 6, 1, 12), 60),
            CompletedTurn(timestamp(2026, 6, 2, 12), 180),
        ]

        report = build_report_metrics(
            [],
            date(2026, 6, 1),
            date(2026, 6, 1),
            ZoneInfo("Pacific/Honolulu"),
            root_turns=root_turns,
        )

        self.assertEqual(report.human_initiated_top_level_turns, 1)
        self.assertEqual(report.mean_human_initiated_turn_seconds, 60)
        self.assertEqual(report.median_human_initiated_turn_seconds, 60)

    def test_no_root_turns_have_zero_root_statistics(self) -> None:
        report = build_report_metrics(
            [],
            date(2026, 6, 1),
            date(2026, 6, 1),
            ZoneInfo("UTC"),
            root_turns=[],
        )

        self.assertEqual(report.human_initiated_top_level_turns, 0)
        self.assertEqual(report.mean_human_initiated_turn_seconds, 0.0)
        self.assertEqual(report.median_human_initiated_turn_seconds, 0.0)

    def test_omitted_root_turns_are_not_treated_as_human_initiated(self) -> None:
        report = build_report_metrics(
            [CompletedTurn(timestamp(2026, 6, 1), 60)],
            date(2026, 6, 1),
            date(2026, 6, 1),
            ZoneInfo("UTC"),
        )

        self.assertEqual(report.human_initiated_top_level_turns, 0)
        self.assertEqual(report.mean_human_initiated_turn_seconds, 0.0)
        self.assertEqual(report.median_human_initiated_turn_seconds, 0.0)

    def test_counts_overlapping_turns_independently(self) -> None:
        turns = [
            CompletedTurn(timestamp(2026, 6, 2, 12), 7_200),
            CompletedTurn(timestamp(2026, 6, 2, 12), 7_200),
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 2), date(2026, 6, 2), ZoneInfo("Pacific/Honolulu")
        )

        self.assertEqual(report.days[0].agent_hours, 4.0)
        self.assertEqual(report.days[0].completed_turns, 2)

    def test_ignores_turns_started_outside_range(self) -> None:
        turns = [
            CompletedTurn(timestamp(2026, 5, 31, 23), 3_600),
            CompletedTurn(timestamp(2026, 6, 1), 1_800),
            CompletedTurn(timestamp(2026, 6, 3), 7_200),
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 1), date(2026, 6, 2), ZoneInfo("UTC")
        )

        self.assertEqual([day.agent_hours for day in report.days], [0.5, 0.0])
        self.assertEqual(report.total_agent_hours, 0.5)

    def test_all_zero_range_has_zero_summaries_and_histogram(self) -> None:
        report = build_report_metrics(
            [], date(2026, 6, 1), date(2026, 6, 3), ZoneInfo("UTC")
        )

        self.assertEqual(report.total_agent_hours, 0.0)
        self.assertEqual(report.mean_per_calendar_day, 0.0)
        self.assertEqual(report.mean_per_active_day, 0.0)
        self.assertEqual(report.median_agent_hours, 0.0)
        self.assertEqual(report.p95_agent_hours, 0.0)
        self.assertEqual(report.max_agent_hours, 0.0)
        self.assertEqual(report.active_days, 0)
        self.assertEqual(report.zero_days, 3)
        self.assertEqual(report.histogram["0"], 3)

    def test_one_day_percentiles_equal_that_day(self) -> None:
        report = build_report_metrics(
            [CompletedTurn(timestamp(2026, 6, 1), 9_000)],
            date(2026, 6, 1),
            date(2026, 6, 1),
            ZoneInfo("UTC"),
        )

        self.assertEqual(report.median_agent_hours, 2.5)
        self.assertEqual(report.p95_agent_hours, 2.5)

    def test_p95_uses_linear_interpolation(self) -> None:
        turns = [
            CompletedTurn(timestamp(2026, 6, 1), 0),
            CompletedTurn(timestamp(2026, 6, 2), 36_000),
            CompletedTurn(timestamp(2026, 6, 3), 72_000),
            CompletedTurn(timestamp(2026, 6, 4), 108_000),
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 1), date(2026, 6, 4), ZoneInfo("UTC")
        )

        self.assertAlmostEqual(report.p95_agent_hours, 28.5)

    def test_histogram_boundaries_are_exact_and_ordered(self) -> None:
        hours = (0, 0.5, 1, 5, 10, 15, 30, 60)
        turns = [
            CompletedTurn(timestamp(2026, 6, day), value * 3_600)
            for day, value in enumerate(hours, 1)
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 1), date(2026, 6, 8), ZoneInfo("UTC")
        )

        expected = OrderedDict(
            (
                ("0", 1),
                (">0 to <1", 1),
                ("1 to <5", 1),
                ("5 to <10", 1),
                ("10 to <15", 1),
                ("15 to <30", 1),
                ("30 to <60", 1),
                ("60+", 1),
            )
        )
        self.assertEqual(report.histogram, expected)

    def test_thresholds_are_strictly_greater_than(self) -> None:
        turns = [
            CompletedTurn(timestamp(2026, 6, 1), 15 * 3_600),
            CompletedTurn(timestamp(2026, 6, 2), 15 * 3_600 + 1),
            CompletedTurn(timestamp(2026, 6, 3), 60 * 3_600),
            CompletedTurn(timestamp(2026, 6, 4), 60 * 3_600 + 1),
        ]

        report = build_report_metrics(
            turns, date(2026, 6, 1), date(2026, 6, 4), ZoneInfo("UTC")
        )

        self.assertEqual(report.days_above_15_hours, 3)
        self.assertEqual(report.days_above_60_hours, 1)

    def test_rejects_invalid_date_range(self) -> None:
        with self.assertRaises(ValueError):
            build_report_metrics(
                [], date(2026, 6, 2), date(2026, 6, 1), ZoneInfo("UTC")
            )

    def test_handles_one_day_range_at_date_max(self) -> None:
        report = build_report_metrics(
            [], date.max, date.max, ZoneInfo("UTC")
        )

        self.assertEqual(report.days, (DailyStat(date.max, 0.0, 0),))


if __name__ == "__main__":
    unittest.main()
