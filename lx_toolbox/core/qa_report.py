"""
QA Report generator — produces an AsciiDoc report with embedded screenshots
and a CSV file matching the Google Sheets tracking format.

Reports are persisted incrementally to a JSON data file after every lab
start/grade/finish event.  Subsequent runs for the same course load the
existing data and merge new or repeated exercises (upsert semantics).
"""

import csv
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


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
    notes: str = ""


class QAReport:
    """
    Accumulates QA exercise results and generates reports.

    The report directory is stable per course_id (``qa_reports/{course_id}/``).
    A JSON data file (``qa_data.json``) acts as the source of truth and is
    loaded automatically on construction if it already exists, allowing
    multiple script runs to build up the same report incrementally.

    Usage::

        report = QAReport(course_id="do280-4.14", environment="factory")
        # ... exercises are added during QA ...
        report.save()   # called after every lab start/grade/finish
    """

    def __init__(self, course_id: str, environment: str, assignee: str = ""):
        self.course_id = course_id
        self.environment = environment
        self.assignee = assignee
        self.date = datetime.now().strftime("%Y-%m-%d")
        self.exercises: list[ExerciseResult] = []

        # Anchor to the project root (same root that config/ lives in)
        project_root = Path(__file__).resolve().parent.parent.parent
        # Stable directory per environment and course
        # e.g. <project_root>/qa_reports/factory/do280-4.14/
        self.report_dir = project_root / "qa_reports" / self.environment / self.course_id
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
            # Restore metadata from previous run (keep current values as override)
            self.date = data.get("date", self.date)
            if not self.assignee:
                self.assignee = data.get("assignee", "")
            # Restore exercises
            for ex_dict in data.get("exercises", []):
                self.exercises.append(ExerciseResult(**ex_dict))
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
        self._save_json()
        self.generate_asciidoc()
        self.generate_csv()

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
        """Format seconds into a human-readable string like '1m 30s' or '45s'."""
        if seconds <= 0:
            return ""
        minutes = int(seconds) // 60
        secs = int(seconds) % 60
        if minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    @staticmethod
    def _chapter_number(chapter_section: str) -> str:
        """Extract chapter number from a chapter_section like 'ch02s04' → '2'."""
        match = re.match(r"ch(\d+)", chapter_section)
        return str(int(match.group(1))) if match else "?"

    # ------------------------------------------------------------------
    # AsciiDoc generation
    # ------------------------------------------------------------------

    def generate_asciidoc(self, output_path: str = None) -> str:
        """
        Write an AsciiDoc report with embedded screenshot references.

        Returns:
            The path to the generated .adoc file.
        """
        if output_path is None:
            output_path = str(self.report_dir / f"qa_report_{self.course_id}.adoc")

        lines: list[str] = []

        # --- Header ---
        lines.append(f"= QA Report: {self.course_id}")
        lines.append(":imagesdir: screenshots")
        lines.append(":toc: left")
        lines.append(":toclevels: 2")
        lines.append("")
        lines.append("== Course Information")
        lines.append("")
        lines.append(f"Course:: {self.course_id}")
        lines.append(f"Environment:: {self.environment}")
        lines.append(f"Date:: {self.date}")
        if self.assignee:
            lines.append(f"Assignee:: {self.assignee}")
        lines.append("")

        # --- Summary table ---
        lines.append("== Summary")
        lines.append("")
        lines.append('[cols="4,1,1,1,1", options="header"]')
        lines.append("|===")
        lines.append("| Exercise | Result | Start time | Grade time | Finish time")
        lines.append("")
        for ex in self.exercises:
            result_str = ex.grade_result if ex.grade_result else "-"
            start_str = self._format_duration(ex.start_duration_secs) or "-"
            grade_str = self._format_duration(ex.grade_duration_secs) or "-"
            finish_str = self._format_duration(ex.finish_duration_secs) or "-"
            lines.append(f"| {ex.title}")
            lines.append(f"| {result_str}")
            lines.append(f"| {start_str}")
            lines.append(f"| {grade_str}")
            lines.append(f"| {finish_str}")
            lines.append("")
        lines.append("|===")
        lines.append("")

        # --- Per-exercise detail grouped by chapter ---
        current_chapter = None
        for ex in self.exercises:
            ch = self._chapter_number(ex.chapter_section)
            if ch != current_chapter:
                current_chapter = ch
                lines.append(f"== Chapter {current_chapter}")
                lines.append("")

            lines.append(f"=== {ex.title}")
            lines.append("")

            result_label = ex.grade_result if ex.grade_result else "_(pending)_"
            lines.append(f"Result:: *{result_label}*")
            if ex.start_duration_secs > 0:
                lines.append(f"Start script time:: {self._format_duration(ex.start_duration_secs)}")
            if ex.grade_duration_secs > 0:
                lines.append(f"Grade script time:: {self._format_duration(ex.grade_duration_secs)}")
            if ex.finish_duration_secs > 0:
                lines.append(f"Finish script time:: {self._format_duration(ex.finish_duration_secs)}")
            if ex.notes:
                lines.append(f"Notes:: {ex.notes}")
            lines.append("")

            # Screenshots
            for phase, path in [("Lab Start", ex.start_screenshot),
                                ("Mid-Exercise", ex.mid_screenshot),
                                ("Lab Grade", ex.grade_screenshot),
                                ("Lab Finish", ex.finish_screenshot)]:
                if path and os.path.isfile(path):
                    # AsciiDoc references relative to :imagesdir:
                    filename = os.path.basename(path)
                    lines.append(f".{phase}")
                    lines.append(f"image::{filename}[{phase}, 800]")
                    lines.append("")

        content = "\n".join(lines)
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
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for ex in self.exercises:
                writer.writerow({
                    "Guided Exercise/Lab Name": ex.title,
                    "PASS/FAIL": ex.grade_result,
                    "start script time": self._format_duration(ex.start_duration_secs),
                    "grade script time": self._format_duration(ex.grade_duration_secs),
                    "finish script time": self._format_duration(ex.finish_duration_secs),
                    "Notes and Concerns": ex.notes,
                })

        return output_path
