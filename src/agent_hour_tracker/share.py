"""Sanitized, versioned payloads intended for public Archive Score sharing."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date, timedelta, tzinfo
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .metrics import LeverageMetrics, ReportMetrics

__all__ = [
    "build_public_share",
    "render_share_json",
    "validate_public_share",
]

_SCHEMA = "agent-hours-score"
_SCHEMA_VERSION = 1
_SCOPES = frozenset(("interactive-only", "including-exec"))
_LEVERAGE_BASES = frozenset(("reported-period", "estimated-weekly"))
_TOP_LEVEL_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "tracker_version",
        "methodology_version",
        "window",
        "scope",
        "metrics",
        "leverage",
    )
)
_WINDOW_KEYS = frozenset(("start", "end", "days", "timezone"))
_METRIC_KEYS = frozenset(
    (
        "agent_hours_per_day",
        "total_agent_hours",
        "peak_day_agent_hours",
        "completed_turns",
        "active_days",
    )
)
_LEVERAGE_KEYS = frozenset(
    ("ratio", "human_hours", "basis", "human_hours_per_week")
)
_VERSION_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")


def build_public_share(
    report: ReportMetrics,
    tracker_version: str,
    methodology_version: str,
    *,
    start: date | None = None,
    end: date | None = None,
    timezone: tzinfo | str = "UTC",
    scope: str = "interactive-only",
    leverage: LeverageMetrics | None = None,
) -> dict[str, Any]:
    """Build the exact public score schema from aggregate report metrics.

    The returned mapping intentionally contains no daily rows, source labels,
    paths, diagnostics, or turn identifiers.  Dates default to the first and
    last rows in ``report`` for direct library use; the CLI supplies the
    canonical or explicitly requested bounds.

    Raises:
        ValueError: If the report has no rows, bounds do not describe those
            rows, the scope is unsupported, or a value is not finite.
    """

    days = tuple(sorted(report.days, key=lambda item: item.date))
    if not days:
        raise ValueError("public share requires at least one calendar day")
    resolved_start = days[0].date if start is None else _date_value(start, "start")
    resolved_end = days[-1].date if end is None else _date_value(end, "end")
    if resolved_end < resolved_start:
        raise ValueError("share end date must not precede start date")
    day_count = (resolved_end - resolved_start).days + 1
    if day_count != 30:
        raise ValueError("public share window must contain exactly 30 days")
    if tuple(day.date for day in days) != tuple(
        resolved_start + timedelta(days=index) for index in range(day_count)
    ):
        raise ValueError("share bounds must match contiguous report days")
    if not isinstance(scope, str) or scope not in _SCOPES:
        raise ValueError("unsupported share scope")
    if not isinstance(tracker_version, str) or not _VERSION_PATTERN.fullmatch(
        tracker_version
    ):
        raise ValueError("tracker version must be a semantic version")
    if methodology_version != "1":
        raise ValueError("unsupported methodology version")

    total_agent_hours = _nonnegative(report.total_agent_hours, "total agent hours")
    agent_hours_per_day = _nonnegative(
        report.mean_per_calendar_day, "agent hours per day"
    )
    peak_day_agent_hours = _nonnegative(
        report.max_agent_hours, "peak day agent hours"
    )
    completed_turns = _integer(
        sum(day.completed_turns for day in days), "completed turns"
    )
    active_days = _integer(report.active_days, "active days")
    if active_days > day_count or active_days > completed_turns:
        raise ValueError("active days cannot exceed calendar days")
    if (
        active_days == 0 and total_agent_hours > 0.0
    ) or peak_day_agent_hours * active_days + 0.000031 < total_agent_hours:
        raise ValueError("public share metrics are inconsistent")
    if abs(agent_hours_per_day * day_count - total_agent_hours) > 0.000031:
        raise ValueError("public share daily mean is inconsistent")
    if peak_day_agent_hours > total_agent_hours + 0.000001:
        raise ValueError("public share peak exceeds total")
    payload = {
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "tracker_version": tracker_version,
        "methodology_version": methodology_version,
        "window": {
            "start": resolved_start.isoformat(),
            "end": resolved_end.isoformat(),
            "days": day_count,
            "timezone": _timezone_name(timezone),
        },
        "scope": scope,
        "metrics": {
            "agent_hours_per_day": _rounded(agent_hours_per_day),
            "total_agent_hours": _rounded(total_agent_hours),
            "peak_day_agent_hours": _rounded(peak_day_agent_hours),
            "completed_turns": completed_turns,
            "active_days": active_days,
        },
        "leverage": _leverage_payload(leverage),
    }
    validate_public_share(payload)
    return payload


def render_share_json(
    report: ReportMetrics,
    tracker_version: str,
    methodology_version: str,
    *,
    start: date | None = None,
    end: date | None = None,
    timezone: tzinfo | str = "UTC",
    scope: str = "interactive-only",
    leverage: LeverageMetrics | None = None,
) -> str:
    """Serialize a public score using stable, finite JSON output."""

    payload = build_public_share(
        report,
        tracker_version,
        methodology_version,
        start=start,
        end=end,
        timezone=timezone,
        scope=scope,
        leverage=leverage,
    )
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        indent=2,
        separators=(",", ": "),
    ) + "\n"


def validate_public_share(payload: Mapping[str, Any]) -> None:
    """Validate the public schema strictly, rejecting private/extra fields."""

    if not isinstance(payload, Mapping) or set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("invalid public share fields")
    if payload["schema"] != _SCHEMA or payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("unsupported public share schema")
    if not isinstance(payload["tracker_version"], str) or not payload[
        "tracker_version"
    ]:
        raise ValueError("invalid public share version")
    if not _VERSION_PATTERN.fullmatch(payload["tracker_version"]):
        raise ValueError("invalid public share tracker version")
    if payload["methodology_version"] != "1":
        raise ValueError("invalid public share methodology version")
    if not isinstance(payload["scope"], str) or payload["scope"] not in _SCOPES:
        raise ValueError("unsupported public share scope")

    window = payload["window"]
    if not isinstance(window, Mapping) or set(window) != _WINDOW_KEYS:
        raise ValueError("invalid public share window")
    window_start = _parse_iso_date(window["start"])
    window_end = _parse_iso_date(window["end"])
    days = _integer(window["days"], "window days", positive=True)
    if days != 30:
        raise ValueError("public share window must contain exactly 30 days")
    if window_end < window_start or days != (window_end - window_start).days + 1:
        raise ValueError("invalid public share day bounds")
    if (
        not isinstance(window["timezone"], str)
        or not window["timezone"]
        or len(window["timezone"]) > 64
    ):
        raise ValueError("invalid public share timezone")
    try:
        ZoneInfo(window["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("invalid public share timezone") from None

    metrics = payload["metrics"]
    if not isinstance(metrics, Mapping) or set(metrics) != _METRIC_KEYS:
        raise ValueError("invalid public share metrics")
    for field_name in (
        "agent_hours_per_day",
        "total_agent_hours",
        "peak_day_agent_hours",
    ):
        _nonnegative(metrics[field_name], field_name)
    agent_hours_per_day = float(metrics["agent_hours_per_day"])
    total_agent_hours = float(metrics["total_agent_hours"])
    peak_day_agent_hours = float(metrics["peak_day_agent_hours"])
    completed_turns = _integer(metrics["completed_turns"], "completed turns")
    active_days = _integer(metrics["active_days"], "active days")
    if active_days > days or active_days > completed_turns:
        raise ValueError("active days cannot exceed window days")
    if completed_turns < 0 or active_days < 0:
        raise ValueError("public share counts must be nonnegative")
    if (
        active_days == 0 and total_agent_hours > 0.0
    ) or peak_day_agent_hours * active_days + 0.000031 < total_agent_hours:
        raise ValueError("public share metrics are inconsistent")
    if abs(agent_hours_per_day * days - total_agent_hours) > 0.000031:
        raise ValueError("public share daily mean is inconsistent")
    if (
        peak_day_agent_hours > total_agent_hours + 0.000001
        or peak_day_agent_hours + 0.000001 < agent_hours_per_day
    ):
        raise ValueError("public share peak is inconsistent")

    leverage = payload["leverage"]
    if leverage is None:
        return
    if not isinstance(leverage, Mapping) or set(leverage) != _LEVERAGE_KEYS:
        raise ValueError("invalid public share leverage")
    ratio = _nonnegative(leverage["ratio"], "leverage ratio")
    human_hours = _positive(leverage["human_hours"], "human hours")
    if (
        not isinstance(leverage["basis"], str)
        or leverage["basis"] not in _LEVERAGE_BASES
    ):
        raise ValueError("invalid leverage basis")
    weekly = leverage["human_hours_per_week"]
    weekly_value = None if weekly is None else _positive(weekly, "human hours per week")
    if leverage["basis"] == "estimated-weekly" and weekly is None:
        raise ValueError("estimated leverage requires weekly human hours")
    if leverage["basis"] == "reported-period" and weekly is not None:
        raise ValueError("reported leverage cannot include weekly human hours")
    if leverage["basis"] == "estimated-weekly" and abs(
        weekly_value * 30.0 / 7.0 - human_hours
    ) > 0.000003:
        raise ValueError("estimated leverage hours are inconsistent")
    if (
        not math.isfinite(ratio * human_hours)
        or abs(
            ratio * human_hours
            - total_agent_hours
        )
        > 0.000002 + 0.000001 * (human_hours + ratio)
    ):
        raise ValueError("leverage ratio is inconsistent")


def _leverage_payload(
    leverage: LeverageMetrics | None,
) -> dict[str, Any] | None:
    if leverage is None:
        return None
    if not isinstance(leverage, LeverageMetrics):
        raise ValueError("invalid leverage value")  # noqa: TRY004 - public validation uses ValueError
    return {
        "ratio": _rounded(_nonnegative(leverage.ratio, "leverage ratio")),
        "human_hours": _rounded(_positive(leverage.human_hours, "human hours")),
        "basis": leverage.basis,
        "human_hours_per_week": (
            None
            if leverage.human_hours_per_week is None
            else _rounded(
                _positive(
                    leverage.human_hours_per_week, "human hours per week"
                )
            )
        ),
    }


def _timezone_name(value: tzinfo | str) -> str:
    if isinstance(value, str):
        if not value:
            raise ValueError("timezone must be a non-empty string")
        return value
    name = getattr(value, "key", None) or str(value)
    if not name:
        raise ValueError("timezone must be a non-empty string")
    return name


def _date_value(value: date, label: str) -> date:
    if type(value) is not date:
        raise ValueError(f"{label} must be a date")
    return value


def _parse_iso_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid public share dates")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid public share dates") from None
    if parsed.isoformat() != value:
        raise ValueError("invalid public share dates")
    return parsed


def _finite(value: Any, label: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(f"{label} must be finite")  # noqa: TRY004 - public validation uses ValueError
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be finite") from None
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def _nonnegative(value: Any, label: str) -> float:
    converted = _finite(value, label)
    if converted < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return converted


def _positive(value: Any, label: str) -> float:
    converted = _finite(value, label)
    if converted <= 0.0:
        raise ValueError(f"{label} must be positive")
    return converted


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")  # noqa: TRY004 - public validation uses ValueError
    if value < (1 if positive else 0):
        raise ValueError(f"{label} must be nonnegative")
    return value


def _rounded(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if rounded == 0.0 else rounded
