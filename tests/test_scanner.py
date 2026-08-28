import json
import math
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import agent_hour_tracker.scanner as scanner_module
from agent_hour_tracker.scanner import CompletedTurn, ScanResult, scan_sessions


class ScannerTests(unittest.TestCase):
    def _scan(self, *lines: object) -> tuple[ScanResult, Path]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            session_file = directory / "session.jsonl"
            encoded_lines = [
                line
                if isinstance(line, bytes)
                else line.encode("utf-8")
                if isinstance(line, str)
                else json.dumps(line).encode("utf-8")
                for line in lines
            ]
            session_file.write_bytes(b"\n".join(encoded_lines) + b"\n")
            return scan_sessions(directory), session_file

    def _start(self, turn_id: str, started_at: object) -> dict[str, object]:
        return {
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": turn_id,
                "started_at": started_at,
            },
        }

    def _complete(self, turn_id: str, **metadata: object) -> dict[str, object]:
        return {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": turn_id, **metadata},
        }

    def _write_file(
        self, directory: Path, name: str, lines: list[bytes | str | object]
    ) -> None:
        path = directory / name
        encoded_lines = [
            line
            if isinstance(line, bytes)
            else line.encode("utf-8")
            if isinstance(line, str)
            else json.dumps(line).encode("utf-8")
            for line in lines
        ]
        path.write_bytes(b"\n".join(encoded_lines) + b"\n")

    def _timestamped(
        self, timestamp: str, record: dict[str, object]
    ) -> dict[str, object]:
        return {"timestamp": timestamp, **record}

    def test_scan_result_has_independent_defaults(self) -> None:
        first = ScanResult()
        second = ScanResult()

        self.assertEqual(first.turns, [])
        self.assertEqual(first.diagnostics.files_scanned, 0)
        self.assertIsNot(first.turns, second.turns)
        self.assertIsNot(first.diagnostics, second.diagnostics)

    def test_recorded_duration_pairs_completion_to_turn_id(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._start("one", 100.0),
            self._start("two", 200.0),
            self._complete("two", duration_ms=2500),
            self._complete("one", duration_ms=1250),
        )

        self.assertEqual(
            result.turns,
            [
                CompletedTurn(started_at=100.0, duration_seconds=1.25),
                CompletedTurn(started_at=200.0, duration_seconds=2.5),
            ],
        )
        self.assertEqual(result.diagnostics.files_scanned, 1)

    def test_completed_timestamp_fallback_uses_completion_started_at(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._start("one", 100.0),
            self._complete("one", started_at=105.0, completed_at=107.5),
        )

        self.assertEqual(result.turns, [CompletedTurn(105.0, 2.5)])
        self.assertEqual(result.diagnostics.duration_fallbacks, 1)

    def test_timestamp_fallback_uses_separate_start_event(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._start("one", 100.0),
            self._complete("one", completed_at=103.0),
        )

        self.assertEqual(result.turns, [CompletedTurn(100.0, 3.0)])
        self.assertEqual(result.diagnostics.duration_fallbacks, 1)

    def test_nested_subagent_source_is_included(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "payload": {"source": {"subagent": True}}},
            self._start("one", 100.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(len(result.turns), 1)
        self.assertEqual(result.diagnostics.unknown_sources, Counter())

    def test_batch_source_excludes_valid_completed_turn(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": {"exec": "batch-run"}},
            self._start("one", 100.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.excluded_batch_turns, 1)

    def test_unknown_source_is_counted_once_per_file_and_included(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "mystery"},
            {"type": "session_meta", "source": "mystery"},
            self._start("one", 100.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(len(result.turns), 1)
        self.assertEqual(len(result.diagnostics.unknown_sources), 1)
        self.assertEqual(next(iter(result.diagnostics.unknown_sources.values())), 1)
        self.assertTrue(
            next(iter(result.diagnostics.unknown_sources)).startswith(
                "unknown:string:"
            )
        )

    def test_distinct_unknown_sources_are_counted_once_after_interactive_source(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            {"type": "session_meta", "source": "first-unknown"},
            {"type": "session_meta", "source": "first-unknown"},
            {"type": "session_meta", "source": "second-unknown"},
            {"type": "session_meta", "source": "second-unknown"},
            self._start("one", 100.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(len(result.turns), 1)
        self.assertEqual(
            sorted(result.diagnostics.unknown_sources.values()), [1, 1]
        )
        self.assertTrue(
            all(
                label.startswith("unknown:string:")
                for label in result.diagnostics.unknown_sources
            )
        )

    def test_batch_status_excludes_turns_even_with_unknown_source_records(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": {"exec": True}},
            {"type": "session_meta", "source": "unknown-after-batch"},
            self._start("one", 100.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.excluded_batch_turns, 1)

    def test_malformed_candidate_line_is_reported_without_crashing(self) -> None:
        result, session_file = self._scan(
            b'{"type":"task_started",',
            {"type": "session_meta", "source": "cli"},
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 1)
        self.assertEqual(result.diagnostics.malformed_files, {session_file})

    def test_start_without_completion_is_incomplete(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._start("one", 100.0),
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.incomplete_turns, 1)

    def test_invalid_completion_does_not_consume_pending_start(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._start("one", 100.0),
            self._complete("one", duration_ms=-1.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(result.turns, [CompletedTurn(100.0, 1.0)])
        self.assertEqual(result.diagnostics.malformed_lines, 1)
        self.assertEqual(result.diagnostics.incomplete_turns, 0)

    def test_completion_without_start_is_unmatched_but_not_malformed(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._complete("missing", duration_ms=1000),
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.unmatched_completions, 1)
        self.assertEqual(result.diagnostics.malformed_lines, 0)

    def test_invalid_nonfinite_and_negative_metadata_is_rejected(self) -> None:
        result, session_file = self._scan(
            {"type": "session_meta", "source": "cli"},
            self._start("negative", -1.0),
            self._start("nan", math.nan),
            self._start("valid", 100.0),
            self._complete("valid", duration_ms=-1.0),
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 3)
        self.assertEqual(result.diagnostics.malformed_files, {session_file})
        self.assertEqual(result.diagnostics.incomplete_turns, 1)

    def test_malformed_candidate_values_do_not_stop_following_lines(self) -> None:
        huge_duration = (
            b'{"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"huge","duration_ms":'
            + b"9" * 5000
            + b"}}"
        )
        deep_candidate = (
            b'{"payload":' * 1200
            + b'{"type":"task_complete","turn_id":"deep","duration_ms":1000}'
            + b"}" * 1199
        )
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            huge_duration,
            deep_candidate,
            self._start("real", 100.0),
            self._complete("real", duration_ms=1000),
        )

        self.assertEqual(result.turns, [CompletedTurn(100.0, 1.0)])
        self.assertEqual(result.diagnostics.malformed_lines, 1)

    def test_nested_fake_events_in_non_event_records_are_ignored(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta", "source": "cli"},
            {
                "type": "message",
                "payload": {
                    "type": "task_started",
                    "turn_id": "fake",
                    "started_at": 1.0,
                },
            },
            {
                "type": "function_call_output",
                "output": {
                    "type": "task_complete",
                    "turn_id": "fake",
                    "duration_ms": 9999,
                },
            },
            {"type": "message", "payload": {"type": "session_meta", "source": "exec"}},
            self._start("real", 100.0),
            self._complete("real", duration_ms=1000),
        )

        self.assertEqual(result.turns, [CompletedTurn(100.0, 1.0)])
        self.assertEqual(result.diagnostics.unmatched_completions, 0)
        self.assertEqual(result.diagnostics.excluded_batch_turns, 0)

    def test_missing_source_is_reported_with_safe_label(self) -> None:
        result, _ = self._scan(
            {"type": "session_meta"},
            self._start("one", 100.0),
            self._complete("one", duration_ms=1000),
        )

        self.assertEqual(len(result.turns), 1)
        self.assertEqual(
            result.diagnostics.unknown_sources, Counter({"unknown:missing": 1})
        )

    def test_unknown_source_labels_are_bounded_and_sanitized(self) -> None:
        source = "TOP_SECRET_TRANSCRIPT\n" + "x" * 500
        result, _ = self._scan({"type": "session_meta", "source": source})

        labels = result.diagnostics.unknown_sources
        self.assertEqual(sum(labels.values()), 1)
        label = next(iter(labels))
        self.assertLessEqual(len(label), 64)
        self.assertNotIn(source, label)
        self.assertNotIn("TOP_SECRET_TRANSCRIPT", label)
        self.assertRegex(label, r"^unknown:string:[0-9a-f]{16}$")

    def test_active_duration_caps_each_top_level_timestamp_gap(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            self._timestamped(
                "2026-06-01T00:10:00Z", {"type": "message", "payload": "first"}
            ),
            self._timestamped(
                "2026-06-01T01:40:00Z", {"type": "message", "payload": "idle"}
            ),
            self._timestamped(
                "2026-06-01T01:50:00Z",
                self._complete("turn", duration_ms=6_600_000),
            ),
        )

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 3_000.0)])
        self.assertEqual(result.diagnostics.event_timing_fallbacks, 0)

    def test_duplicate_turn_ids_across_files_are_retained_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            records = [
                self._timestamped(
                    "2026-06-01T00:00:00Z",
                    {"type": "session_meta", "source": "cli"},
                ),
                self._timestamped(
                    "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
                ),
                self._timestamped(
                    "2026-06-01T00:01:00Z", self._complete("turn")
                ),
            ]
            self._write_file(directory, "one.jsonl", records)
            self._write_file(directory, "two.jsonl", records)

            result = scan_sessions(directory)

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 60.0)])
        self.assertEqual(result.diagnostics.duplicate_turns, 1)

    def test_best_replayed_observation_wins_in_both_path_orders(self) -> None:
        valid_records = [
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            self._timestamped("2026-06-01T00:10:00Z", self._complete("turn")),
        ]
        replay_records = [
            self._timestamped(
                "1970-01-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "1970-01-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            self._timestamped(
                "1970-01-01T00:00:00Z",
                self._complete("turn", duration_ms=600_000),
            ),
        ]

        for first_name, second_name in (("a.jsonl", "b.jsonl"), ("b.jsonl", "a.jsonl")):
            with self.subTest(first_name=first_name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    directory = Path(temporary_directory)
                    self._write_file(directory, first_name, replay_records)
                    self._write_file(directory, second_name, valid_records)

                    result = scan_sessions(directory)

                self.assertEqual(
                    result.turns, [CompletedTurn(1_780_272_000, 600.0)]
                )
                self.assertEqual(result.diagnostics.duplicate_turns, 1)

    def test_batch_duplicate_does_not_suppress_interactive_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            common = [
                self._timestamped(
                    "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
                ),
                self._timestamped("2026-06-01T00:01:00Z", self._complete("turn")),
            ]
            self._write_file(
                directory,
                "a-batch.jsonl",
                [
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        {"type": "session_meta", "source": "exec"},
                    ),
                    *common,
                ],
            )
            self._write_file(
                directory,
                "z-interactive.jsonl",
                [
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        {"type": "session_meta", "source": "cli"},
                    ),
                    *common,
                ],
            )

            result = scan_sessions(directory)

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 60.0)])
        self.assertEqual(result.diagnostics.excluded_batch_turns, 1)
        self.assertEqual(result.diagnostics.duplicate_turns, 0)

    def test_missing_or_invalid_top_level_timestamps_bound_recorded_fallback(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "not-an-iso-timestamp",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "also-invalid", self._start("turn", 1_780_272_000)
            ),
            self._timestamped(
                "still-invalid", self._complete("turn", duration_ms=7_200_000)
            ),
            self._start("missing", 1_780_272_001),
            self._complete("missing", duration_ms=7_200_000),
        )

        self.assertEqual(
            result.turns,
            [
                CompletedTurn(1_780_272_000, 1_800.0),
                CompletedTurn(1_780_272_001, 1_800.0),
            ],
        )
        self.assertEqual(result.diagnostics.event_timing_fallbacks, 2)

    def test_nested_timestamps_and_fake_markers_do_not_change_event_timing(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            self._timestamped(
                "2026-06-01T00:00:10Z",
                {
                    "type": "message",
                    "payload": {
                        "timestamp": "2026-06-02T00:00:00Z",
                        "type": "task_complete",
                        "turn_id": "fake",
                    },
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:20Z",
                {
                    "type": "function_call_output",
                    "output": {"timestamp": "2099-01-01T00:00:00Z"},
                },
            ),
            self._timestamped("2026-06-01T00:00:30Z", self._complete("turn")),
        )

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 30.0)])
        self.assertEqual(result.diagnostics.unmatched_completions, 0)

    def test_marker_text_in_malformed_message_and_tool_records_is_ignored(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "session_meta",
                    "payload": {"source": "cli"},
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            b'{"timestamp":"2026-06-01T00:00:10Z","type":"message",'
            b'"response_item":"task_complete",',
            b'{"timestamp":"2026-06-01T00:00:20Z",'
            b'"type":"function_call_output","output":"task_started",',
            b'{"timestamp":"2026-06-01T00:00:25Z","type":"message",'
            b'"output":"task_\\u005fcomplete",',
            self._timestamped("2026-06-01T00:00:30Z", self._complete("turn")),
        )

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 30.0)])
        self.assertEqual(result.diagnostics.malformed_lines, 0)

    def test_direct_task_and_source_fields_beat_nested_conflicts(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "session_meta",
                    "payload": {
                        "nested": {"source": "exec"},
                        "source": "cli",
                    },
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_started",
                        "nested": {
                            "turn_id": "fake",
                            "started_at": 1.0,
                        },
                        "turn_id": "real",
                        "started_at": 1_780_272_000,
                    },
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:30Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "nested": {
                            "turn_id": "fake",
                            "duration_ms": 99_000,
                        },
                        "turn_id": "real",
                    },
                },
            ),
        )

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 30.0)])
        self.assertEqual(result.diagnostics.excluded_batch_turns, 0)

    def test_nested_task_fields_without_direct_values_are_rejected(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "session_meta",
                    "source": {"exec": True},
                    "payload": {"nested": {"source": "exec"}},
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_started",
                        "nested": {
                            "turn_id": "fake",
                            "started_at": 1_780_272_000,
                        },
                    },
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:30Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "nested": {
                            "turn_id": "fake",
                            "duration_ms": 99_000,
                        },
                    },
                },
            ),
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 2)
        self.assertEqual(result.diagnostics.incomplete_turns, 0)
        self.assertEqual(
            result.diagnostics.unknown_sources,
            Counter({"unknown:missing": 1}),
        )

    def test_secret_payload_values_never_reach_json_scalar_decoder(self) -> None:
        records = [
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "session_meta",
                    "payload": {
                        "base_instructions": "TOP_SECRET_BASE_INSTRUCTIONS",
                        "source": "cli",
                    },
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            self._timestamped(
                "2026-06-01T00:00:30Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn",
                        "duration_ms": 1_000,
                        "last_agent_message": "TOP_SECRET_LAST_AGENT_MESSAGE",
                    },
                },
            ),
        ]
        scalar_inputs: list[bytes | str] = []
        original_loads = scanner_module.json.loads

        def spy_loads(value: bytes | str, *args: object, **kwargs: object) -> object:
            scalar_inputs.append(value)
            return original_loads(value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_file(directory, "real-shaped.jsonl", records)
            with patch.object(
                scanner_module.json, "loads", side_effect=spy_loads
            ):
                result = scan_sessions(directory)

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 30.0)])
        self.assertTrue(scalar_inputs)
        self.assertTrue(
            all(
                secret.encode("ascii") not in (
                    value if isinstance(value, bytes) else value.encode("utf-8")
                )
                for value in scalar_inputs
                for secret in (
                    "TOP_SECRET_BASE_INSTRUCTIONS",
                    "TOP_SECRET_LAST_AGENT_MESSAGE",
                )
            )
        )

    def test_oversized_direct_scalars_are_rejected_without_decoding(self) -> None:
        oversized_turn_id = "x" * 257
        oversized_number = "9" * 65
        records = [
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "payload": {"source": "cli"}},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_started",
                        "turn_id": oversized_turn_id,
                        "started_at": 1_780_272_000,
                    },
                },
            ),
            self._timestamped(
                "2026-06-01T00:00:30Z",
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn",
                        "duration_ms": oversized_number,
                    },
                },
            ),
        ]
        scalar_inputs: list[bytes | str] = []
        original_loads = scanner_module.json.loads

        def spy_loads(value: bytes | str, *args: object, **kwargs: object) -> object:
            scalar_inputs.append(value)
            return original_loads(value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_file(directory, "oversized.jsonl", records)
            with patch.object(
                scanner_module.json, "loads", side_effect=spy_loads
            ):
                result = scan_sessions(directory)

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 2)
        self.assertTrue(all(len(value) <= 64 for value in scalar_inputs))

    def test_candidate_followed_by_junk_is_malformed(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            b'{"timestamp":"2026-06-01T00:00:30Z",'
            b'"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"turn","duration_ms":1000}}junk',
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 1)
        self.assertEqual(result.diagnostics.incomplete_turns, 1)

    def test_mismatched_nested_delimiters_in_candidate_values_are_malformed(
        self,
    ) -> None:
        cases = (
            (
                b'"last_agent_message":{"items":[1}}}',
                "task-complete-last-message.jsonl",
            ),
            (
                b'"tool_output":{"items":[1}}}',
                "task-complete-tool-output.jsonl",
            ),
            (
                b'"base_instructions":{"items":[1}}}',
                "session-meta-base-instructions.jsonl",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for suffix, name in cases[:2]:
                self._write_file(
                    directory,
                    name,
                    [
                        self._timestamped(
                            "2026-06-01T00:00:00Z",
                            {"type": "session_meta", "source": "cli"},
                        ),
                        self._timestamped(
                            "2026-06-01T00:00:00Z",
                            self._start(name, 1_780_272_000),
                        ),
                        (
                            b'{"timestamp":"2026-06-01T00:00:30Z",'
                            b'"type":"event_msg","payload":{"type":"task_complete",'
                            b'"turn_id":'
                            + json.dumps(name).encode("utf-8")
                            + b',"duration_ms":1000,'
                            + suffix
                        ),
                    ],
                )
            suffix, name = cases[2]
            self._write_file(
                directory,
                name,
                [
                    (
                        b'{"timestamp":"2026-06-01T00:00:00Z",'
                        b'"type":"session_meta","payload":{"source":"cli",'
                        + suffix
                    ),
                ],
            )

            result = scan_sessions(directory)

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 3)

    def test_invalid_primitive_and_escape_in_ignored_values_are_malformed(self) -> None:
        invalid_primitive = (
            b'{"timestamp":"2026-06-01T00:00:30Z",'
            b'"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"primitive","duration_ms":1000,'
            b'"last_agent_message":undefined}}'
        )
        invalid_escape = (
            b'{"timestamp":"2026-06-01T00:00:30Z",'
            b'"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"escape","duration_ms":1000,'
            b'"last_agent_message":"bad\\q"}}'
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_file(
                directory,
                "invalid-values.jsonl",
                [
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        {"type": "session_meta", "source": "cli"},
                    ),
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        self._start("primitive", 1_780_272_000),
                    ),
                    invalid_primitive,
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        self._start("escape", 1_780_272_000),
                    ),
                    invalid_escape,
                ],
            )

            result = scan_sessions(directory)

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 2)
        self.assertEqual(result.diagnostics.incomplete_turns, 2)

    def test_candidate_depth_over_limit_is_malformed_without_recursion_error(self) -> None:
        depth = 256
        deeply_nested = (
            b'{"timestamp":"2026-06-01T00:00:30Z",'
            b'"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"deep","duration_ms":1000,'
            b'"last_agent_message":'
            + b"[" * depth
            + b"0"
            + b"]" * depth
            + b"}}"
        )
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("deep", 1_780_272_000)
            ),
            deeply_nested,
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 1)
        self.assertEqual(result.diagnostics.incomplete_turns, 1)

    def test_valid_mixed_nested_values_and_unicode_escapes_are_accepted(self) -> None:
        result, _ = self._scan(
            b'{"timestamp":"2026-06-01T00:00:00Z",'
            b'"type":"session_meta","payload":{"source":"cli",'
            b'"base_instructions":{"items":[{"text":"\\u263a"}]}}}',
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            b'{"timestamp":"2026-06-01T00:00:30Z",'
            b'"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"turn","duration_ms":1000,'
            b'"last_agent_message":{"items":[{"text":"line\\u0031\\n",'
            b'"flags":[true,false,null]}]},'
            b'"tool_output":{"values":[{"number":2.5e+1}]}}}',
        )

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 30.0)])

    def test_invalid_json_number_in_ignored_value_is_malformed(self) -> None:
        result, _ = self._scan(
            self._timestamped(
                "2026-06-01T00:00:00Z",
                {"type": "session_meta", "source": "cli"},
            ),
            self._timestamped(
                "2026-06-01T00:00:00Z", self._start("turn", 1_780_272_000)
            ),
            b'{"timestamp":"2026-06-01T00:00:30Z",'
            b'"type":"event_msg","payload":{"type":"task_complete",'
            b'"turn_id":"turn","duration_ms":1000,'
            b'"last_agent_message":{"value":01}}}',
        )

        self.assertEqual(result.turns, [])
        self.assertEqual(result.diagnostics.malformed_lines, 1)
        self.assertEqual(result.diagnostics.incomplete_turns, 1)

    def test_task_metadata_skips_large_ignored_value_once_per_pass(self) -> None:
        large_message = "x" * 100_000
        complete = self._timestamped(
            "2026-06-01T00:00:30Z",
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": large_message,
                    "turn_id": "turn",
                    "duration_ms": 1_000,
                },
            },
        )
        complete_line = json.dumps(complete).encode("utf-8")
        marker = b'"last_agent_message":'
        ignored_value_start = complete_line.index(marker) + len(marker)
        while complete_line[ignored_value_start] in b" \t":
            ignored_value_start += 1
        skip_calls = 0
        original_skip = scanner_module._skip_json_value

        def spy_skip(raw_line: bytes, cursor: int, depth: int = 0) -> int | None:
            nonlocal skip_calls
            if (
                raw_line == complete_line + b"\n"
                and cursor == ignored_value_start
                and depth == 0
            ):
                skip_calls += 1
            return original_skip(raw_line, cursor, depth)

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            self._write_file(
                directory,
                "large.jsonl",
                [
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        {"type": "session_meta", "source": "cli"},
                    ),
                    self._timestamped(
                        "2026-06-01T00:00:00Z",
                        self._start("turn", 1_780_272_000),
                    ),
                    complete_line,
                ],
            )
            with patch.object(
                scanner_module, "_skip_json_value", side_effect=spy_skip
            ):
                result = scan_sessions(directory)

        self.assertEqual(result.turns, [CompletedTurn(1_780_272_000, 30.0)])
        self.assertEqual(skip_calls, 1)


if __name__ == "__main__":
    unittest.main()
