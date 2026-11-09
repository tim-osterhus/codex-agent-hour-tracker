"""Synthetic compressed rollout coverage; never needs a user's archive."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from compression import zstd
except ImportError:
    from backports import zstd

from agent_hour_tracker.archive import collect_archive, merge_archives
from agent_hour_tracker.cli import main
from agent_hour_tracker.scanner import CompletedTurn, scan_sessions


def rollout(turn_id="one", duration_ms=1000, source="cli"):
    return b"\n".join(json.dumps(record).encode() for record in (
        {"type": "session_meta", "source": source},
        {"type": "task_started", "turn_id": turn_id, "started_at": 100},
        {"type": "task_complete", "turn_id": turn_id, "duration_ms": duration_ms},
    )) + b"\n"


class CompressedSessionTests(unittest.TestCase):
    def test_scan_export_and_merge_match_plain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plain = root / "plain"
            compressed = root / "compressed"
            plain.mkdir()
            compressed.mkdir()
            for name, content in (
                ("root", rollout()),
                ("delegated", rollout(duration_ms=2000, source={"subagent": {}})),
                ("batch", rollout(duration_ms=9000, source="exec")),
            ):
                (plain / f"{name}.jsonl").write_bytes(content)
                (compressed / f"{name}.jsonl.zst").write_bytes(zstd.compress(content))
            expected = scan_sessions(plain)
            actual = scan_sessions(compressed)
            self.assertEqual(actual, expected)
            archive = collect_archive([compressed])
            self.assertEqual(archive, collect_archive([plain]))
            merged = merge_archives([archive, collect_archive([plain])])
            self.assertEqual(merged.turns, expected.turns)
            self.assertEqual(merged.root_turns, expected.root_turns)
            self.assertEqual(actual.turns, [CompletedTurn(100, 2)])
            self.assertEqual(actual.root_turns, [CompletedTurn(100, 1)])

    def test_plain_twin_is_preferred_in_scan_and_explicit_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plain = root / "rollout.jsonl"
            compressed = root / "rollout.jsonl.zst"
            plain.write_bytes(rollout())
            compressed.write_bytes(zstd.compress(rollout(duration_ms=9000)))
            result = scan_sessions(root)
            self.assertEqual(result.turns, [CompletedTurn(100, 1)])
            self.assertEqual(result.diagnostics.files_scanned, 1)
            for paths in ([root], [compressed], [compressed, plain, root]):
                archive = collect_archive(paths)
                self.assertEqual(archive["diagnostics"]["files_scanned"], 1)
                self.assertEqual(archive["observations"][0]["duration_seconds"], 1)

    def test_invalid_and_truncated_streams_are_diagnosed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for content in (
                b"not zstd", zstd.compress(rollout())[:-1], b"",
                zstd.compress(rollout()) + b"garbage",
            ):
                with self.subTest(content_length=len(content)):
                    path = root / "broken.jsonl.zst"
                    path.write_bytes(content)
                    result = scan_sessions(root)
                    self.assertEqual(result.diagnostics.decompression_errors, 1)
                    self.assertEqual(result.diagnostics.malformed_files, {path})
                    archive = collect_archive([path])
                    self.assertEqual(archive["diagnostics"]["decompression_errors"], 1)
                    self.assertEqual(merge_archives([archive]).diagnostics.decompression_errors, 1)

    def test_missing_backends_do_not_break_plain_scans_or_cli_imports(self):
        # A fresh interpreter tests module initialization, not just a patched
        # backend after the scanner has already imported successfully.
        script = """
import builtins
import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import patch

original_import = builtins.__import__
def without_zstd(name, *args, **kwargs):
    if name in {"compression", "compression.zstd", "backports", "backports.zstd"}:
        raise ImportError("synthetic unavailable backend")
    return original_import(name, *args, **kwargs)

with patch("builtins.__import__", side_effect=without_zstd):
    from agent_hour_tracker.archive import collect_archive
    from agent_hour_tracker.cli import main
    from agent_hour_tracker.scanner import CompletedTurn, scan_sessions

root = Path(sys.argv[1])
plain = root / "plain"
plain.mkdir()
(root / "rollout.jsonl").rename(plain / "rollout.jsonl")
assert scan_sessions(plain).turns == [CompletedTurn(100, 1)]
(root / "PRIVATE_PATH.jsonl.zst").write_bytes(b"PRIVATE_RAW_INVALID_STREAM")
result = scan_sessions(root)
assert result.turns == [CompletedTurn(100, 1)]
assert result.diagnostics.files_scanned == 2
assert result.diagnostics.decompression_errors == 1
assert len(result.diagnostics.malformed_files) == 1
archive = collect_archive([root])
assert archive["diagnostics"]["decompression_errors"] == 1
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    assert main(["--sessions-dir", str(root), "--timezone", "UTC", "--share"]) == 0
assert "Decompression errors: 1" in stderr.getvalue()
assert "PRIVATE_" not in stdout.getvalue() + stderr.getvalue()
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rollout.jsonl").write_bytes(rollout())
            process = subprocess.run(
                [sys.executable, "-c", script, str(root)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout, "")

    def test_partial_stream_preserves_completed_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "partial.jsonl.zst"
            path.write_bytes(zstd.compress(rollout()) + zstd.compress(b"unfinished")[:-1])
            result = scan_sessions(root)
            self.assertEqual(result.turns, [CompletedTurn(100, 1)])
            self.assertEqual(result.diagnostics.decompression_errors, 1)

    def test_cli_reports_stream_errors_without_raw_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "PRIVATE_PATH.jsonl.zst").write_bytes(b"PRIVATE_RAW_INVALID_STREAM")
            for mode in (["--share"], ["--export", str(root / "export.json")]):
                with self.subTest(mode=mode[0]):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        status = main(["--sessions-dir", str(root), "--timezone", "UTC", *mode])
                    self.assertEqual(status, 0)
                    self.assertIn("Decompression errors: 1", stderr.getvalue())
                    self.assertNotIn("PRIVATE_", stdout.getvalue() + stderr.getvalue())

    def test_concatenated_frames_and_large_ignored_payloads(self):
        secret = b"PRIVATE_TRANSCRIPT_REASONING_TOOL_CONTENT"
        transcript = b'{"type":"response_item","payload":{"content":"' + secret * 100000 + b'"}}\n'
        data = rollout() + transcript
        original_loads = json.loads
        decoded_sizes = []

        def bounded_loads(value, *args, **kwargs):
            self.assertNotIn(secret, value if isinstance(value, bytes) else value.encode())
            self.assertLessEqual(len(value), 4096)
            decoded_sizes.append(len(value))
            return original_loads(value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "rollout.jsonl.zst"
            split = 13  # frame boundary may fall in the middle of a JSON record
            path.write_bytes(zstd.compress(data[:split]) + zstd.compress(data[split:]))
            with patch("agent_hour_tracker.scanner.json.loads", side_effect=bounded_loads):
                result = scan_sessions(root)
                archive = collect_archive([path])
            self.assertEqual(result.turns, [CompletedTurn(100, 1)])
            self.assertEqual(result.diagnostics.malformed_files, set())
            self.assertTrue(decoded_sizes)
            self.assertNotIn(secret.decode(), json.dumps(archive))


if __name__ == "__main__":
    unittest.main()
