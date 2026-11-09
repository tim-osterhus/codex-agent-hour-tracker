#!/usr/bin/env python3
"""Compatibility entry point for the installed private account-usage exporter.

Prefer: agent-hours account-usage --label NAME
"""
import sys
from pathlib import Path

# Keep the original checkout invocation working before installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_hour_tracker.account_usage import main

if __name__ == '__main__':
    raise SystemExit(main())
