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
    def setUp(self) -> None:
        self.readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    def test_readme_leads_with_literal_definition_and_share_action(self) -> None:
        lines = self.readme.splitlines()
        self.assertEqual(lines[0], "# Agent Hour Tracker")
        definition = next(line for line in lines[1:] if line.strip())
        self.assertRegex(definition, r"^Agent Hour Tracker is .+\.$")
        self.assertIn("local", definition.lower())
        self.assertIn("codex", definition.lower())
        self.assertIn("agent-hour", definition.lower())
        self.assertEqual(definition.count("."), 1)
        share_action = "uvx codex-agent-hour-tracker --share"
        self.assertIn(share_action, self.readme)
        self.assertLess(self.readme.index(share_action), self.readme.index("uv tool install"))

    def test_readme_documents_synthetic_score_and_cumulative_hours(self) -> None:
        self.assertRegex(self.readme, r"(?i)synthetic[^\n]*archive score")
        self.assertRegex(
            self.readme,
            r"(?is)overlap.{0,160}independently|independently.{0,160}overlap",
        )
        self.assertRegex(self.readme, r"(?i)cumulative.{0,120}agent-hours")

    def test_readme_has_installation_report_privacy_and_deeper_links(self) -> None:
        for required in (
            r"uv tool install\s+codex-agent-hour-tracker",
            r"pipx install\s+codex-agent-hour-tracker",
            r"--format\s+csv",
            r"reports/",
            r"--share",
            r"sanitized\s+aggregate",
            r"safe default.{0,20}sharing",
            r"semi-sensitive.{0,30}activity patterns",
            r"deliberate aggregate disclosure",
            r"docs/methodology\.md",
            r"SECURITY\.md",
            r"LICENSE",
            r"skills/codex-agent-hour-tracker/",
        ):
            with self.subTest(required=required):
                self.assertRegex(self.readme, required)

    def test_custom_report_examples_create_ignored_output_directory(self) -> None:
        setup_index = self.readme.index("mkdir -p reports")
        for redirect in (
            "> reports/january-summary.txt",
            "> reports/january-summary.csv",
        ):
            with self.subTest(redirect=redirect):
                self.assertLess(setup_index, self.readme.index(redirect))

    def test_development_command_uses_uv_run(self) -> None:
        self.assertRegex(
            self.readme,
            r"uv run python -m unittest discover -s tests -v",
        )
        self.assertNotRegex(
            self.readme,
            r"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest",
        )

    def test_skill_link_uses_approved_repository_path(self) -> None:
        self.assertIn(
            "https://github.com/tim-osterhus/codex-agent-hour-tracker/tree/main/skills/codex-agent-hour-tracker/",
            self.readme,
        )
        self.assertNotIn("tree/main/skill)", self.readme)

    def test_readme_states_exact_default_window_and_explicit_ranges(self) -> None:
        self.assertRegex(
            self.readme,
            r"(?is)30 most recent completed local calendar days.{0,180}ending yesterday",
        )
        self.assertRegex(
            self.readme,
            r"(?is)(January 30.{0,80}February 28|Jan(?:uary)? 30.{0,80}Feb(?:ruary)? 28)",
        )
        self.assertRegex(self.readme, r"(?is)explicit `--start`.{0,160}`--end`")
        self.assertNotRegex(self.readme, r"(?i)default[^.\n]{0,100}2026-06-01")

    def test_public_methodology_and_security_docs_cover_required_boundaries(self) -> None:
        methodology_path = PROJECT_ROOT / "docs" / "methodology.md"
        security_path = PROJECT_ROOT / "SECURITY.md"
        self.assertTrue(methodology_path.is_file())
        self.assertTrue(security_path.is_file())
        methodology = (
            methodology_path.read_text(encoding="utf-8")
            if methodology_path.is_file()
            else ""
        )
        security = (
            security_path.read_text(encoding="utf-8")
            if security_path.is_file()
            else ""
        )

        for required in (
            r"30 minutes",
            r"fallback",
            r"start date",
            r"batch",
            r"deduplic",
            r"event[- ]timing.{0,50}(?:usable|usability)|(?:usable|usability).{0,50}event[- ]timing",
            r"zero-use",
            r"Archive Score",
            r"diagnostic",
            r"limitation",
        ):
            with self.subTest(document="methodology", required=required):
                self.assertRegex(methodology, rf"(?i){required}")

        for required in (
            r"latest.{0,20}version",
            r"GitHub Security Advisories",
            r"private vulnerability",
            r"session files",
            r"transcripts",
            r"full.{0,20}generated reports",
            r"bounded aggregate",
            r"intentional disclosure",
        ):
            with self.subTest(document="security", required=required):
                self.assertRegex(security, rf"(?i){required}")

    def test_public_docs_have_no_absolute_paths_or_private_markers(self) -> None:
        documents = {
            "README.md": self.readme,
            "SECURITY.md": (
                (PROJECT_ROOT / "SECURITY.md").read_text(encoding="utf-8")
                if (PROJECT_ROOT / "SECURITY.md").is_file()
                else ""
            ),
            "docs/methodology.md": (
                (PROJECT_ROOT / "docs" / "methodology.md").read_text(
                    encoding="utf-8"
                )
                if (PROJECT_ROOT / "docs" / "methodology.md").is_file()
                else ""
            ),
        }
        for name, content in documents.items():
            with self.subTest(document=name):
                self.assertNotRegex(content, r"(?i)session[_ -]?dump")
        _assert_public_text_has_no_absolute_paths(self, documents)
        _assert_public_text_has_no_synthetic_markers(self, documents)
        _assert_public_text_has_no_local_identifiers(self, documents)


class PublicSiteTests(unittest.TestCase):
    """Static checks for the intentionally dependency-free landing page."""

    def setUp(self) -> None:
        self.index, self.parser = _site_html()
        self.css = (
            (SITE_ROOT / "styles.css").read_text(encoding="utf-8")
            if (SITE_ROOT / "styles.css").is_file()
            else ""
        )
        self.js = (
            (SITE_ROOT / "app.js").read_text(encoding="utf-8")
            if (SITE_ROOT / "app.js").is_file()
            else ""
        )
        self.headers = (
            (SITE_ROOT / "_headers").read_text(encoding="utf-8")
            if (SITE_ROOT / "_headers").is_file()
            else ""
        )

    def test_static_site_files_exist(self) -> None:
        for filename in ("index.html", "styles.css", "app.js", "_headers"):
            with self.subTest(filename=filename):
                self.assertTrue((SITE_ROOT / filename).is_file())

    def test_html_has_required_regions_and_one_exact_h1(self) -> None:
        self.assertTrue(
            any(
                attrs.get("class") == "project-header"
                for attrs in self.parser.tags_named("header")
            )
        )
        self.assertEqual(len(self.parser.tags_named("main")), 1)
        section_ids = {
            attrs.get("id")
            for attrs in self.parser.tags_named("section")
        }
        self.assertTrue(
            {
                "challenge",
                "meaning",
                "run-it",
                "privacy",
                "methodology",
                "share",
            }.issubset(section_ids)
        )
        self.assertEqual(len(self.parser.tags_named("footer")), 1)
        headings = self.parser.tags_named("h1")
        self.assertEqual(len(headings), 1)
        self.assertEqual(
            self.parser.text_for("h1"),
            "How many hours a day does your Codex work?",
        )

    def test_above_fold_action_has_command_copy_button_and_subordinate_github(self) -> None:
        command = "uvx codex-agent-hour-tracker --share"
        self.assertIn(command, self.index)
        copy_buttons = [
            attrs
            for attrs in self.parser.tags_named("button")
            if attrs.get("data-copy-command") == command
        ]
        self.assertEqual(len(copy_buttons), 1)
        self.assertEqual(copy_buttons[0].get("type"), "button")
        self.assertIn("Copy", self.parser.text_for("button"))
        github_links = [
            attrs
            for attrs in self.parser.tags_named("a")
            if attrs.get("href") == "https://github.com/tim-osterhus/codex-agent-hour-tracker"
        ]
        self.assertEqual(len(github_links), 1)
        self.assertIn("GitHub", self.parser.text)
        self.assertLess(self.index.find(command), self.index.find("github.com"))

    def test_synthetic_archive_score_card_uses_only_approved_values(self) -> None:
        self.assertRegex(self.parser.text, r"(?i)synthetic\s+example")
        self.assertRegex(self.parser.text, r"(?i)agent-hours\s*/\s*day\s+12\.34")
        self.assertRegex(self.parser.text, r"(?i)total\s+370\.20")
        self.assertRegex(self.parser.text, r"(?i)peak\s+31\.80")
        for value in ("12.34", "370.20", "31.80"):
            with self.subTest(value=value):
                self.assertEqual(self.index.count(value), 1)

    def test_page_defines_cumulative_hours_overlap_and_archive_score_window(self) -> None:
        text = self.parser.text.lower()
        for required in (
            "cumulative agent-hours",
            "overlapping turns",
            "overlapping turns independently",
            "archive score",
            "mean cumulative agent-hours per calendar day",
            "30 most recent completed local calendar days",
            "ending yesterday",
            "including zero-use days",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_page_states_privacy_boundary_and_report_disclosure(self) -> None:
        text = self.parser.text.lower()
        for required in (
            "calculation stays local",
            "bounded timing metadata",
            "does not decode conversation content",
            "no upload",
            "intentional disclosure",
            "full and csv reports",
            "semi-sensitive day-level patterns",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_page_has_exact_external_trust_and_benchmark_links(self) -> None:
        hrefs = {attrs.get("href") for attrs in self.parser.tags_named("a")}
        for href in (
            "https://github.com/tim-osterhus/codex-agent-hour-tracker",
            "https://pypi.org/project/codex-agent-hour-tracker/",
            "https://github.com/tim-osterhus/codex-agent-hour-tracker/blob/main/docs/methodology.md",
            "https://github.com/tim-osterhus/codex-agent-hour-tracker/blob/main/SECURITY.md",
            "https://openai.com/index/how-agents-are-transforming-work/",
        ):
            with self.subTest(href=href):
                self.assertIn(href, hrefs)
        benchmark = (
            "By June 2026, among daily active users at OpenAI, the 99th percentile "
            "regularly generated more than 60 hours of Codex agent turns per day "
            "across parallel agents."
        )
        self.assertIn(benchmark.lower(), self.parser.text.lower())
        self.assertNotRegex(self.parser.text, r"(?i)15\s*hours|personal\s+percentile")

    def test_share_section_invites_only_a_sanitized_terminal_screenshot(self) -> None:
        share_start = self.index.index('<section id="share"')
        share_end = self.index.index("</section>", share_start)
        share_parser = _SiteHTMLParser()
        share_parser.feed(self.index[share_start:share_end])
        share_text = share_parser.text.lower()
        for required in (
            "post or share",
            "terminal screenshot",
            "sanitized archive score card",
            "bounded aggregate",
            "dates, counts, and durations",
            "intentional disclosure",
        ):
            with self.subTest(required=required):
                self.assertIn(required, share_text)
        self.assertNotRegex(share_text, r"(?i)(?:full|csv).{0,80}(?:post|share)|(?:post|share).{0,80}(?:full|csv)")

    def test_faint_text_meets_wcag_contrast_against_both_dark_surfaces(self) -> None:
        faint = _css_variable(self.css, "faint")
        for background_name in ("bg", "surface"):
            background = _css_variable(self.css, background_name)
            ratio = _contrast_ratio(faint, background)
            with self.subTest(background=background_name):
                self.assertGreaterEqual(
                    ratio,
                    4.5,
                    f"--faint contrast against --{background_name} is {ratio:.3f}:1",
                )

    def test_page_is_local_dependency_free_and_has_no_private_or_marketing_tropes(self) -> None:
        for tag_name in ("form", "svg", "canvas", "iframe"):
            with self.subTest(tag_name=tag_name):
                self.assertEqual(self.parser.tags_named(tag_name), [])
        self.assertNotRegex(self.index, r"(?i)(google-analytics|plausible|segment|hotjar|gtag|fbq)")
        self.assertNotRegex(self.index, r"(?i)session[_ -]?dump")
        stylesheet_links = [
            attrs for attrs in self.parser.tags_named("link") if attrs.get("rel") == "stylesheet"
        ]
        self.assertEqual([attrs.get("href") for attrs in stylesheet_links], ["styles.css"])
        scripts = self.parser.tags_named("script")
        self.assertEqual([attrs.get("src") for attrs in scripts], ["app.js"])
        self.assertNotRegex(self.css, r"(?i)@import|url\(")
        self.assertNotRegex(self.index, r"<script(?![^>]+src=)")
        site_documents = {
            "site/index.html": self.index,
            "site/styles.css": self.css,
            "site/app.js": self.js,
            "site/_headers": self.headers,
        }
        _assert_public_text_has_no_absolute_paths(self, site_documents)
        _assert_public_text_has_no_synthetic_markers(self, site_documents)
        _assert_public_text_has_no_local_identifiers(self, site_documents)

    def test_copy_script_is_single_handler_local_and_time_bounded(self) -> None:
        self.assertNotRegex(self.js, r"(?i)fetch\s*\(|localStorage|sessionStorage|indexedDB|XMLHttpRequest")
        self.assertEqual(
            len(re.findall(r"addEventListener\s*\(\s*['\"]click['\"]", self.js)),
            1,
        )
        self.assertIn("navigator.clipboard.writeText", self.js)
        self.assertRegex(self.js, r"(?s)catch\s*\([^)]*\).*?(?:Copy failed|failed)")
        self.assertIn("Copied", self.js)
        self.assertRegex(self.js, r"copyButton\.textContent\s*=\s*['\"]Copied['\"]")
        self.assertRegex(self.js, r"const originalLabel\s*=\s*copyButton\.textContent")
        self.assertRegex(self.js, r"copyButton\.textContent\s*=\s*originalLabel")
        self.assertRegex(self.js, r"(?s)setTimeout\s*\(\s*\(\)\s*=>.*?,\s*2000\s*\)")

    def test_css_has_responsive_single_column_focus_overflow_and_reduced_motion_rules(self) -> None:
        self.assertRegex(self.css, r"@media\s*\([^)]*max-width\s*:\s*720px[^)]*\)")
        self.assertRegex(self.css, r"@media\s*\(prefers-reduced-motion\s*:\s*reduce\)")
        self.assertRegex(self.css, r"(?i)overflow-x\s*:\s*(?:hidden|clip)")
        self.assertRegex(self.css, r"(?i)focus-visible")
        self.assertRegex(self.css, r"(?s)max-width\s*:\s*(?:[5-6]\d|7[0-5])ch")

    def test_mobile_command_layout_stacks_code_and_button(self) -> None:
        mobile_start = self.css.index("@media (max-width: 720px)")
        mobile_css = self.css[mobile_start:]
        self.assertRegex(
            mobile_css,
            r"(?s)\.command-line\s*\{[^}]*flex-direction\s*:\s*column",
        )
        self.assertRegex(
            mobile_css,
            r"(?s)\.command-line\s+code\s*\{[^}]*flex\s*:\s*0\s+1\s+auto",
        )

    def test_headers_match_required_security_directives(self) -> None:
        expected = """/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Permissions-Policy: camera=(), microphone=(), geolocation=()"""
        self.assertEqual(self.headers.strip(), expected)

class PublicFilesContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    def test_build_requires_pep_639_compatible_hatchling(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            build_system = tomllib.load(handle)["build-system"]

        self.assertIn("hatchling>=1.27", build_system["requires"])

    def test_sdist_includes_public_security_and_methodology_docs(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            sdist_include = tomllib.load(handle)["tool"]["hatch"]["build"][
                "targets"
            ]["sdist"]["include"]

        self.assertIn("/SECURITY.md", sdist_include)
        self.assertIn("/docs", sdist_include)

    def test_distribution_name_is_public_package_name(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        self.assertEqual(project["name"], "codex-agent-hour-tracker")

    def test_both_console_scripts_point_to_cli_main(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            scripts = tomllib.load(handle)["project"]["scripts"]

        self.assertEqual(
            scripts.get("agent-hours"), "agent_hour_tracker.cli:main"
        )
        self.assertEqual(
            scripts.get("codex-agent-hour-tracker"), "agent_hour_tracker.cli:main"
        )

    def test_mit_license_exists(self) -> None:
        self.assertTrue((PROJECT_ROOT / "LICENSE").is_file())

    def test_gitignore_covers_private_and_generated_files(self) -> None:
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        for rule in (
            "/reports/",
            "/dist/",
            "/build/",
            "/docs/superpowers/",
            "__pycache__/",
            ".pytest_cache/",
            ".venv/",
            ".env",
            ".env.*",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, gitignore)

    def test_codex_agent_hour_skill_has_only_public_entrypoint_and_metadata(self) -> None:
        expected = {
            Path("SKILL.md"),
            Path("agents/openai.yaml"),
        }
        actual = {
            path.relative_to(SKILL_ROOT)
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        } if SKILL_ROOT.is_dir() else set()
        self.assertEqual(actual, expected)

    def test_codex_agent_hour_skill_frontmatter_and_trigger_are_precise(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(
            skill,
            r"(?ms)^---\s*\nname:\s*codex-agent-hour-tracker\s*\ndescription:\s*Use when .*agent-hour.*(?:Archive Score|tracker reports).*\n---",
        )
        frontmatter = skill.split("---", 2)[1]
        self.assertLessEqual(len(frontmatter), 1024)
        self.assertNotRegex(
            frontmatter,
            r"(?i)(workflow|step[- ]by[- ]step|install|run|share|generate)",
        )

    def test_codex_agent_hour_skill_uses_safe_cli_resolution(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?m)\bagent-hours\b")
        self.assertRegex(skill, r"(?s)agent-hours.{0,180}uvx codex-agent-hour-tracker")
        self.assertIn("--share", skill)

    def test_codex_agent_hour_skill_forbids_raw_session_access(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(
            skill,
            r"(?is)(never|do not|must not).{0,100}(open|read|summarize|upload|print).{0,100}raw.{0,100}(session|jsonl)",
        )
        self.assertRegex(skill, r"(?is)sessions directory")
        self.assertRegex(skill, r"(?is)CLI.{0,100}(bounded|scan)")

    def test_codex_agent_hour_skill_treats_full_reports_as_private(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?is)full (?:text|report).{0,120}private")
        self.assertRegex(skill, r"(?is)CSV.{0,120}private")
        self.assertRegex(skill, r"(?is)ask.{0,100}permission")
        self.assertRegex(skill, r"(?is)before.{0,100}save.{0,100}(?:full report|CSV|report)")
        self.assertRegex(skill, r"(?is)user-approved path")

    def test_codex_agent_hour_skill_requires_explicit_sharing_authorization(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?is)--share.{0,160}(public|screenshot|comparison)")
        self.assertRegex(
            skill,
            r"(?is)(never|do not|must not).{0,120}(publish|upload|share).{0,120}(explicit|request|permission)",
        )
        self.assertRegex(skill, r"(?is)explicit (?:user )?(?:request|permission).{0,80}(?:publish|upload|share)")

    def test_codex_agent_hour_skill_defines_exact_archive_score_window(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(
            skill,
            r"(?is)30 most recent completed local calendar days.{0,100}ending yesterday",
        )
        self.assertRegex(skill, r"(?is)including zero-use days|zero-use days.{0,50}included")

    def test_codex_agent_hour_skill_uses_archive_scoped_naming_and_aggregation(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?is)single scanned archive.{0,80}Archive Score")
        self.assertRegex(skill, r"(?is)never.{0,80}(?:account|operator|person) score")
        self.assertRegex(skill, r"(?is)across machines.{0,120}(?:explicit|request)")
        self.assertRegex(skill, r"(?is)already-produced bounded outputs.{0,120}never.{0,80}raw")

    def test_codex_agent_hour_skill_does_not_embed_private_values_or_identifiers(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        documents = {"skills/codex-agent-hour-tracker/SKILL.md": skill}
        _assert_public_text_has_no_absolute_paths(self, documents)
        _assert_public_text_has_no_synthetic_markers(self, documents)
        _assert_public_text_has_no_local_identifiers(self, documents)
        self.assertNotRegex(skill, r"\$\s*\d+(?:\.\d+)?\s*(?:/|per\s+)?(?:hour|hr)")

    def test_codex_agent_hour_skill_openai_yaml_has_concise_interface_without_dependencies(self) -> None:
        config = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertRegex(config, r'(?m)^interface:\s*$')
        for key in ("display_name", "short_description", "default_prompt"):
            self.assertRegex(config, rf'(?m)^  {key}:\s*"[^"]+"\s*$')
        self.assertRegex(config, r'(?m)^  default_prompt:.*\$codex-agent-hour-tracker')
        self.assertNotRegex(config, r"(?m)^dependencies:\s*$")
        self.assertNotRegex(config, r"(?m)^policy:\s*$")

    def test_codex_agent_hour_skill_defines_score_as_mean_not_total(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(
            skill,
            r"(?is)mean cumulative agent-hours per calendar day",
        )
        self.assertRegex(
            skill,
            r"(?is)(?:score|Archive Score).{0,120}(?:not|rather than|instead of).{0,80}total",
        )
        self.assertRegex(skill, r"(?is)(?:total|peak|turn count).{0,120}(?:context|additional|also)")

    def test_codex_agent_hour_skill_describes_csv_patterns_and_save_boundary(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(
            _line_contains_tokens(
                skill,
                "CSV",
                "day-level activity",
                "turn-count patterns",
            )
        )
        self.assertTrue(
            _line_contains_tokens(
                skill,
                "ask",
                "permission",
                "before",
                "save",
                "either",
                "full text report",
                "CSV",
            )
        )
        self.assertTrue(_line_contains_tokens(skill, "save only to a user-approved path"))

    def test_codex_agent_hour_skill_authorizes_each_output_type_and_forbids_raw_uploads(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(
            _line_contains_tokens(
                skill,
                "Archive Score card",
                "publish",
                "upload",
                "share",
                "explicit user request",
            )
        )
        self.assertTrue(
            _line_contains_tokens(
                skill,
                "full text report",
                "CSV",
                "publish",
                "upload",
                "share",
                "explicit user request",
            )
        )
        self.assertTrue(
            _line_contains_tokens(
                skill,
                "never upload raw session files under any circumstance",
            )
        )
        self.assertRegex(
            skill,
            r"(?is)only.{0,120}sanitized canonical.{0,120}`--share`",
        )

    def test_codex_agent_hour_skill_handles_custom_range_sharing_as_canonical_only(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?is)`--share`.{0,220}canonical")
        self.assertTrue(_line_contains_tokens(skill, "--share", "canonical-only", "--format"))
        self.assertRegex(
            skill,
            r"(?is)(?:reject|refuse|does not accept).{0,100}(?:explicit )?(?:`?--start`?|start).{0,100}(?:`?--end`?|end)",
        )
        self.assertRegex(
            skill,
            r"(?is)custom.{0,180}(?:private|do not expose|authorization)",
        )

    def test_codex_agent_hour_skill_description_targets_codex_tracker_reports(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = skill.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?im)^description:\s*Use when .*Codex tracker reports")


if __name__ == "__main__":
    unittest.main()
