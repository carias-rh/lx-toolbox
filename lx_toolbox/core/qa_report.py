"""
QA Report generator — produces an AsciiDoc report with embedded screenshots
and a CSV file matching the Google Sheets tracking format.

Reports are persisted incrementally to a JSON data file after every lab
start/grade/finish event.  Subsequent runs for the same course load the
existing data and merge new or repeated exercises (upsert semantics).

The share target is a DOCX opened in Google Docs (``qa-report`` converts
AsciiDoc → DocBook → Pandoc, then tints PASS/FAIL rows in the OpenXML).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from ..utils.terminal_ocr import parse_terminal_timing_from_screenshot


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# Excel-style conditional-format colours; they survive Google Docs import.
_PASS_FILL = "C6EFCE"
_PASS_TEXT = "006100"
_FAIL_FILL = "FFC7CE"
_FAIL_TEXT = "9C0006"

_JIRA_SERVER_BROWSE = "https://issues.redhat.com/browse/"
_JIRA_CLOUD_BROWSE = "https://redhat.atlassian.net/browse/"
_JIRA_CLOUD_PROJECTS = {"CDEV"}
_JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b", re.IGNORECASE)
_JIRA_BROWSE = re.compile(
    r"https?://(?:issues\.redhat\.com(?:/jira)?|redhat\.atlassian\.net)/browse/"
    r"([A-Z][A-Z0-9]{1,9}-\d+)",
    re.IGNORECASE,
)

_SCREENSHOT_PHASES = (
    ("lab start", "start_screenshot"),
    ("mid-exercise", "mid_screenshot"),
    ("lab grade", "grade_screenshot"),
    ("lab finish", "finish_screenshot"),
)
_SCRIPT_TIME_PHASES = ("start", "grade", "finish")


_DEFECT_TOKEN = re.compile(
    r"(https?://(?:issues\.redhat\.com(?:/jira)?|redhat\.atlassian\.net)"
    r"/browse/[A-Z][A-Z0-9]{1,9}-\d+)"
    r"|(https?://[^\s,;]+)"
    r"|(\b[A-Z][A-Z0-9]{1,9}-\d+\b)",
    re.IGNORECASE,
)


def defect_browse_url(key: str) -> str:
    """Canonical browse URL for a Jira key (CDEV on Cloud, others on issues.redhat.com)."""
    key = key.upper()
    project = key.split("-", 1)[0]
    base = _JIRA_CLOUD_BROWSE if project in _JIRA_CLOUD_PROJECTS else _JIRA_SERVER_BROWSE
    return base + key


def parse_defect_refs(raw: str) -> list[str]:
    """Return canonical Defect URLs from pasted Jira URLs or keys.

    Empty input yields an empty list. Multiple tokens (comma, semicolon, or
    whitespace separated) are kept in order of appearance and de-duplicated.
    Bare keys such as ``PTL-12345`` and ``issues.redhat.com/browse/…`` URLs
    become ``https://issues.redhat.com/browse/PTL-12345``. ``CDEV-12345``
    becomes ``https://redhat.atlassian.net/browse/CDEV-12345``. Other http(s)
    URLs are kept as-is.
    """
    if not raw or not str(raw).strip():
        return []

    text = str(raw).strip()
    found: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            found.append(url)

    def add_key(key: str) -> None:
        add(defect_browse_url(key))

    for browse_url, other_url, bare_key in _DEFECT_TOKEN.findall(text):
        if browse_url:
            match = _JIRA_BROWSE.search(browse_url)
            if match:
                add_key(match.group(1))
        elif other_url:
            add(other_url.rstrip(").,]>\"'"))
        elif bare_key:
            add_key(bare_key)

    return found


def defect_key(url: str) -> str:
    """Return the Jira key from a Defect URL, or the original string."""
    match = _JIRA_BROWSE.search(url) or _JIRA_KEY.search(url)
    return match.group(1).upper() if match else url


def defect_link(url: str) -> str:
    """AsciiDoc link ``url[KEY]`` for a Defect URL."""
    key = defect_key(url)
    return f"{url}[{key}]"


_TITLE_PREFIX = re.compile(
    r"^(?:[A-Z]{2,}\d+\s*:\s*)?(?:ch\d+s\d+\s*[-:]?\s*)?",
    re.IGNORECASE,
)


@dataclass
class DefectDetails:
    """Jira-enriched view of a Defect listed on a QA Report."""
    url: str
    key: str
    title: str = ""
    fixed: bool | None = None  # True if a branch/commit/PR is attached


def section_short(chapter_section: str) -> str:
    """``ch09s02`` → ``9.2``."""
    match = re.match(r"ch(\d+)s(\d+)", chapter_section)
    if not match:
        return chapter_section
    return f"{int(match.group(1))}.{int(match.group(2))}"


def display_defect_title(summary: str) -> str:
    """Strip the ``RH403: ch07s04 - `` prefix from a Jira summary when present."""
    if not summary:
        return ""
    stripped = _TITLE_PREFIX.sub("", summary).strip()
    return stripped or summary


def defect_has_development(dev_summary: dict) -> bool:
    """True when Jira Development lists a pull request, commit repo, or branch."""
    summary = (dev_summary or {}).get("summary") or {}
    for kind in ("pullrequest", "repository", "branch"):
        count = ((summary.get(kind) or {}).get("overall") or {}).get("count") or 0
        try:
            if int(count) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _jira_session():
    """Return ``(base_url, requests.Session)`` or ``(None, None)`` if unconfigured."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    except ImportError:
        pass
    try:
        import requests
    except ImportError:
        return None, None
    base = (os.getenv("JIRA_URL") or "").rstrip("/")
    email = os.getenv("JIRA_EMAIL")
    token = os.getenv("JIRA_API_TOKEN")
    if not (base and email and token):
        return None, None
    session = requests.Session()
    session.auth = (email, token)
    session.headers["Accept"] = "application/json"
    return base, session


def fetch_jira_defect_details(urls: list[str]) -> dict[str, DefectDetails]:
    """Look up Jira summaries and Development (branch/commit/PR) for Defect URLs.

    Returns a map keyed by URL. Missing credentials or API errors skip entries
    so report generation still succeeds.
    """
    log = logging.getLogger(__name__)
    result: dict[str, DefectDetails] = {}
    if not urls:
        return result

    base, session = _jira_session()
    if session is None:
        log.info("Jira is not configured; Defect titles and Fixed defect are omitted")
        return result

    url_by_key: dict[str, str] = {}
    for url in urls:
        key = defect_key(url)
        url_by_key[key] = url
        result[url] = DefectDetails(url=url, key=key)

    keys = list(url_by_key)
    jql = "key in ({})".format(", ".join(keys))
    try:
        resp = session.get(
            f"{base}/rest/api/3/search/jql",
            params={"jql": jql, "maxResults": max(len(keys), 50), "fields": "summary"},
            timeout=30,
        )
        resp.raise_for_status()
        issues = resp.json().get("issues") or []
    except Exception as exc:
        log.warning("Could not fetch Jira issues for Defect table: %s", exc)
        return result

    def _lookup(issue: dict) -> tuple[str, str, bool | None]:
        key = issue.get("key") or ""
        title = display_defect_title(((issue.get("fields") or {}).get("summary")) or "")
        issue_id = issue.get("id")
        fixed: bool | None = None
        if issue_id:
            try:
                dev = session.get(
                    f"{base}/rest/dev-status/latest/issue/summary",
                    params={"issueId": issue_id},
                    timeout=20,
                )
                if dev.status_code == 200:
                    fixed = defect_has_development(dev.json())
            except Exception as exc:
                log.debug("dev-status failed for %s: %s", key, exc)
        return key, title, fixed

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, title, fixed in pool.map(_lookup, issues):
            url = url_by_key.get(key)
            if not url:
                continue
            result[url] = DefectDetails(url=url, key=key, title=title, fixed=fixed)

    return result


@dataclass
class ExerciseResult:
    """Result data for a single guided exercise or lab."""
    chapter_section: str          # e.g. "ch01s02"
    title: str                    # e.g. "Section 1.2: Guided Exercise: Resource Manifests"
    grade_result: str = ""        # "PASS" / "FAIL" / "" (not yet graded)
    start_screenshot: str = ""    # absolute path to screenshot file
    mid_screenshot: str = ""      # screenshot taken mid-exercise as proof of QA
    grade_screenshot: str = ""
    finish_screenshot: str = ""
    start_duration_secs: float = 0.0
    grade_duration_secs: float = 0.0
    finish_duration_secs: float = 0.0
    start_duration_source: str = "manual"
    grade_duration_source: str = "manual"
    finish_duration_source: str = "manual"
    start_started_at: str = ""
    grade_started_at: str = ""
    finish_started_at: str = ""
    notes: str = ""
    defects: list[str] = field(default_factory=list)  # canonical Jira URLs


class QAReport:
    """
    Accumulates QA exercise results and generates reports.

    The report directory is stable per course_id
    (``qa_reports/{environment}/{course_id}/``).
    A JSON data file (``qa_data.json``) acts as the source of truth and is
    loaded automatically on construction if it already exists, allowing
    multiple script runs to build up the same report incrementally.

    Usage::

        report = QAReport(course_id="do280-4.14", environment="factory")
        # ... exercises are added during QA ...
        report.save()   # called after every lab start/grade/finish
    """

    def __init__(
        self,
        course_id: str,
        environment: str,
        assignee: str = "",
        report_dir: Path | str | None = None,
    ):
        self.course_id = course_id
        self.environment = environment
        self.assignee = assignee
        self.date = datetime.now().strftime("%Y-%m-%d")
        self.exercises: list[ExerciseResult] = []

        if report_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            self.report_dir = project_root / "qa_reports" / self.environment / self.course_id
        else:
            self.report_dir = Path(report_dir)

        self.screenshots_dir = self.report_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

        self._json_path = self.report_dir / "qa_data.json"

        # Load existing data from a previous run (if any)
        self._load_json()

    # ------------------------------------------------------------------
    # Persistence: JSON source of truth
    # ------------------------------------------------------------------

    def _load_json(self):
        """Load exercise results from an existing qa_data.json if present."""
        if not self._json_path.exists():
            return
        try:
            with open(self._json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.date = data.get("date", self.date)
            if not self.assignee:
                self.assignee = data.get("assignee", "")
            known = {item.name for item in fields(ExerciseResult)}
            for ex_dict in data.get("exercises", []):
                kwargs = {k: v for k, v in ex_dict.items() if k in known}
                if not kwargs.get("defects"):
                    kwargs["defects"] = []
                self.exercises.append(ExerciseResult(**kwargs))
            logging.getLogger(__name__).info(
                f"Loaded {len(self.exercises)} existing exercise(s) from {self._json_path}"
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not load existing report data: {e}")

    def _save_json(self):
        """Write the current state to qa_data.json."""
        data = {
            "course_id": self.course_id,
            "environment": self.environment,
            "assignee": self.assignee,
            "date": self.date,
            "exercises": [asdict(ex) for ex in self.exercises],
        }
        with open(self._json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save(self):
        """Persist current state: write JSON, regenerate .adoc and .csv."""
        self.refresh_script_times_from_screenshots()
        self._save_json()
        self.generate_asciidoc()
        self.generate_csv()

    def apply_script_time_from_screenshot(
        self, ex: ExerciseResult, phase: str, screenshot_path: str
    ) -> bool:
        """Set this phase's Lab Script time from the screenshot ``time real`` line.

        Never uses wall-clock (button-wait) time. Returns True when ``real``
        was parsed. On failure the stored duration is cleared so a wait time
        cannot leak into the report.
        """
        parsed = parse_terminal_timing_from_screenshot(screenshot_path)
        setattr(ex, f"{phase}_screenshot", screenshot_path)
        if parsed and parsed.started_at:
            setattr(ex, f"{phase}_started_at", parsed.started_at)
        else:
            setattr(ex, f"{phase}_started_at", "")
        if parsed and parsed.real_seconds is not None:
            setattr(ex, f"{phase}_duration_secs", parsed.real_seconds)
            setattr(ex, f"{phase}_duration_source", "ocr")
            return True
        setattr(ex, f"{phase}_duration_secs", 0.0)
        setattr(ex, f"{phase}_duration_source", "")
        return False

    def refresh_script_times_from_screenshots(self, *, force: bool = False) -> int:
        """Fill Lab Script times from screenshot ``time real`` when missing.

        Phases already sourced from OCR are skipped unless ``force`` is true.
        Returns how many phases were updated.
        """
        jobs: list[tuple[ExerciseResult, str, str]] = []
        for ex in self.exercises:
            for phase in _SCRIPT_TIME_PHASES:
                source_attr = f"{phase}_duration_source"
                if not force and getattr(ex, source_attr) == "ocr":
                    continue
                path = getattr(ex, f"{phase}_screenshot")
                if not path or not os.path.isfile(path):
                    continue
                jobs.append((ex, phase, path))
        if not jobs:
            return 0

        def _ocr(job: tuple[ExerciseResult, str, str]):
            exercise, phase, path = job
            return exercise, phase, parse_terminal_timing_from_screenshot(path)

        updated = 0
        workers = min(8, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ex, phase, parsed in pool.map(_ocr, jobs):
                if not parsed or parsed.real_seconds is None:
                    continue
                setattr(ex, f"{phase}_duration_secs", parsed.real_seconds)
                setattr(ex, f"{phase}_duration_source", "ocr")
                if parsed.started_at:
                    setattr(ex, f"{phase}_started_at", parsed.started_at)
                updated += 1
        return updated

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def add_exercise(self, result: ExerciseResult):
        """
        Add or replace an exercise result (upsert by chapter_section).

        If an exercise with the same ``chapter_section`` already exists it is
        replaced in-place so that re-running an exercise overwrites the old
        result while preserving ordering.
        """
        for i, ex in enumerate(self.exercises):
            if ex.chapter_section == result.chapter_section:
                self.exercises[i] = result
                return
        self.exercises.append(result)

    def get_exercise(self, chapter_section: str) -> ExerciseResult | None:
        """Retrieve an exercise result by its chapter_section identifier."""
        for ex in self.exercises:
            if ex.chapter_section == chapter_section:
                return ex
        return None

    def screenshot_path(self, chapter_section: str, phase: str) -> str:
        """
        Return the full filesystem path for a screenshot.

        Args:
            chapter_section: e.g. "ch01s02"
            phase: "start", "mid", "grade", or "finish"
        """
        return str(self.screenshots_dir / f"{chapter_section}_{phase}.png")

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds into a human-readable string, truncating fractional seconds."""
        if seconds <= 0:
            return ""

        whole_seconds = int(seconds)
        minutes = whole_seconds // 60
        secs = whole_seconds % 60

        if minutes > 0:
            return f"{minutes}m {secs:02d}s"
        return f"{whole_seconds}s"

    @staticmethod
    def _chapter_number(chapter_section: str) -> str:
        """Extract chapter number from a chapter_section like 'ch02s04' → '2'."""
        match = re.match(r"ch(\d+)", chapter_section)
        return str(int(match.group(1))) if match else "?"

    @staticmethod
    def _sort_key(ex: "ExerciseResult") -> tuple:
        """Return a (chapter, section) tuple for ordering exercises."""
        m = re.match(r"ch(\d+)s(\d+)", ex.chapter_section)
        return (int(m.group(1)), int(m.group(2))) if m else (999, 999)

    def _sorted_exercises(self) -> list[ExerciseResult]:
        """Return exercises sorted by chapter and section number."""
        return sorted(self.exercises, key=self._sort_key)

    @staticmethod
    def _escape_cell(text: str) -> str:
        """Escape AsciiDoc table-cell special characters."""
        return text.replace("|", "\\|").replace(">>", "≫")

    @staticmethod
    def _qa_result_markup(result: str) -> str:
        # Plain PASS/FAIL inside tables. AsciiDoc ``[green]#PASS#`` is parsed as
        # a cell specifier and leaks literal ``[green]`` into the DOCX.
        # Colour comes from cellbgcolor (HTML/PDF) and the DOCX post-process.
        if result in ("PASS", "FAIL"):
            return result
        return result if result else "-"

    @staticmethod
    def _row_bg(result: str) -> str:
        if result == "PASS":
            return "{set:cellbgcolor:#C6EFCE}"
        if result == "FAIL":
            return "{set:cellbgcolor:#FFC7CE}"
        return "{set:cellbgcolor!}"

    @staticmethod
    def _has_grade_script(ex: ExerciseResult) -> bool:
        """True when the ``lab grade`` Lab Script ran for this exercise."""
        if ex.grade_duration_secs > 0:
            return True
        return bool(ex.grade_screenshot and os.path.isfile(ex.grade_screenshot))

    @staticmethod
    def _duration_cell(seconds: float) -> str:
        return QAReport._format_duration(seconds) or "-"

    @staticmethod
    def _fixed_cell(fixed: bool | None) -> str:
        if fixed is True:
            return "YES"
        if fixed is False:
            return "NO"
        return "—"

    def _all_defects(self) -> list[tuple[ExerciseResult, str]]:
        """Flatten (exercise, defect URL) pairs in report order."""
        pairs: list[tuple[ExerciseResult, str]] = []
        for ex in self._sorted_exercises():
            for url in ex.defects or []:
                pairs.append((ex, url))
        return pairs

    def _screenshot_block(self, label: str, path: str) -> list[str]:
        filename = os.path.basename(path)
        return [
            '[cols="1a", frame=all, grid=none, options="autowidth"]',
            "|===",
            "|",
            f"image::{filename}[{label}, 800]",
            f"| *{label}*",
            "|===",
            "",
        ]

    # ------------------------------------------------------------------
    # AsciiDoc generation
    # ------------------------------------------------------------------

    def render_asciidoc(self, defect_details: dict[str, DefectDetails] | None = None) -> str:
        """Return the AsciiDoc report as a string.

        ``defect_details`` is a URL → Jira enrichment map. ``generate_asciidoc``
        fills it from Jira; tests pass a stub or omit it.
        """
        lines: list[str] = []
        exercises = self._sorted_exercises()
        details = defect_details or {}

        lines.append(f"= QA Report: {self.course_id}")
        lines.append(":imagesdir: screenshots")
        lines.append(":toc!:")
        lines.append("")
        lines.append("== Course Information")
        lines.append("")
        lines.append(f"Course:: {self.course_id}")
        lines.append(f"Environment:: {self.environment}")
        lines.append(f"Date:: {self.date}")
        if self.assignee:
            lines.append(f"Assignee:: {self.assignee}")
        lines.append("")

        lines.append("== Summary")
        lines.append("")
        lines.append('[cols="4,1,1,1,1", options="header"]')
        lines.append("|===")
        lines.append("| Exercise | QA Result | Start | Grade | Finish")
        lines.append("")
        for ex in exercises:
            bg = self._row_bg(ex.grade_result)
            title = self._escape_cell(ex.title)
            lines.append(f"| {bg}<<{ex.chapter_section},{title}>>")
            lines.append(f"| {self._qa_result_markup(ex.grade_result)}")
            lines.append(f"| {self._duration_cell(ex.start_duration_secs)}")
            lines.append(f"| {self._duration_cell(ex.grade_duration_secs)}")
            lines.append(f"| {self._duration_cell(ex.finish_duration_secs)}")
            lines.append("")
        lines.append("|===")
        lines.append("")

        defect_pairs = self._all_defects()
        if defect_pairs:
            lines.append("== Defects")
            lines.append("")
            lines.append('[cols="2,1,4,1", options="header"]')
            lines.append("|===")
            lines.append("| Key | Section | Title | Fixed defect")
            lines.append("")
            for ex, url in defect_pairs:
                info = details.get(url) or DefectDetails(url=url, key=defect_key(url))
                bg = self._row_bg(ex.grade_result)
                short = section_short(ex.chapter_section)
                title = self._escape_cell(info.title)
                lines.append(f"| {bg}{defect_link(url)}")
                lines.append(f"| <<{ex.chapter_section},{short}>>")
                lines.append(f"| {title}")
                lines.append(f"| {self._fixed_cell(info.fixed)}")
                lines.append("")
            lines.append("|===")
            lines.append("")

        current_chapter = None
        for ex in exercises:
            ch = self._chapter_number(ex.chapter_section)
            if ch != current_chapter:
                current_chapter = ch
                lines.append(f"== Chapter {current_chapter}")
                lines.append("")

            lines.append(f"[[{ex.chapter_section}]]")
            lines.append(f"=== {ex.title}")
            lines.append("")

            has_grade = self._has_grade_script(ex)
            if has_grade:
                lines.append('[cols="1,1,1,1", options="header"]')
                lines.append("|===")
                lines.append("| QA Result | Start | Grade | Finish")
                lines.append(
                    f"| {self._row_bg(ex.grade_result)}{self._qa_result_markup(ex.grade_result)} "
                    f"| {self._duration_cell(ex.start_duration_secs)} "
                    f"| {self._duration_cell(ex.grade_duration_secs)} "
                    f"| {self._duration_cell(ex.finish_duration_secs)}"
                )
            else:
                lines.append('[cols="1,1,1", options="header"]')
                lines.append("|===")
                lines.append("| QA Result | Start | Finish")
                lines.append(
                    f"| {self._row_bg(ex.grade_result)}{self._qa_result_markup(ex.grade_result)} "
                    f"| {self._duration_cell(ex.start_duration_secs)} "
                    f"| {self._duration_cell(ex.finish_duration_secs)}"
                )
            lines.append("|===")
            lines.append("")

            if ex.defects:
                joined = ", ".join(defect_link(url) for url in ex.defects)
                lines.append(f"Defects:: {joined}")
                lines.append("")
            if ex.notes:
                lines.append(f"Notes:: {ex.notes}")
                lines.append("")

            for label, attr in _SCREENSHOT_PHASES:
                path = getattr(ex, attr)
                if path and os.path.isfile(path):
                    lines.extend(self._screenshot_block(label, path))

        return "\n".join(lines)

    def generate_asciidoc(self, output_path: str = None) -> str:
        """
        Write an AsciiDoc report with embedded screenshot references.

        Returns:
            The path to the generated .adoc file.
        """
        if output_path is None:
            output_path = str(self.report_dir / f"qa_report_{self.course_id}.adoc")

        if self.refresh_script_times_from_screenshots():
            self._save_json()

        urls = [url for _, url in self._all_defects()]
        content = self.render_asciidoc(defect_details=fetch_jira_defect_details(urls))
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_path

    # ------------------------------------------------------------------
    # CSV generation
    # ------------------------------------------------------------------

    def generate_csv(self, output_path: str = None) -> str:
        """
        Write a CSV file with columns matching the Google Sheets tracking format.

        Returns:
            The path to the generated .csv file.
        """
        if output_path is None:
            output_path = str(self.report_dir / f"qa_results_{self.course_id}.csv")

        fieldnames = [
            "Guided Exercise/Lab Name",
            "PASS/FAIL",
            "start script time",
            "grade script time",
            "finish script time",
            "Notes and Concerns",
            "Defects",
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for ex in self._sorted_exercises():
                writer.writerow({
                    "Guided Exercise/Lab Name": ex.title,
                    "PASS/FAIL": ex.grade_result,
                    "start script time": self._format_duration(ex.start_duration_secs),
                    "grade script time": self._format_duration(ex.grade_duration_secs),
                    "finish script time": self._format_duration(ex.finish_duration_secs),
                    "Notes and Concerns": ex.notes,
                    "Defects": " ".join(ex.defects or []),
                })

        return output_path


# ------------------------------------------------------------------
# DOCX post-process (PASS/FAIL colours + row tint for Google Docs)
# ------------------------------------------------------------------

def _register_namespaces_from(xml: bytes) -> None:
    """Register xmlns prefixes so ElementTree round-trips Word XML."""
    head = xml[:4096]
    for match in re.finditer(rb'\sxmlns:([A-Za-z0-9]+)=["\']([^"\']+)["\']', head):
        ET.register_namespace(match.group(1).decode(), match.group(2).decode())
    default = re.search(rb'\sxmlns=["\']([^"\']+)["\']', head)
    if default:
        ET.register_namespace("", default.group(1).decode())


def _tc_text(tc: ET.Element) -> str:
    return "".join(tc.itertext()).strip()


def _ensure_child(parent: ET.Element, tag: str, index: int = 0) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.Element(tag)
        parent.insert(index, child)
    return child


def _set_cell_fill(tc: ET.Element, fill: str) -> None:
    tc_pr = _ensure_child(tc, f"{W}tcPr", 0)
    shd = tc_pr.find(f"{W}shd")
    if shd is None:
        shd = ET.SubElement(tc_pr, f"{W}shd")
    shd.set(f"{W}val", "clear")
    shd.set(f"{W}fill", fill)
    shd.set(f"{W}color", "auto")


def _set_run_color(run: ET.Element, color: str) -> None:
    r_pr = _ensure_child(run, f"{W}rPr", 0)
    color_el = r_pr.find(f"{W}color")
    if color_el is None:
        color_el = ET.SubElement(r_pr, f"{W}color")
    color_el.set(f"{W}val", color)
    if r_pr.find(f"{W}b") is None:
        ET.SubElement(r_pr, f"{W}b")


_SHOT_CAPTIONS = frozenset({"lab start", "mid-exercise", "lab grade", "lab finish"})
_BOX_BORDER = {"val": "single", "sz": "12", "space": "0", "color": "666666"}
_NIL_BORDER = {"val": "nil"}


def _set_border_child(parent: ET.Element, tag: str, attrs: dict[str, str]) -> None:
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    for key, value in attrs.items():
        el.set(f"{W}{key}", value)


def _table_rows(tbl: ET.Element) -> list[ET.Element]:
    return tbl.findall(f"{W}tr")


def is_screenshot_table(tbl: ET.Element) -> bool:
    rows = _table_rows(tbl)
    if len(rows) != 2:
        return False
    has_drawing = rows[0].find(f".//{W}drawing") is not None
    cells = rows[1].findall(f"{W}tc")
    caption = _tc_text(cells[0]).lower() if cells else ""
    return has_drawing and caption in _SHOT_CAPTIONS


def box_screenshot_tables(root: ET.Element) -> int:
    """Draw a single outer border around each screenshot + caption table."""
    boxed = 0
    for tbl in root.findall(f".//{W}tbl"):
        if not is_screenshot_table(tbl):
            continue
        tbl_pr = _ensure_child(tbl, f"{W}tblPr", 0)
        borders = tbl_pr.find(f"{W}tblBorders")
        if borders is None:
            borders = ET.SubElement(tbl_pr, f"{W}tblBorders")
        for edge in ("top", "left", "bottom", "right"):
            _set_border_child(borders, f"{W}{edge}", _BOX_BORDER)
        for edge in ("insideH", "insideV"):
            _set_border_child(borders, f"{W}{edge}", _NIL_BORDER)

        rows = _table_rows(tbl)
        for i, row in enumerate(rows):
            for tc in row.findall(f"{W}tc"):
                tc_pr = _ensure_child(tc, f"{W}tcPr", 0)
                tc_borders = tc_pr.find(f"{W}tcBorders")
                if tc_borders is None:
                    tc_borders = ET.SubElement(tc_pr, f"{W}tcBorders")
                _set_border_child(tc_borders, f"{W}left", _BOX_BORDER)
                _set_border_child(tc_borders, f"{W}right", _BOX_BORDER)
                _set_border_child(
                    tc_borders, f"{W}top", _BOX_BORDER if i == 0 else _NIL_BORDER
                )
                _set_border_child(
                    tc_borders, f"{W}bottom", _BOX_BORDER if i == len(rows) - 1 else _NIL_BORDER
                )
        boxed += 1
    return boxed


def apply_pass_fail_tints(root: ET.Element) -> int:
    """Tint every table row whose cells include a QA Result of PASS or FAIL.

    Returns the number of rows styled.
    """
    def _anchors(row: ET.Element) -> list[str]:
        found = []
        for link in row.findall(f".//{W}hyperlink"):
            anchor = link.get(f"{W}anchor")
            if anchor:
                found.append(anchor)
        return found

    def _tint(row: ET.Element, fill: str, color: str) -> None:
        for tc in row.findall(f"{W}tc"):
            _set_cell_fill(tc, fill)
        for run in row.findall(f".//{W}r"):
            run_text = "".join((t.text or "") for t in run.findall(f"{W}t")).strip()
            if run_text in ("PASS", "FAIL"):
                _set_run_color(run, color)

    styled = 0
    anchor_result: dict[str, str] = {}
    rows = root.findall(f".//{W}tr")
    for row in rows:
        texts = [_tc_text(tc) for tc in row.findall(f"{W}tc")]
        if "FAIL" in texts:
            result = "FAIL"
        elif "PASS" in texts:
            result = "PASS"
        else:
            continue
        for anchor in _anchors(row):
            anchor_result[anchor] = result
        fill, color = (_FAIL_FILL, _FAIL_TEXT) if result == "FAIL" else (_PASS_FILL, _PASS_TEXT)
        _tint(row, fill, color)
        styled += 1

    # Defects table rows keep the Section QA Result tint even without a PASS/FAIL cell
    for row in rows:
        texts = [_tc_text(tc) for tc in row.findall(f"{W}tc")]
        if "PASS" in texts or "FAIL" in texts:
            continue
        mapped = {anchor_result[a] for a in _anchors(row) if a in anchor_result}
        if mapped == {"FAIL"}:
            _tint(row, _FAIL_FILL, _FAIL_TEXT)
            styled += 1
        elif mapped == {"PASS"}:
            _tint(row, _PASS_FILL, _PASS_TEXT)
            styled += 1
    return styled


def style_qa_report_docx(path: str | Path) -> Path:
    """Apply PASS/FAIL row tints to an already-converted QA Report DOCX."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with ZipFile(path, "r") as zin:
        xml = zin.read("word/document.xml")
        others = [
            (info, zin.read(info.filename))
            for info in zin.infolist()
            if info.filename != "word/document.xml"
        ]
        doc_info = zin.getinfo("word/document.xml")

    _register_namespaces_from(xml)
    root = ET.fromstring(xml)
    apply_pass_fail_tints(root)
    box_screenshot_tables(root)
    new_xml = ET.tostring(root, encoding="UTF-8", xml_declaration=True)

    buf = io.BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zout:
        zout.writestr(doc_info, new_xml)
        for info, data in others:
            zout.writestr(info, data)
    path.write_bytes(buf.getvalue())
    return path


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "style-docx":
        styled = style_qa_report_docx(sys.argv[2])
        print(f"Styled: {styled}")
    else:
        print("Usage: python -m lx_toolbox.core.qa_report style-docx <file.docx>", file=sys.stderr)
        sys.exit(1)
