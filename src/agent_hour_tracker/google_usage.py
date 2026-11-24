"""Private token and measured-hours report for Antigravity and Muse Code."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .cli import _resolve_report_range, _resolve_timezone, _today_in_timezone
from .google_timing import antigravity_timing

_PROVIDERS = {"antigravity": "Antigravity", "muse": "Muse Code"}


@dataclass
class Usage:
    tokens: dict[date, int] | None
    hours: dict[date, float]
    missing_token_days: set[date] = field(default_factory=set)

    def tokens_on(self, day: date) -> int | None:
        if self.tokens is None:
            return None
        return self.tokens.get(day, None if day in self.missing_token_days else 0)


def _token_usage(executable: str, start: date, end: date, timezone: ZoneInfo) -> dict[date, int]:
    with TemporaryDirectory(prefix="agent-hours-google-") as directory:
        config = Path(directory) / "ccusage.json"
        config.write_text("{}", encoding="utf-8")
        proc = subprocess.run(
            [executable, "antigravity", "daily", "--config", str(config), "--json",
             "--offline", "--no-cost", "--timezone", timezone.key,
             "--since", start.isoformat().replace("-", ""),
             "--until", end.isoformat().replace("-", "")],
            capture_output=True, check=True, timeout=600,
        )
    data = json.loads(proc.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("daily"), list):
        raise ValueError("invalid ccusage report")
    totals: dict[date, int] = {}
    for row in data["daily"]:
        day = date.fromisoformat(row["date"])
        tokens = row["totalTokens"]
        if day in totals or not start <= day <= end or type(tokens) is not int or tokens < 0:
            raise ValueError("invalid ccusage daily row")
        totals[day] = tokens
    if sum(totals.values()) != data["totals"]["totalTokens"]:
        raise ValueError("inconsistent ccusage total")
    if proc.stderr.strip():
        print("warning: ccusage reported diagnostics for Antigravity; "
              "some token records may be unavailable", file=sys.stderr)
    return totals


def _print_csv(results: dict[str, Usage], dates: list[date]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(["date", *(f"{provider}_{field}" for provider in results
                               for field in ("tokens", "measured_agent_hours"))])
    for day in dates:
        row: list[object] = [day.isoformat()]
        for result in results.values():
            row.extend((result.tokens_on(day), result.hours.get(day)))
        writer.writerow(row)


def _print_text(results: dict[str, Usage], dates: list[date], timezone: ZoneInfo, title: str) -> None:
    print(f"{title} · {dates[0]} to {dates[-1]} ({timezone.key})")
    for provider, result in results.items():
        print(f"\n{_PROVIDERS[provider]}")
        if result.tokens is None:
            print("Tokens: unavailable")
        else:
            total = sum(result.tokens.values())
            print(f"Tokens: {total:,} total · {total / len(dates):,.0f}/day")
        if result.hours:
            print(f"Measured agent hours: {sum(result.hours.values()):.4f}")
        else:
            print("Measured agent hours: unavailable")
        print(f"{'Date':<12} {'Tokens':>16} {'Measured hours':>16}")
        for day in dates:
            count = result.tokens_on(day)
            tokens = f"{count:,}" if count is not None else "n/a"
            hours = f"{result.hours[day]:.4f}" if day in result.hours else "n/a"
            print(f"{day.isoformat():<12} {tokens:>16} {hours:>16}")
    if len(results) > 1:
        missing = [_PROVIDERS[provider] for provider, result in results.items() if result.tokens is None]
        if missing:
            print(f"\nCombined tokens: unavailable (no token counts for {', '.join(missing)})")
        else:
            combined = sum(sum((result.tokens or {}).values()) for result in results.values())
            print(f"\nCombined tokens: {combined:,} total · {combined / len(dates):,.0f}/day")
    notes = []
    if "antigravity" in results:
        notes.append("Antigravity token counts include cached tokens.")
    if "muse" in results:
        notes.append("Muse Code figures cover retained local session logs only.")
    print("\n" + " ".join([*notes, "Measured hours cover recorded work intervals."]))


def _muse_usage(roots: list[Path] | None, timezone: ZoneInfo, start: date, end: date) -> tuple[Usage, bool]:
    """Return Muse usage and whether its token read failed."""

    try:
        from .muse_usage import scan_muse

        scan = scan_muse(roots, timezone)
    except (ImportError, OSError, ValueError):
        print("error: unable to read Muse Code session logs", file=sys.stderr)
        return Usage(None, {}), True
    skipped = scan.unreadable + scan.unsupported
    hours = {day: value for day, value in scan.hours.items() if start <= day <= end}
    tokens = {day: value for day, value in scan.tokens.items() if start <= day <= end}
    missing = {day: count for day, count in scan.missing_tokens.items() if start <= day <= end}
    if not scan.files_scanned:
        if scan.unreadable:
            print("error: unable to read Muse Code session logs", file=sys.stderr)
            return Usage(None, {}), True
        print("warning: no retained Muse Code session logs found; Muse Code tokens are unavailable",
              file=sys.stderr)
        return Usage(None, hours), False
    skipped_in_report = skipped + sum(missing.values())
    if (missing and not tokens) or (skipped and not scan.tokens):
        print(f"error: no Muse Code tokens read; skipped {skipped_in_report} unreadable, unsupported, or incomplete "
              "inputs or records", file=sys.stderr)
        return Usage(None, hours), True
    if skipped_in_report:
        print(f"warning: skipped {skipped_in_report} unreadable, unsupported, or incomplete Muse Code inputs or records; "
              "Muse Code totals are partial", file=sys.stderr)
    return Usage(tokens, hours, set(missing)), False


def main(argv: list[str], *, combined: bool = False) -> int:
    """Run ``google-usage`` for Antigravity or ``usage`` for both providers."""

    choices = tuple(_PROVIDERS) if combined else ("antigravity",)
    parser = argparse.ArgumentParser(
        prog="agent-hours usage" if combined else "agent-hours google-usage",
        description=("Report local Antigravity and Muse Code tokens and measured agent hours."
                     if combined else
                     "Report local Antigravity tokens and measured agent hours."),
    )
    parser.add_argument("--provider", choices=(*choices, "all"), default="all")
    parser.add_argument("--ccusage", default="ccusage", metavar="EXECUTABLE",
                        help="installed ccusage executable (default: ccusage on PATH)")
    if combined:
        parser.add_argument("--muse-sessions-dir", type=Path, action="append", metavar="PATH",
                            help="Muse Code sessions directory; repeat to combine roots")
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="first day, inclusive")
    parser.add_argument("--end", metavar="YYYY-MM-DD", help="last day, inclusive (default: yesterday)")
    parser.add_argument("--timezone", metavar="IANA_ZONE")
    parser.add_argument("--format", choices=("text", "csv"), default="text")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    providers = choices if args.provider == "all" else (args.provider,)
    muse_roots = getattr(args, "muse_sessions_dir", None)
    if muse_roots and "muse" not in providers:
        print("error: --muse-sessions-dir requires Muse Code in the report", file=sys.stderr)
        return 2
    for root in muse_roots or ():
        if not root.is_dir():
            print(f"error: Muse Code sessions directory does not exist: {root}", file=sys.stderr)
            return 2
    timezone = _resolve_timezone(args.timezone, sys.stderr)
    if timezone is None:
        return 2
    period = _resolve_report_range(args.start, args.end, _today_in_timezone(timezone), sys.stderr)
    if period is None:
        return 2
    start, end = period
    include_antigravity = "antigravity" in providers
    executable = shutil.which(args.ccusage) if include_antigravity else None
    failed = False
    if include_antigravity and executable is None:
        message = "install ccusage v20.0.51 or later, or supply --ccusage EXECUTABLE"
        if not combined:
            print(f"error: {message}", file=sys.stderr)
            return 2
        print(f"error: ccusage not found; Antigravity tokens are unavailable ({message})", file=sys.stderr)
        failed = True
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    results = {}
    for provider in providers:
        if provider == "muse":
            results[provider], muse_failed = _muse_usage(muse_roots, timezone, start, end)
            failed = failed or muse_failed
            continue
        tokens = None
        if executable is not None:
            try:
                tokens = _token_usage(executable, start, end, timezone)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                print(f"error: unable to read {_PROVIDERS[provider]} tokens using ccusage", file=sys.stderr)
                failed = True
        timing = antigravity_timing()
        if timing.unreadable:
            print(f"warning: skipped {timing.unreadable} unreadable {_PROVIDERS[provider]} timing inputs or records",
                  file=sys.stderr)
        hours = {day: hours for day, hours in timing.daily_hours(timezone).items() if start <= day <= end}
        results[provider] = Usage(tokens, hours)
    if args.format == "csv":
        _print_csv(results, dates)
    else:
        title = "Coding agent usage" if combined else "Google coding agent usage"
        _print_text(results, dates, timezone, title)
    return int(failed)
