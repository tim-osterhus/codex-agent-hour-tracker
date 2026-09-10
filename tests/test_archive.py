from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_hour_tracker.archive import (
    ArchiveError,
    collect_archive,
    load_archive,
    merge_archives,
    write_archive,
)
from agent_hour_tracker.scanner import CompletedTurn


def turn_digest(turn_id: str) -> str:
    return hashlib.sha256(f"str:{turn_id}".encode()).hexdigest()


def archive_with_observations(*observations: dict[str, object]) -> dict:
    return {
        "schema": "agent-hours-archive",
        "schema_version": 1,
        "methodology_version": "1",
        "observations": list(observations),
        "diagnostics": {},
    }


def observation(
    turn_id: str,
    duration_seconds: float,
    source_kind: str,
    *,
    started_at: float = 100.0,
    event_timing_usable: bool = False,
) -> dict[str, object]:
    return {
        "turn_id_sha256": turn_digest(turn_id),
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "event_timing_usable": event_timing_usable,
        "source_kind": source_kind,
    }


class ArchiveTests(unittest.TestCase):
    def _write_session(
        self,
        directory: Path,
        name: str,
        turn_id: str,
        duration_ms: int,
        source: object,
    ) -> Path:
        path = directory / name
        records = (
            {"type": "session_meta", "source": source},
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": turn_id,
                    "started_at": 100.0,
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": turn_id,
                    "duration_ms": duration_ms,
                },
            },
        )
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
        return path

    def test_collect_is_timing_only_and_retains_batch_by_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_session(directory, "root.jsonl", "secret-id", 1_000, "cli")
            self._write_session(
                directory,
                "batch.jsonl",
                "secret-id",
                2_000,
                {"exec": True},
            )

            archive = collect_archive([directory], label="local sample")

        self.assertEqual(archive["schema"], "agent-hours-archive")
        self.assertEqual(archive["schema_version"], 1)
        self.assertEqual(archive["methodology_version"], "1")
        self.assertEqual(archive["label"], "local sample")
        self.assertEqual(archive["diagnostics"]["files_scanned"], 2)
        self.assertEqual(len(archive["observations"]), 2)
        encoded = json.dumps(archive)
        self.assertNotIn("secret-id", encoded)
        self.assertEqual(
            {item["source_kind"] for item in archive["observations"]},
            {"root", "batch"},
        )
        self.assertTrue(
            all(
                len(item["turn_id_sha256"]) == 64
                and item["turn_id_sha256"].islower()
                for item in archive["observations"]
            )
        )

    def test_collect_keeps_best_observation_per_source_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_session(directory, "first.jsonl", "same", 1_000, "cli")
            self._write_session(directory, "second.jsonl", "same", 3_000, "cli")

            archive = collect_archive([directory])

        self.assertEqual(len(archive["observations"]), 1)
        self.assertEqual(archive["observations"][0]["duration_seconds"], 3.0)
        self.assertEqual(archive["diagnostics"]["duplicate_turns"], 1)

    def test_merge_filters_batch_before_global_dedup_and_roots_independently(
        self,
    ) -> None:
        digest = turn_digest("same")
        archives = [
            archive_with_observations(
                observation("same", 2.0, "root"),
                observation("same", 9.0, "delegated"),
                observation("same", 20.0, "batch"),
            )
        ]

        result = merge_archives(archives)
        self.assertEqual(result.turns, [CompletedTurn(100.0, 9.0)])
        self.assertEqual(result.root_turns, [CompletedTurn(100.0, 2.0)])
        self.assertEqual(result.diagnostics.excluded_batch_turns, 1)
        self.assertEqual(result.diagnostics.duplicate_turns, 1)
        self.assertEqual(digest, archives[0]["observations"][0]["turn_id_sha256"])

        including_exec = merge_archives(archives, include_exec=True)
        self.assertEqual(including_exec.turns, [CompletedTurn(100.0, 20.0)])
        self.assertEqual(including_exec.root_turns, [CompletedTurn(100.0, 2.0)])

    def test_round_trip_is_deterministic(self) -> None:
        archive = archive_with_observations(
            observation("two", 2.0, "delegated", started_at=200.0),
            observation("one", 1.0, "root"),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            first_path = Path(temporary_directory) / "archive.json"
            second_path = Path(temporary_directory) / "archive-copy.json"
            write_archive(archive, first_path)
            first = first_path.read_text()
            loaded = load_archive(first_path)
            write_archive(loaded, second_path)
            second = second_path.read_text()

        self.assertEqual(first, second)
        self.assertEqual(loaded["observations"][0]["started_at"], 100.0)

    def test_write_refuses_overwrite_and_uses_private_permissions(self) -> None:
        archive = archive_with_observations(observation("one", 1.0, "root"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "archive.json"
            write_archive(archive, path)
            with self.assertRaises(ArchiveError):
                write_archive(archive, path)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_load_rejects_bad_schema_methodology_and_private_values_safely(
        self,
    ) -> None:
        base = archive_with_observations(observation("one", 1.0, "root"))
        cases = (
            ("schema", "not-an-archive"),
            ("methodology_version", "old"),
            ("observations", "not-a-list"),
        )
        for field_name, value in cases:
            with self.subTest(field_name=field_name):
                invalid = dict(base)
                invalid[field_name] = value
                with self.assertRaises(ArchiveError):
                    merge_archives([invalid])

        secret = "TOP_SECRET_TRANSCRIPT"
        invalid = archive_with_observations(observation("one", 1.0, "root"))
        invalid["observations"][0]["duration_seconds"] = secret
        with self.assertRaises(ArchiveError) as raised:
            merge_archives([invalid])
        self.assertNotIn(secret, str(raised.exception))

    def test_load_rejects_nonfinite_duration_and_unsupported_source(self) -> None:
        invalid_duration = archive_with_observations(
            observation("one", 1.0, "root")
        )
        invalid_duration["observations"][0]["duration_seconds"] = float("nan")
        with self.assertRaises(ArchiveError):
            merge_archives([invalid_duration])

        invalid_source = archive_with_observations(
            observation("one", 1.0, "private")
        )
        with self.assertRaises(ArchiveError):
            merge_archives([invalid_source])

        invalid_timestamp = archive_with_observations(
            observation("one", 1.0, "root", started_at=10**400)
        )
        with self.assertRaises(ArchiveError):
            merge_archives([invalid_timestamp])

    def test_labels_reject_terminal_controls(self) -> None:
        archive = archive_with_observations(observation("one", 1.0, "root"))
        archive["label"] = "machine\nsecret"
        with tempfile.TemporaryDirectory() as temporary_directory, self.assertRaises(
            ArchiveError
        ):
            write_archive(
                archive,
                Path(temporary_directory) / "invalid-label.json",
            )

    def test_collect_reports_missing_and_unsupported_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            unsupported = directory / "notes.txt"
            unsupported.write_text("not a session")
            archive = collect_archive(
                [unsupported, directory / "missing", directory]
            )

        self.assertEqual(archive["diagnostics"]["unsupported_paths"], 1)
        self.assertEqual(archive["diagnostics"]["missing_paths"], 1)

    def test_collect_reports_traversal_errors_without_private_path_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            with patch.object(os, "walk", side_effect=OSError("secret path")):
                archive = collect_archive([directory])

        self.assertEqual(archive["diagnostics"]["traversal_errors"], 1)
        self.assertNotIn("secret path", json.dumps(archive))


if __name__ == "__main__":
    unittest.main()
