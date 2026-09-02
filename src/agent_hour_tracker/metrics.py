"""Aggregate completed turn durations into daily report metrics."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
from statistics import median
from zoneinfo import ZoneInfo

from .scanner import CompletedTurn

__all__ = ["DailyStat", "ReportMetrics", "build_report_metrics"]

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
