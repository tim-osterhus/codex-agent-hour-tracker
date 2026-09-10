"""Aggregate completed turn durations into daily report metrics."""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
from numbers import Real
from statistics import median
from typing import Literal
from zoneinfo import ZoneInfo

from .scanner import CompletedTurn

__all__ = [
    "DailyStat",
    "LeverageMetrics",
    "MonthlyStat",
    "ReportMetrics",
    "build_leverage",
    "build_report_metrics",
    "monthly_breakdown",
]

HISTOGRAM_LABELS = (
    "0",
    ">0 to <1",
    "1 to <5",
    "5 to <10",
    "10 to <15",
    "15 to <30",
    "30 to <60",
    "60+",
)


@dataclass(frozen=True, slots=True)
class DailyStat:
    """Agent-hours and completed-turn count for one calendar date."""

    date: date
    agent_hours: float
    completed_turns: int


@dataclass(frozen=True, slots=True)
class ReportMetrics:
    """Daily rows and summary statistics for an inclusive report range."""

    days: tuple[DailyStat, ...]
    total_agent_hours: float
    mean_per_calendar_day: float
    mean_per_active_day: float
    median_agent_hours: float
    p95_agent_hours: float
    max_agent_hours: float
    active_days: int
    zero_days: int
    days_above_15_hours: int
    days_above_60_hours: int
    histogram: OrderedDict[str, int]
    human_initiated_top_level_turns: int = 0
    mean_human_initiated_turn_seconds: float = 0.0
    median_human_initiated_turn_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class LeverageMetrics:
    """Agent-hours relative to a reported or estimated human-hour basis."""

    ratio: float
    human_hours: float
    basis: Literal["reported-period", "estimated-weekly"]
    human_hours_per_week: float | None = None


@dataclass(frozen=True, slots=True)
class MonthlyStat:
    """Aggregate metrics for the requested portion of one calendar month."""

    month: str
    start: date
    end: date
    calendar_days: int
    agent_hours: float
    completed_turns: int
    active_days: int

    @property
    def days(self) -> int:
        """Compatibility alias for the number of rows in this month."""

        return self.calendar_days

    @property
    def mean_per_calendar_day(self) -> float:
        """Return cumulative agent-hours divided by requested days."""

        return self.agent_hours / self.calendar_days if self.calendar_days else 0.0


def build_leverage(
    total_agent_hours: Real,
    *,
    human_hours: Real | None = None,
    human_hours_per_week: Real | None = None,
    calendar_day_count: Real | None = None,
) -> LeverageMetrics:
    """Calculate agent leverage from one explicit human-hours basis.

    ``human_hours`` is the total human-hour denominator for the requested
    report period. ``human_hours_per_week`` derives that denominator from
    the inclusive calendar-day count and is labeled as an estimate.

    Raises:
        ValueError: If the basis is missing, conflicting, non-finite, or not
            strictly positive, or if the resulting ratio is not finite.
    """

    if (human_hours is None) == (human_hours_per_week is None):
        raise ValueError(
            "provide exactly one of human_hours or human_hours_per_week"
        )
    agent_hours = _finite_number(total_agent_hours, "total agent hours")
    if agent_hours < 0.0:
        raise ValueError("total agent hours must be nonnegative")

    if human_hours is not None:
        denominator = _positive_number(human_hours, "human hours")
        basis: Literal["reported-period", "estimated-weekly"] = (
            "reported-period"
        )
        weekly_value = None
    else:
        weekly_value = _positive_number(
            human_hours_per_week, "human hours per week"
        )
        if calendar_day_count is None:
            raise ValueError(
                "calendar_day_count is required for weekly human hours"
            )
        day_count = _positive_number(calendar_day_count, "calendar day count")
        denominator = weekly_value * day_count / 7.0
        if not math.isfinite(denominator) or denominator <= 0.0:
            raise ValueError("estimated human hours must be finite and positive")
        basis = "estimated-weekly"

    ratio = agent_hours / denominator
    if not math.isfinite(ratio):
        raise ValueError("leverage ratio must be finite")
    return LeverageMetrics(
        ratio=ratio,
        human_hours=denominator,
        basis=basis,
        human_hours_per_week=weekly_value,
    )


def monthly_breakdown(report: ReportMetrics) -> tuple[MonthlyStat, ...]:
    """Group daily rows by month while retaining requested partial bounds."""

    grouped: dict[str, list[DailyStat]] = {}
    for day in sorted(report.days, key=lambda item: item.date):
        grouped.setdefault(day.date.strftime("%Y-%m"), []).append(day)
    return tuple(
        MonthlyStat(
            month=month,
            start=rows[0].date,
            end=rows[-1].date,
            calendar_days=len(rows),
            agent_hours=sum(row.agent_hours for row in rows),
            completed_turns=sum(row.completed_turns for row in rows),
            active_days=sum(row.agent_hours > 0.0 for row in rows),
        )
        for month, rows in grouped.items()
    )


def _finite_number(value: Real, label: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(f"{label} must be finite")  # noqa: TRY004 - one validation error type
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be finite") from None
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def _positive_number(value: Real, label: str) -> float:
    converted = _finite_number(value, label)
    if converted <= 0.0:
        raise ValueError(f"{label} must be positive")
    return converted


def build_report_metrics(
    turns: list[CompletedTurn],
    start: date,
    end: date,
    timezone: ZoneInfo,
    root_turns: list[CompletedTurn] | None = None,
) -> ReportMetrics:
    """Build daily and distribution metrics for an inclusive date range.

    Each turn's full duration is assigned to the local date of its start,
    independently of any other turn. Turns whose local start date falls
    outside the requested range are ignored.

    Raises:
        ValueError: If ``end`` precedes ``start``.
    """

    if end < start:
        raise ValueError("end date must not precede start date")

    if root_turns is None:
        root_turns = []

    hours_by_date: dict[date, float] = {}
    turns_by_date: dict[date, int] = {}
    current = start
    while current <= end:
        hours_by_date[current] = 0.0
        turns_by_date[current] = 0
        if current == end:
            break
        current += timedelta(days=1)

    for turn in turns:
        try:
            local_date = datetime.fromtimestamp(turn.started_at, timezone).date()
        except (OSError, OverflowError, ValueError):
            continue
        if local_date not in hours_by_date:
            continue
        hours_by_date[local_date] += turn.duration_seconds / 3_600.0
        turns_by_date[local_date] += 1

    days = tuple(
        DailyStat(day, hours_by_date[day], turns_by_date[day])
        for day in hours_by_date
    )
    values = [day.agent_hours for day in days]
    root_durations = [
        turn.duration_seconds
        for turn in root_turns
        if _local_start_date(turn.started_at, timezone, start, end) is not None
    ]
    root_count = len(root_durations)
    total_agent_hours = sum(values)
    active_days = sum(value > 0.0 for value in values)
    zero_days = sum(value == 0.0 for value in values)
    day_count = len(values)

    return ReportMetrics(
        days=days,
        total_agent_hours=total_agent_hours,
        mean_per_calendar_day=total_agent_hours / day_count,
        mean_per_active_day=(
            total_agent_hours / active_days if active_days else 0.0
        ),
        median_agent_hours=float(median(values)),
        p95_agent_hours=_percentile_95(values),
        max_agent_hours=max(values),
        active_days=active_days,
        zero_days=zero_days,
        days_above_15_hours=sum(value > 15.0 for value in values),
        days_above_60_hours=sum(value > 60.0 for value in values),
        histogram=_build_histogram(values),
        human_initiated_top_level_turns=root_count,
        mean_human_initiated_turn_seconds=(
            sum(root_durations) / root_count if root_count else 0.0
        ),
        median_human_initiated_turn_seconds=(
            float(median(root_durations)) if root_durations else 0.0
        ),
    )


def _local_start_date(
    started_at: float,
    timezone: ZoneInfo,
    start: date,
    end: date,
) -> date | None:
    try:
        local_date = datetime.fromtimestamp(started_at, timezone).date()
    except (OSError, OverflowError, ValueError):
        return None
    return local_date if start <= local_date <= end else None


def _percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * 0.95
    lower_index = int(position)
    upper_index = ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] + weight * (
        ordered[upper_index] - ordered[lower_index]
    )


def _build_histogram(values: list[float]) -> OrderedDict[str, int]:
    histogram: OrderedDict[str, int] = OrderedDict(
        (label, 0) for label in HISTOGRAM_LABELS
    )
    for value in values:
        if value == 0.0:
            label = "0"
        elif value < 1.0:
            label = ">0 to <1"
        elif value < 5.0:
            label = "1 to <5"
        elif value < 10.0:
            label = "5 to <10"
        elif value < 15.0:
            label = "10 to <15"
        elif value < 30.0:
            label = "15 to <30"
        elif value < 60.0:
            label = "30 to <60"
        else:
            label = "60+"
        histogram[label] += 1
    return histogram
