"""Deterministic text and CSV renderers for agent-hour metrics."""

from __future__ import annotations

import csv
import io
from datetime import date, tzinfo

from .metrics import DailyStat, LeverageMetrics, ReportMetrics, monthly_breakdown

__all__ = ["render_csv", "render_monthly", "render_share", "render_text"]


def render_text(
    report: ReportMetrics,
    *,
    start: date | None = None,
    end: date | None = None,
    timezone: tzinfo | str | None = None,
    scope: str | None = None,
    methodology_version: str | None = None,
    leverage: LeverageMetrics | None = None,
    monthly: bool = False,
) -> str:
    """Render the summary, daily rows, and histogram as plain text."""

    days = sorted(report.days, key=lambda day: day.date)
    lines = ["AGENT-HOUR SUMMARY"]
    lines.extend(
        _context_lines(
            days,
            start=start,
            end=end,
            timezone=timezone,
            scope=scope,
            methodology_version=methodology_version,
        )
    )
    if len(lines) > 1:
        lines.append("")
    lines.extend(
        [
        f"Calendar days:{len(days):>16}",
        f"Total agent-hours:{report.total_agent_hours:>12.2f}",
        f"Human-initiated top-level turns:{report.human_initiated_top_level_turns:>8}",
        (
            "Mean human-initiated top-level duration: "
            f"{report.mean_human_initiated_turn_seconds / 60.0:.2f} min"
        ),
        (
            "Median human-initiated top-level duration: "
            f"{report.median_human_initiated_turn_seconds / 60.0:.2f} min"
        ),
        f"Mean / calendar day:{report.mean_per_calendar_day:>10.2f} h",
        f"Mean / active day:{report.mean_per_active_day:>13.2f} h",
        f"Median daily agent-hours:{report.median_agent_hours:>5.2f} h",
        f"P95 daily agent-hours:{report.p95_agent_hours:>8.2f} h",
        f"Maximum daily agent-hours:{report.max_agent_hours:>5.2f} h",
        f"Active days:{report.active_days:>21}",
        f"Zero days:{report.zero_days:>23}",
        f"Days above 15 hours:{report.days_above_15_hours:>12}",
        f"Days above 60 hours:{report.days_above_60_hours:>12}",
        "",
        "DAILY AGENT-HOURS",
        f"{'Date':<28}{'Agent-hours':>5}{'Completed turns':>18}",
        ]
    )
    lines.extend(
        f"{day.date.isoformat():<28}{day.agent_hours:.2f}{day.completed_turns:>11}"
        for day in days
    )
    lines.extend(("", "DAILY DISTRIBUTION"))
    lines.extend(f"{label:<28}{count}" for label, count in report.histogram.items())
    if leverage is not None:
        lines.extend(("", *_render_leverage_lines(leverage, len(days))))
    if monthly:
        lines.extend(("", render_monthly(report).rstrip("\n")))
    return "\n".join(lines) + "\n"


def render_monthly(report: ReportMetrics) -> str:
    """Render monthly totals and requested-day averages.

    A partial month uses only the rows covered by the requested report range,
    including zero-use calendar days in that range.
    """

    lines = [
        "MONTHLY AGENT-HOURS",
        f"{'Month':<10}{'Requested bounds':<31}{'Calendar days':>15}{'Agent-hours':>15}{'Agent-hours/day':>18}{'Completed turns':>18}{'Active days':>13}",
    ]
    lines.extend(
        (
            f"{month.month:<10}"
            f"{month.start.isoformat()} to {month.end.isoformat():<17}"
            f"{month.calendar_days:>15}"
            f"{month.agent_hours:>15.2f}"
            f"{month.mean_per_calendar_day:>18.2f}"
            f"{month.completed_turns:>18}"
            f"{month.active_days:>13}"
        )
        for month in monthly_breakdown(report)
    )
    return "\n".join(lines) + "\n"


def render_share(
    report: ReportMetrics,
    tracker_version: str,
    methodology_version: str,
    *,
    start: date | None = None,
    end: date | None = None,
    timezone: tzinfo | str | None = None,
    scope: str | None = None,
    leverage: LeverageMetrics | None = None,
) -> str:
    """Render a deterministic, conversation-free Archive Score card."""

    days = sorted(report.days, key=lambda day: day.date)
    if not days:
        raise ValueError("share report requires at least one calendar day")
    day_count = len(days)
    completed_turns = sum(day.completed_turns for day in days)
    lines = [
        "CODEX AGENT-HOUR SCORE",
        "",
        (
            f"{day_count} complete calendar days | "
            f"{days[0].date.isoformat()} to {days[-1].date.isoformat()}"
        ),
        "-" * 53,
        f"Agent-hours/day: {report.mean_per_calendar_day:.2f}",
        f"Total agent-hours: {report.total_agent_hours:.2f}",
        f"Peak day: {report.max_agent_hours:.2f}",
        f"Completed turns: {completed_turns}",
        f"Active days: {report.active_days}/{day_count}",
    ]
    context = _context_lines(
        days,
        start=start,
        end=end,
        timezone=timezone,
        scope=scope,
        methodology_version=methodology_version,
    )
    if context:
        lines.extend(("", *context))
    lines.extend(
        (
            "",
            (
                f"Agent-Hour Score | methodology v{methodology_version} | "
                f"tracker v{tracker_version}"
            ),
        )
    )
    if leverage is not None:
        lines.extend(("", *_render_leverage_lines(leverage, day_count)))
    lines.append("Calculated locally. No conversation content uploaded.")
    return "\n".join(lines) + "\n"


def render_csv(report: ReportMetrics) -> str:
    """Render one CSV row per calendar date with stable numeric precision."""

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("date", "agent_hours", "completed_turns"))
    for day in sorted(report.days, key=lambda day: day.date):
        writer.writerow(
            (day.date.isoformat(), f"{day.agent_hours:.6f}", day.completed_turns)
        )
    return buffer.getvalue()


def _context_lines(
    days: list[DailyStat],
    *,
    start: date | None,
    end: date | None,
    timezone: tzinfo | str | None,
    scope: str | None,
    methodology_version: str | None,
) -> list[str]:
    if not days:
        return []
    resolved_start = days[0].date if start is None else start
    resolved_end = days[-1].date if end is None else end
    lines: list[str] = []
    if scope is not None:
        lines.append(f"Scope: {scope}")
    if timezone is not None:
        timezone_name = (
            timezone if isinstance(timezone, str) else getattr(timezone, "key", str(timezone))
        )
        lines.append(f"Timezone: {timezone_name}")
    if start is not None or end is not None:
        lines.append(
            f"Window: {resolved_start.isoformat()} to {resolved_end.isoformat()} "
            f"({len(days)} calendar days)"
        )
    if methodology_version is not None and (scope is not None or timezone is not None):
        lines.append(f"Methodology: v{methodology_version}")
    return lines


def _render_leverage_lines(leverage: LeverageMetrics, day_count: int) -> tuple[str, ...]:
    lines = (f"Agent leverage: {leverage.ratio:.2f}x",)
    if leverage.basis == "estimated-weekly":
        weekly = leverage.human_hours_per_week
        lines += (
            (
                "Human-hours basis: estimated weekly "
                f"({weekly:.2f} h/week over {day_count} calendar days = "
                f"{leverage.human_hours:.2f} h)"
            ),
        )
    else:
        lines += (f"Human-hours basis: reported period ({leverage.human_hours:.2f} h)",)
    return lines
