"""Deterministic text and CSV renderers for agent-hour metrics."""

from __future__ import annotations

import csv
import io

from .metrics import ReportMetrics

__all__ = ["render_csv", "render_share", "render_text"]


def render_text(report: ReportMetrics) -> str:
    """Render the summary, daily rows, and histogram as plain text."""

    days = sorted(report.days, key=lambda day: day.date)
    lines = [
        "AGENT-HOUR SUMMARY",
        f"Calendar days:{len(days):>16}",
        f"Total agent-hours:{report.total_agent_hours:>12.2f}",
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
    lines.extend(
        f"{day.date.isoformat():<28}{day.agent_hours:.2f}{day.completed_turns:>11}"
        for day in days
    )
    lines.extend(("", "DAILY DISTRIBUTION"))
    lines.extend(f"{label:<28}{count}" for label, count in report.histogram.items())
    return "\n".join(lines) + "\n"


def render_share(
    report: ReportMetrics, tracker_version: str, methodology_version: str
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
        f"{day_count} complete calendar days | "
        f"{days[0].date.isoformat()} to {days[-1].date.isoformat()}",
        "-" * 53,
        f"Agent-hours/day: {report.mean_per_calendar_day:.2f}",
        f"Total agent-hours: {report.total_agent_hours:.2f}",
        f"Peak day: {report.max_agent_hours:.2f}",
        f"Completed turns: {completed_turns}",
        f"Active days: {report.active_days}/{day_count}",
        "",
        f"Archive Score | methodology v{methodology_version} | "
        f"tracker v{tracker_version}",
        "Calculated locally. No conversation content uploaded.",
    ]
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
