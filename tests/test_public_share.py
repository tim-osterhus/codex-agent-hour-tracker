from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from zoneinfo import ZoneInfo

from agent_hour_tracker.metrics import build_report_metrics
from agent_hour_tracker.share import build_public_share, render_share_json, validate_public_share


class PublicShareTests(unittest.TestCase):
    def payload(self, provider: str = "codex") -> dict:
        report = build_report_metrics([], date(2026, 1, 1), date(2026, 1, 30), ZoneInfo("UTC"))
        return build_public_share(report, "0.3.0", "1", provider=provider)

    def test_versions_round_trip_and_exact_fields(self) -> None:
        legacy = self.payload()
        self.assertEqual(legacy["schema_version"], 1)
        self.assertNotIn("provider", legacy)
        validate_public_share(json.loads(json.dumps(legacy)))
        for provider in ("codex", "claude", "all"):
            payload = self.payload(provider)
            payload.update(schema_version=2, provider=provider)
            validate_public_share(json.loads(json.dumps(payload)))
            self.assertEqual(set(payload), set(legacy) | {"provider"})
        report = build_report_metrics([], date(2026, 1, 1), date(2026, 1, 30), ZoneInfo("UTC"))
        self.assertEqual(json.loads(render_share_json(report, "0.3.0", "1", provider="all")), self.payload("all"))

    def test_provider_and_version_validation(self) -> None:
        for value in (None, True, [], {}, "CODEX", "private-path"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.payload(value)
            payload = self.payload("all")
            payload["provider"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_public_share(payload)
        for version in (True, 1.0, 2.0, "2", 3, None):
            payload = self.payload("all")
            payload["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_public_share(payload)
        payload = self.payload()
        payload["provider"] = "codex"
        with self.assertRaises(ValueError):
            validate_public_share(payload)
        payload = self.payload("all")
        del payload["provider"]
        with self.assertRaises(ValueError):
            validate_public_share(payload)

    def test_private_fields_rejected_at_every_level(self) -> None:
        for provider in ("codex", "claude", "all"):
            for level in (None, "window", "metrics"):
                payload = self.payload(provider)
                (payload if level is None else payload[level])["private"] = "secret"
                with self.subTest(provider=provider, level=level), self.assertRaises(ValueError):
                    validate_public_share(payload)

    def test_dates_remain_strict_for_both_versions(self) -> None:
        for provider in ("codex", "all"):
            for field, value in (("start", "2026-01-02"), ("end", "2026-01-31"), ("start", "2026-1-01"), ("start", "2026-02-30"), ("days", 29), ("days", True)):
                payload = copy.deepcopy(self.payload(provider))
                payload["window"][field] = value
                with self.subTest(provider=provider, field=field, value=value), self.assertRaises(ValueError):
                    validate_public_share(payload)


if __name__ == "__main__":
    unittest.main()
