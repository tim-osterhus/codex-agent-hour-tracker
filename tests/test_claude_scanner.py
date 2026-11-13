import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent_hour_tracker.claude_scanner import _scan_file
from agent_hour_tracker.scanner import ScanDiagnostics


class ClaudeScannerTests(unittest.TestCase):
    def event(self, **changes):
        event = {
            "type": "system", "subtype": "turn_duration", "durationMs": 2500,
            "timestamp": "2026-11-12T12:00:03Z",
            "uuid": "00000000-0000-4000-8000-000000000001",
            "sessionId": "00000000-0000-4000-8000-000000000002",
            "isSidechain": False,
        }
        return event | changes

    def scan(self, *events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_bytes(b"\n".join(
                e if isinstance(e, bytes) else json.dumps(e).encode()
                for e in events
            ) + b"\n")
            diagnostics = ScanDiagnostics()
            return _scan_file(path, diagnostics), diagnostics

    def test_native_duration_and_unknown_human_origin(self):
        observations, diagnostics = self.scan(self.event())
        source, parsed = observations[0]
        self.assertEqual(source, "unknown")
        self.assertEqual(parsed.turn.provider, "claude")
        self.assertEqual(parsed.turn.duration_seconds, 2.5)
        self.assertEqual(parsed.turn.started_at,
                         datetime.fromisoformat("2026-11-12T12:00:00.500+00:00").timestamp())
        self.assertTrue(parsed.event_timing_usable)
        self.assertEqual(diagnostics.unknown_sources["claude:unverified-main-origin"], 1)

    def test_sidechain_native_timer_is_excluded_to_avoid_summary_double_count(self):
        observations, diagnostics = self.scan(self.event(isSidechain=True))
        self.assertFalse(observations)
        self.assertEqual(diagnostics.unknown_sources["claude:ignored-sidechain-duration"], 1)

    def test_missing_sidechain_never_becomes_human_root(self):
        event = self.event()
        del event["isSidechain"]
        self.assertEqual(self.scan(event)[0][0][0], "unknown")

    def test_forks_and_physical_copies_share_immutable_identity(self):
        a, _ = self.scan(self.event())
        b, _ = self.scan(self.event(sessionId="00000000-0000-4000-8000-000000000003"))
        self.assertEqual(a[0][1].turn_id, b[0][1].turn_id)
        c, _ = self.scan(self.event(uuid="00000000-0000-4000-8000-000000000004"))
        self.assertNotEqual(a[0][1].turn_id, c[0][1].turn_id)

    def test_no_native_timer_does_not_estimate_from_messages(self):
        observations, diagnostics = self.scan(
            {"type": "user", "timestamp": "2026-11-12T00:00:00Z", "message": "private"},
            {"type": "assistant", "timestamp": "2026-11-13T00:00:00Z", "message": "private"},
            {"type": "result", "duration_ms": 1000},
        )
        self.assertFalse(observations)
        self.assertEqual(diagnostics.unknown_sources["claude:no-native-duration"], 1)
        self.assertEqual(diagnostics.incomplete_turns, 0)

    def test_native_duration_is_not_capped_by_codex_idle_heuristic(self):
        observations, _ = self.scan(self.event(durationMs=7_200_000))
        self.assertEqual(observations[0][1].turn.duration_seconds, 7200)

    def test_bad_native_records_are_diagnosed(self):
        for changes in [
            {"durationMs": -1}, {"durationMs": True}, {"durationMs": "1000"},
            {"durationMs": float("inf")}, {"durationMs": []},
            {"timestamp": "not-a-date"}, {"timestamp": "2026-11-12T12:00:00"},
            {"uuid": ""}, {"uuid": "private"}, {"isSidechain": "false"},
        ]:
            with self.subTest(changes=changes):
                observations, diagnostics = self.scan(self.event(**changes))
                self.assertFalse(observations)
                self.assertEqual(diagnostics.malformed_lines, 1)

    def test_duplicate_metadata_and_trailing_garbage_are_rejected(self):
        raw = json.dumps(self.event()).encode()
        for malformed in [raw[:-1] + b',"durationMs":1000}',
                          raw[:-1] + b',"type":"user"}', raw + b' trailing']:
            observations, diagnostics = self.scan(malformed)
            self.assertFalse(observations)
            self.assertEqual(diagnostics.malformed_lines, 1)

    def test_only_bounded_metadata_scalars_are_decoded(self):
        private = "PRIVATE_CONVERSATION_SENTINEL" * 100_000
        event = self.event(message={"content": [private]},
                           toolUseResult={"text": private},
                           reasoning=private,
                           payload={"durationMs": 999999, "uuid": "private"})
        original = json.loads
        decoded = []

        def bounded_only(value, *args, **kwargs):
            self.assertLessEqual(len(value), 64)
            self.assertNotIn(b"PRIVATE", value)
            decoded.append(value)
            return original(value, *args, **kwargs)

        with patch("agent_hour_tracker.scanner.json.loads", side_effect=bounded_only):
            observations, diagnostics = self.scan(event)
        self.assertEqual(len(observations), 1)
        self.assertEqual(len(decoded), 6)
        self.assertEqual(diagnostics.malformed_lines, 0)

    def test_nested_fake_timer_and_metadata_are_ignored(self):
        observations, diagnostics = self.scan(
            {"type": "assistant", "message": self.event()},
            {"type": "system", "subtype": "other", "payload": self.event()},
        )
        self.assertFalse(observations)
        self.assertEqual(diagnostics.unknown_sources["claude:no-native-duration"], 1)

    def agent_result(self, **changes):
        return {
            "type": "user", "timestamp": "2026-11-12T12:00:03Z",
            "uuid": "00000000-0000-4000-8000-000000000003",
            "toolUseResult": {"status": "completed", "agentId": "a0123456789abcdef",
                              "totalDurationMs": 1500},
        } | changes

    def test_completed_foreground_run_has_exact_delegated_duration(self):
        observations, diagnostics = self.scan(self.agent_result())
        source, parsed = observations[0]
        self.assertEqual(source, "delegated")
        self.assertEqual(parsed.turn.duration_seconds, 1.5)
        self.assertEqual(parsed.turn.provider, "claude")
        self.assertEqual(parsed.turn.started_at,
                         datetime.fromisoformat("2026-11-12T12:00:01.500+00:00").timestamp())
        self.assertEqual(diagnostics.unknown_sources["claude:no-native-duration"], 1)

    def test_parent_main_plus_summary_plus_child_native_counts_one_worker(self):
        observations, diagnostics = self.scan(
            self.event(), self.agent_result(), self.event(isSidechain=True),
        )
        self.assertEqual([source for source, _ in observations], ["unknown", "delegated"])
        self.assertEqual(sum(parsed.turn.duration_seconds for _, parsed in observations), 4)
        self.assertEqual(diagnostics.unknown_sources["claude:ignored-sidechain-duration"], 1)

    def test_async_launch_and_generic_tool_duration_are_not_worker_runtime(self):
        observations, diagnostics = self.scan(
            self.agent_result(toolUseResult={"status": "async_launched", "isAsync": True,
                                            "agentId": "a0123456789abcdef"}),
            self.agent_result(toolUseResult={"durationMs": 99000, "stdout": "private"}),
            self.agent_result(toolUseResult={"status": "completed", "totalDurationMs": 99000}),
        )
        self.assertFalse(observations)
        self.assertEqual(diagnostics.unknown_sources["claude:async-agent-unmeasured"], 1)

    def test_resumed_agent_completion_is_distinct_but_copies_are_identical(self):
        first, _ = self.scan(self.agent_result())
        copy, _ = self.scan(self.agent_result(sessionId="another-fork"))
        resumed, _ = self.scan(self.agent_result(uuid="00000000-0000-4000-8000-000000000004"))
        self.assertEqual(first[0][1].turn_id, copy[0][1].turn_id)
        self.assertNotEqual(first[0][1].turn_id, resumed[0][1].turn_id)

    def test_foreground_telemetry_skips_private_result_content(self):
        result = {"status": "completed", "agentId": "a0123456789abcdef",
                  "totalDurationMs": 1500, "prompt": "PRIVATE" * 10000,
                  "content": [{"type": "text", "text": "PRIVATE" * 10000}]}
        original = json.loads

        def bounded_only(value, *args, **kwargs):
            self.assertLessEqual(len(value), 128)
            self.assertNotIn(b"PRIVATE", value)
            return original(value, *args, **kwargs)

        with patch("agent_hour_tracker.scanner.json.loads", side_effect=bounded_only):
            observations, diagnostics = self.scan(self.agent_result(toolUseResult=result))
        self.assertEqual(len(observations), 1)
        self.assertEqual(diagnostics.malformed_lines, 0)

    def test_child_path_missing_or_false_flag_cannot_duplicate_parent_summary(self):
        for filename in ["subagents/agent-a0123456789abcdef.jsonl",
                         "agent-a0123456789abcdef.jsonl"]:
            for flag in [None, False]:
                with self.subTest(filename=filename, flag=flag):
                    with tempfile.TemporaryDirectory() as directory:
                        parent = Path(directory) / "parent.jsonl"
                        child = Path(directory) / filename
                        child.parent.mkdir(parents=True, exist_ok=True)
                        event = self.event()
                        if flag is None:
                            del event["isSidechain"]
                        else:
                            event["isSidechain"] = flag
                        parent.write_text(json.dumps(self.agent_result()) + "\n")
                        child.write_text(json.dumps(event) + "\n")
                        diagnostics = ScanDiagnostics()
                        observations = (_scan_file(parent, diagnostics)
                                        + _scan_file(child, diagnostics))
                        self.assertEqual(len(observations), 1)
                        self.assertEqual(observations[0][0], "delegated")
                        self.assertEqual(observations[0][1].turn.duration_seconds, 1.5)
                        self.assertEqual(diagnostics.unknown_sources[
                            "claude:ignored-sidechain-duration"], 1)

    def test_duplicate_foreground_object_is_rejected(self):
        raw = json.dumps(self.agent_result()).encode()
        observations, diagnostics = self.scan(raw[:-1] + b',"toolUseResult":{}}')
        self.assertFalse(observations)
        self.assertEqual(diagnostics.malformed_lines, 1)

    def test_bad_completed_telemetry_is_diagnosed(self):
        for changes in [{"totalDurationMs": -1}, {"totalDurationMs": True},
                        {"totalDurationMs": []}, {"totalDurationMs": "1500"}]:
            result = self.agent_result()["toolUseResult"] | changes
            observations, diagnostics = self.scan(self.agent_result(toolUseResult=result))
            self.assertFalse(observations)
            self.assertEqual(diagnostics.malformed_lines, 1)

    def test_open_error_is_diagnosed(self):
        diagnostics = ScanDiagnostics()
        self.assertEqual(_scan_file(Path("/nonexistent/claude-session.jsonl"), diagnostics), [])
        self.assertEqual(diagnostics.open_errors, 1)


if __name__ == "__main__":
    unittest.main()
