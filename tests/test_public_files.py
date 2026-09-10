from __future__ import annotations

import os
import platform
import re
import socket
import string
import tomllib
import unittest
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "codex-agent-hour-tracker"
SITE_ROOT = PROJECT_ROOT / "site"
SYNTHETIC_PRIVATE_MARKERS = (
    "synthetic-private-user",
    "synthetic-private-host",
    "synthetic-private.example",
)


def _local_identifier_markers() -> tuple[str, ...]:
    home = Path.home()
    candidates = (
        str(home),
        home.name,
        os.environ.get("USER", ""),
        os.environ.get("USERNAME", ""),
        platform.node(),
        socket.gethostname(),
    )
    return tuple(
        sorted(
            {
                candidate.strip().casefold()
                for candidate in candidates
                if candidate.strip() and len(candidate.strip()) >= 3
            }
        )
    )


def _absolute_path_pattern() -> re.Pattern[str]:
    posix_root_markers = tuple("/" + root_name + "/" for root_name in ("Users", "home", "tmp"))
    windows_drive_marker = (
        f"[{string.ascii_letters}]" + re.escape(":") + re.escape("\\")
    )
    return re.compile(
        "(?:"
        + "|".join(
            [*(re.escape(marker) for marker in posix_root_markers), windows_drive_marker]
        )
        + ")",
        re.IGNORECASE,
    )


def _assert_public_text_has_no_local_identifiers(
    test_case: unittest.TestCase, documents: dict[str, str]
) -> None:
    for name, content in documents.items():
        lowered_content = content.casefold()
        with test_case.subTest(document=name):
            for marker in _local_identifier_markers():
                test_case.assertNotIn(marker, lowered_content)


def _assert_public_text_has_no_synthetic_markers(
    test_case: unittest.TestCase, documents: dict[str, str]
) -> None:
    for name, content in documents.items():
        lowered_content = content.casefold()
        with test_case.subTest(document=name):
            for marker in SYNTHETIC_PRIVATE_MARKERS:
                test_case.assertNotIn(marker, lowered_content)


def _assert_public_text_has_no_absolute_paths(
    test_case: unittest.TestCase, documents: dict[str, str]
) -> None:
    absolute_path = _absolute_path_pattern()
    for name, content in documents.items():
        with test_case.subTest(document=name):
            test_case.assertIsNone(absolute_path.search(content))


class _SiteHTMLParser(HTMLParser):
    """Collect the small amount of structure needed by the public-site contract."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text_parts: list[str] = []
        self._stack: list[str] = []
        self.text_by_tag: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))
        self._stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._stack:
            self._stack = self._stack[: len(self._stack) - 1 - self._stack[::-1].index(tag)]

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        for tag in self._stack:
            self.text_by_tag.setdefault(tag, []).append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())

    def tags_named(self, tag_name: str) -> list[dict[str, str]]:
        return [attrs for tag, attrs in self.tags if tag == tag_name]

    def text_for(self, tag_name: str) -> str:
        return " ".join(" ".join(self.text_by_tag.get(tag_name, [])).split())


def _site_html() -> tuple[str, _SiteHTMLParser]:
    path = SITE_ROOT / "index.html"
    source = path.read_text(encoding="utf-8") if path.is_file() else ""
    parser = _SiteHTMLParser()
    parser.feed(source)
    return source, parser


def _css_variable(css: str, name: str) -> str:
    match = re.search(rf"--{name}\s*:\s*(#[0-9a-fA-F]{{6}})", css)
    if match is None:
        raise AssertionError(f"missing CSS variable --{name}")
    return match.group(1)


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.03928
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    foreground_luminance = _relative_luminance(foreground)
    background_luminance = _relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _line_contains_tokens(text: str, *tokens: str) -> bool:
    """Return whether one line states every required contract token."""
    lowered_tokens = tuple(token.lower() for token in tokens)
    return any(
        all(token in line.lower() for token in lowered_tokens)
        for line in text.splitlines()
    )


class PublicFilesTests(unittest.TestCase):
    """Public boundaries and compatibility, without enforcing a fixed page layout."""

    def test_public_docs_have_no_paths_or_local_identifiers(self) -> None:
        paths = [PROJECT_ROOT / name for name in ("README.md", "SECURITY.md", "CHANGELOG.md")]
        paths += list((PROJECT_ROOT / "docs").glob("*.md"))
        documents = {str(path.relative_to(PROJECT_ROOT)): path.read_text(encoding="utf-8") for path in paths}
        _assert_public_text_has_no_absolute_paths(self, documents)
        _assert_public_text_has_no_synthetic_markers(self, documents)
        _assert_public_text_has_no_local_identifiers(self, documents)

    def test_readme_documents_supported_workflows(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for value in ("uvx codex-agent-hour-tracker --share", "uv tool install",
                      "pipx install", "--merge", "--export", "--include-exec",
                      "--human-hours", "--human-hours-per-week", "--monthly",
                      "--share --format json", "docs/exports.md", "SECURITY.md", "LICENSE"):
            self.assertIn(value, readme)
        self.assertLess(readme.index("uvx codex-agent-hour-tracker --share"), readme.index("uv tool install"))

    def test_docs_distinguish_private_and_public_exports(self) -> None:
        docs = (PROJECT_ROOT / "docs/exports.md").read_text(encoding="utf-8")
        self.assertIn("agent-hours-score", docs)
        self.assertIn("agent-hours-archive", docs)
        self.assertIn("self-reported", docs)
        self.assertIn("Hashing does not make this public-safe", docs)
        security = (PROJECT_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("private vulnerability", security)
        self.assertIn("GitHub Security Advisories", security)
        self.assertIn("synthetic", security)

    def test_skill_supports_safe_merge_and_canonical_json(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for token in ("--merge", "--export", "--include-exec", "--share --format json",
                      "30 most recent completed local calendar days", "Never transfer raw session data"):
            self.assertIn(token, skill)

    def test_versions_scripts_and_package_data(self) -> None:
        from agent_hour_tracker import __version__
        from agent_hour_tracker.benchmarks import load_benchmarks
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            metadata = tomllib.load(handle)
        self.assertEqual(metadata["project"]["version"], __version__)
        self.assertEqual(__version__, "0.2.0")
        for alias in ("agent-hours", "codex-agent-hour-tracker"):
            self.assertEqual(metadata["project"]["scripts"][alias], "agent_hour_tracker.cli:main")
        self.assertIn("/CHANGELOG.md", metadata["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])
        registry = load_benchmarks()
        self.assertEqual(len(registry["benchmarks"]), 2)
        self.assertEqual(registry["benchmarks"][0]["value"], 3.1)
        self.assertEqual(registry["benchmarks"][0]["unit"], "agent_hours_per_human_hour")

    def test_generated_benchmark_data_is_current(self) -> None:
        import subprocess
        import sys
        subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts/sync_benchmarks.py"), "--check"], check=True)

    def test_gitignore_covers_private_artifacts(self) -> None:
        content = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        for rule in ("/reports/", "*.agent-hours-private.json", "/dist/", "/build/", "/docs/superpowers/", ".env", ".venv/"):
            self.assertIn(rule, content)


class PublicSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index, self.parser = _site_html()
        self.css = (SITE_ROOT / "styles.css").read_text(encoding="utf-8")
        self.headers = (SITE_ROOT / "_headers").read_text(encoding="utf-8")

    def test_landmarks_and_public_actions(self) -> None:
        self.assertEqual(len(self.parser.tags_named("h1")), 1)
        self.assertEqual(len(self.parser.tags_named("main")), 1)
        self.assertTrue(self.parser.tags_named("header"))
        self.assertTrue(self.parser.tags_named("footer"))
        self.assertIn("uvx codex-agent-hour-tracker --share", self.index)
        self.assertTrue(self.parser.tags_named("button"))
        self.assertTrue((SITE_ROOT / "benchmarks/index.html").is_file())

    def test_site_has_no_remote_scripts_or_submission_surfaces(self) -> None:
        for path in SITE_ROOT.rglob("*.html"):
            parser = _SiteHTMLParser()
            parser.feed(path.read_text(encoding="utf-8"))
            self.assertFalse(parser.tags_named("iframe"))
            for attrs in parser.tags_named("script"):
                self.assertTrue(attrs.get("src"), path)
                self.assertNotRegex(attrs["src"], r"^(?:https?:)?//")
            for attrs in parser.tags_named("form"):
                self.assertFalse(attrs.get("action"))
        for path in (*SITE_ROOT.rglob("*.js"), *SITE_ROOT.rglob("*.mjs")):
            content = path.read_text(encoding="utf-8")
            self.assertNotRegex(content, r"fetch\s*\(|localStorage|sessionStorage|indexedDB|XMLHttpRequest")
        self.assertIn("connect-src 'none'", self.headers)
        self.assertIn("form-action 'none'", self.headers)
        self.assertIn("frame-ancestors 'none'", self.headers)
        self.assertIn("object-src 'none'", self.headers)
        self.assertIn("script-src 'self'", self.headers)
        self.assertNotIn("'unsafe-eval'", self.headers)

    def test_site_has_no_private_identifiers(self) -> None:
        documents = {
            str(path.relative_to(PROJECT_ROOT)): path.read_text(encoding="utf-8")
            for path in SITE_ROOT.rglob("*")
            if path.is_file() and path.suffix in (".html", ".css", ".js", ".mjs", ".json", ".svg")
        }
        _assert_public_text_has_no_absolute_paths(self, documents)
        _assert_public_text_has_no_synthetic_markers(self, documents)
        _assert_public_text_has_no_local_identifiers(self, documents)

    def test_responsive_keyboard_and_reduced_motion_styles(self) -> None:
        self.assertRegex(self.css, r"@media[^}]+max-width")
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn("focus-visible", self.css)

    def test_text_and_focus_colors_have_contrast_on_each_surface(self) -> None:
        dark = _css_variable(self.css, "bg")
        paper = _css_variable(self.css, "paper")
        for name in ("paper", "muted", "coral"):
            self.assertGreaterEqual(_contrast_ratio(_css_variable(self.css, name), dark), 4.5)
        light_rules = self.css[self.css.index(".light-section {"):]
        for name in ("muted", "coral"):
            self.assertGreaterEqual(_contrast_ratio(_css_variable(light_rules, name), paper), 4.5)

    def test_links_include_project_and_benchmark_sources(self) -> None:
        html = "\n".join(path.read_text(encoding="utf-8") for path in SITE_ROOT.rglob("*.html"))
        for url in ("https://github.com/tim-osterhus/codex-agent-hour-tracker",
                    "https://pypi.org/project/codex-agent-hour-tracker/",
                    "https://openai.com/index/research-acceleration-view-inside-openai/",
                    "https://openai.com/index/how-agents-are-transforming-work/"):
            self.assertIn(url, html)
