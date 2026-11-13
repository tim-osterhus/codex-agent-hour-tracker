from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_hour_tracker.archive import ArchiveError, collect_archive, merge_archives, write_archive
from agent_hour_tracker.cli import main


class ProviderIntegrationTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def archive(self, provider: str, duration: float, *, version: int = 2) -> dict:
        observation = {
            "turn_id_sha256": hashlib.sha256(b"str:shared-id").hexdigest(),
            "started_at": 1767225600.0,
            "duration_seconds": duration,
            "event_timing_usable": True,
            "source_kind": "root" if provider == "codex" else "unknown",
        }
        if version == 2:
            observation["provider"] = provider
        return {"schema": "agent-hours-archive", "schema_version": version,
                "methodology_version": "1", "observations": [observation], "diagnostics": {}}

    def test_old_new_and_cross_provider_identity(self) -> None:
        old = self.archive("codex", 10, version=1)
        new = self.archive("codex", 20)
        claude = self.archive("claude", 30)
        result = merge_archives([old, new, claude, claude])
        self.assertEqual([(t.provider, t.duration_seconds) for t in result.turns],
                         [("codex", 20), ("claude", 30)])
        self.assertEqual(len(result.root_turns), 1)
        self.assertEqual(sum(t.duration_seconds for t in merge_archives([old, new, claude], provider="codex").turns), 20)
        self.assertEqual(sum(t.duration_seconds for t in merge_archives([old, new, claude], provider="claude").turns), 30)

    def test_v2_provider_validation_is_strict_and_safe(self) -> None:
        for value in (None, "private-secret", [], True):
            archive = self.archive("codex", 1)
            archive["observations"][0]["provider"] = value
            with self.subTest(value=value), self.assertRaises(ArchiveError) as raised:
                merge_archives([archive])
            self.assertNotIn("private-secret", str(raised.exception))
        archive = self.archive("codex", 1, version=1)
        archive["observations"][0]["provider"] = "codex"
        with self.assertRaises(ArchiveError):
            merge_archives([archive])

    def test_archive_version_requires_integer(self) -> None:
        for version in (True, 1.0, 2.0, "2", None):
            archive = self.archive("codex", 1)
            archive["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(ArchiveError):
                merge_archives([archive])

    def test_four_machine_merge_provider_text_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for index in range(4):
                archive = self.archive("codex", 3600)
                archive["observations"] += self.archive("claude", 7200)["observations"]
                path = Path(temporary) / f"machine{index}.json"
                write_archive(archive, path)
                paths.extend(("--merge", str(path)))
            args = (*paths, "--provider", "all", "--start", "2026-01-01", "--end", "2026-01-02", "--timezone", "UTC")
            code, stdout, stderr = self.run_cli(*args)
            self.assertEqual(code, 0, stderr)
            self.assertIn("Total agent-hours:        3.00", stdout)
            self.assertIn("Codex: 1.00 agent-hours, 1 completed turns", stdout)
            self.assertIn("Claude Code: 2.00 agent-hours, 1 completed turns", stdout)
            code, stdout, stderr = self.run_cli(*args, "--format", "csv")
            self.assertEqual(code, 0, stderr)
            rows = list(csv.DictReader(io.StringIO(stdout)))
            self.assertEqual(rows[0]["agent_hours"], "3.000000")
            self.assertEqual(rows[0]["codex_agent_hours"], "1.000000")
            self.assertEqual(rows[0]["claude_agent_hours"], "2.000000")
            self.assertEqual(rows[1]["agent_hours"], "0.000000")

    def test_share_provider_filter_from_old_and_mixed_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            old = self.archive("codex", 3600, version=1)
            mixed = self.archive("codex", 3600)
            mixed["observations"] += self.archive("claude", 7200)["observations"]
            paths = []
            for index, archive in enumerate((old, mixed)):
                archive["label"] = "PRIVATE_MACHINE_LABEL"
                path = Path(temporary) / f"private-machine-{index}.json"
                write_archive(archive, path)
                paths.extend(("--merge", str(path)))
            for provider, hours in (("codex", 1), ("claude", 2), ("all", 3)):
                with self.subTest(provider=provider), patch("agent_hour_tracker.cli._today_in_timezone", return_value=__import__("datetime").date(2026, 1, 2)):
                    code, stdout, stderr = self.run_cli("--provider", provider, *paths, "--share", "--format", "json", "--timezone", "UTC")
                self.assertEqual(code, 0, stderr)
                score = json.loads(stdout)
                self.assertEqual(score["schema_version"], 1 if provider == "codex" else 2)
                self.assertEqual(score.get("provider", "codex"), provider)
                self.assertEqual(score["metrics"]["total_agent_hours"], hours)
                self.assertEqual(score["metrics"]["completed_turns"], 1 if provider != "all" else 2)
                self.assertEqual(score["window"], {"start": "2025-12-03", "end": "2026-01-01", "days": 30, "timezone": "UTC"})
                self.assertEqual(score["scope"], "interactive-only")
                self.assertIsNone(score["leverage"])
                if provider == "codex":
                    self.assertNotIn("provider", score)
                for marker in ("PRIVATE_MACHINE_LABEL", "private-machine", "turn_id", "diagnostics", "observations", "account", "daily", temporary):
                    self.assertNotIn(marker, stdout + stderr)
                with patch("agent_hour_tracker.cli._today_in_timezone", return_value=__import__("datetime").date(2026, 1, 2)):
                    code, text, stderr = self.run_cli("--provider", provider, *paths, "--share", "--timezone", "UTC")
                self.assertEqual(code, 0, stderr)
                if provider != "codex":
                    self.assertIn("origin unverified", text)

    def test_explicit_roots_infer_provider_and_export_private_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex, claude = root / "codex", root / "claude"
            codex.mkdir(); claude.mkdir()
            marker = "PRIVATE_TRANSCRIPT_DO_NOT_EXPORT"
            (claude / "session.jsonl").write_text(json.dumps({
                "type": "system", "subtype": "turn_duration", "durationMs": 3600000,
                "timestamp": "2026-01-01T01:00:00Z", "uuid": "00000000-0000-0000-0000-000000000001",
                "isSidechain": False, "content": marker,
            }) + "\n")
            (claude / "PRIVATE_BAD_FILE.jsonl").write_text('{"type": "system", "subtype": "turn_duration", "durationMs": PRIVATE_BAD_CONTENT}\n')
            path = root / "private.json"
            code, stdout, stderr = self.run_cli("--sessions-dir", str(codex), "--claude-sessions-dir", str(claude), "--export", str(path))
            self.assertEqual(code, 0, stderr)
            self.assertEqual(stdout, "")
            archive = json.loads(path.read_text())
            self.assertEqual(archive["schema_version"], 2)
            self.assertEqual(archive["observations"][0]["provider"], "claude")
            self.assertNotIn(marker, path.read_text() + stderr)
            self.assertEqual(merge_archives([archive]).turns[0].duration_seconds, 3600)
            for roots, provider in ((("--claude-sessions-dir", str(claude)), "claude"), (("--sessions-dir", str(codex), "--claude-sessions-dir", str(claude)), "all")):
                with patch("agent_hour_tracker.cli._today_in_timezone", return_value=__import__("datetime").date(2026, 1, 2)):
                    code, public, errors = self.run_cli(*roots, "--share", "--format", "json", "--timezone", "UTC")
                self.assertEqual(code, 0, errors)
                score = json.loads(public)
                self.assertEqual(score["provider"], provider)
                self.assertEqual(score["metrics"]["total_agent_hours"], 1)
                self.assertNotIn(marker, public + errors)
                self.assertNotIn(str(root), public + errors)
                self.assertNotIn("PRIVATE_BAD", public + errors)
                self.assertIn("Malformed lines: 1", errors)
            code, stdout, stderr = self.run_cli("--claude-sessions-dir", str(claude), "--start", "2026-01-01", "--end", "2026-01-01", "--timezone", "UTC")
            self.assertEqual(code, 0, stderr)
            self.assertIn("Providers: Claude Code", stdout)
            self.assertIn("Scope: native-recorded-turns (origin unverified)", stdout)

    def test_discovery_and_explicit_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            (config / "projects").mkdir()
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(config)}), patch("agent_hour_tracker.cli.Path.home", return_value=config / "absent-home"):
                code, stdout, stderr = self.run_cli("--provider", "all")
                self.assertEqual(code, 0, stderr)
                self.assertIn("Providers: Codex + Claude Code", stdout)
                code, stdout, stderr = self.run_cli("--provider", "claude")
                self.assertEqual(code, 0, stderr)
            code, stdout, stderr = self.run_cli("--provider", "codex", "--claude-sessions-dir", str(config))
            self.assertEqual(code, 2)
            self.assertIn("conflict", stderr)
            code, stdout, stderr = self.run_cli("--provider", "all", "--claude-sessions-dir", str(config / "missing"))
            self.assertEqual(code, 2)
            self.assertIn("missing or not a directory", stderr)


if __name__ == "__main__":
    unittest.main()
