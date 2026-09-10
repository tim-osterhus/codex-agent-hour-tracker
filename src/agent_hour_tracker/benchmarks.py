"""Read the dated public benchmark registry bundled with the package."""

from __future__ import annotations

import json
from importlib.resources import files

__all__ = ["load_benchmarks"]


def load_benchmarks() -> dict:
    """Return reference values and their units, populations, and limitations."""
    return json.loads(
        files("agent_hour_tracker").joinpath("benchmarks.json").read_text(encoding="utf-8")
    )
