"""Unit tests for QA Report generation, Defect URL parsing, and DOCX tints."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from lx_toolbox.core.qa_report import (
    W,
    DefectDetails,
    ExerciseResult,
    QAReport,
    apply_pass_fail_tints,
    box_screenshot_tables,
    defect_has_development,
    defect_key,
    display_defect_title,
    parse_defect_refs,
    section_short,
)
from lx_toolbox.utils.terminal_ocr import ParsedTerminalTiming


def _report(tmp_path: Path, **kwargs) -> QAReport:
    return QAReport(
        course_id=kwargs.pop("course_id", "rh342-10.2"),
        environment=kwargs.pop("environment", "factory"),
        report_dir=tmp_path,
        **kwargs,
    )


def _touch_shot(report: QAReport, chapter_section: str, phase: str) -> str:
    path = report.screenshot_path(chapter_section, phase)
    Path(path).write_bytes(b"")
    return path


class TestParseDefectRefs:
    def test_empty_is_none(self):
        assert parse_defect_refs("") == []
        assert parse_defect_refs("   ") == []
        assert parse_defect_refs(None) == []  # type: ignore[arg-type]

    def test_bare_key(self):
        assert parse_defect_refs("PTL-12345") == [
            "https://issues.redhat.com/browse/PTL-12345"
        ]

    def test_lowercase_key_is_canonicalised(self):
        assert parse_defect_refs("ptl-99") == [
            "https://issues.redhat.com/browse/PTL-99"
        ]

    def test_browse_url(self):
        url = "https://issues.redhat.com/browse/PTL-12345"
        assert parse_defect_refs(url) == [url]

    def test_multiple_mixed(self):
        raw = "PTL-1, https://issues.redhat.com/browse/PTL-2 PTL-3"
        assert parse_defect_refs(raw) == [
            "https://issues.redhat.com/browse/PTL-1",
            "https://issues.redhat.com/browse/PTL-2",
            "https://issues.redhat.com/browse/PTL-3",
        ]

    def test_dedupes_key_and_url(self):
        raw = "PTL-1 https://issues.redhat.com/browse/PTL-1"
        assert parse_defect_refs(raw) == [
            "https://issues.redhat.com/browse/PTL-1"
        ]

    def test_keeps_other_http_urls(self):
        raw = "https://example.invalid/ticket/1"
        assert parse_defect_refs(raw) == [raw]

    def test_defect_key_from_url(self):
        assert defect_key("https://issues.redhat.com/browse/PTL-9") == "PTL-9"

    def test_cdev_key_uses_cloud_host(self):
        assert parse_defect_refs("CDEV-19707") == [
            "https://redhat.atlassian.net/browse/CDEV-19707"
        ]

    def test_cdev_cloud_url_canonicalised(self):
        url = "https://redhat.atlassian.net/browse/CDEV-19707"
        assert parse_defect_refs(url) == [url]
        assert defect_key(url) == "CDEV-19707"


class TestRenderAsciidoc:
    def test_summary_links_to_section_anchor(self, tmp_path):
        report = _report(tmp_path)
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s04",
            title="Section 1.4: Guided Exercise: Configure Remote Logging",
            grade_result="PASS",
            start_duration_secs=16,
            finish_duration_secs=9,
        ))
        adoc = report.render_asciidoc()
        assert "<<ch01s04,Section 1.4: Guided Exercise: Configure Remote Logging>>" in adoc
        assert "[[ch01s04]]" in adoc
        assert "=== Section 1.4: Guided Exercise: Configure Remote Logging" in adoc
        assert ":toc!:" in adoc

    def test_pass_fail_markup_and_row_tint(self, tmp_path):
        report = _report(tmp_path)
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s02",
            title="Section 1.2: Guided Exercise: A",
            grade_result="PASS",
        ))
        report.add_exercise(ExerciseResult(
            chapter_section="ch03s05",
            title="Section 3.5: Lab: B",
            grade_result="FAIL",
        ))
        adoc = report.render_asciidoc()
        assert "| PASS" in adoc or "| {set:cellbgcolor:#C6EFCE}PASS" in adoc
        assert "| FAIL" in adoc or "| {set:cellbgcolor:#FFC7CE}FAIL" in adoc
        assert "{set:cellbgcolor:#C6EFCE}" in adoc
        assert "{set:cellbgcolor:#FFC7CE}" in adoc
        assert "[green]#" not in adoc
        assert "[red]#" not in adoc

    def test_ge_omits_grade_column_lab_keeps_it(self, tmp_path):
        report = _report(tmp_path)
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s04",
            title="Section 1.4: Guided Exercise: X",
            grade_result="PASS",
            start_duration_secs=16,
            finish_duration_secs=9,
        ))
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s07",
            title="Section 1.7: Lab: Y",
            grade_result="PASS",
            start_duration_secs=11,
            grade_duration_secs=11,
            finish_duration_secs=12,
        ))
        adoc = report.render_asciidoc()
        assert "| QA Result | Start | Finish" in adoc
        assert "| QA Result | Start | Grade | Finish" in adoc

    def test_defects_section_hidden_when_empty(self, tmp_path):
        report = _report(tmp_path)
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s02",
            title="Section 1.2: Guided Exercise: A",
            grade_result="PASS",
        ))
        adoc = report.render_asciidoc()
        assert "== Defects" not in adoc

    def test_defects_listed_globally_and_per_section(self, tmp_path):
        report = _report(tmp_path)
        url = "https://issues.redhat.com/browse/PTL-99"
        report.add_exercise(ExerciseResult(
            chapter_section="ch03s05",
            title="Section 3.5: Lab: B",
            grade_result="FAIL",
            defects=[url],
        ))
        adoc = report.render_asciidoc(defect_details={
            url: DefectDetails(
                url=url,
                key="PTL-99",
                title="key is in workstation, not satellite host",
                fixed=True,
            ),
        })
        assert "== Defects" in adoc
        assert "| Key | Section | Title | Fixed defect" in adoc
        assert "<<ch03s05,3.5>>" in adoc
        assert "key is in workstation, not satellite host" in adoc
        assert "| YES" in adoc
        assert "https://issues.redhat.com/browse/PTL-99[PTL-99]" in adoc
        assert "Defects:: https://issues.redhat.com/browse/PTL-99[PTL-99]" in adoc
        # QA Result is the row tint, not a Defects column
        assert "| QA Result |" not in adoc.split("== Defects", 1)[1].split("== Chapter", 1)[0]

    def test_boxed_screenshot_caption_below(self, tmp_path):
        report = _report(tmp_path)
        start = _touch_shot(report, "ch01s04", "start")
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s04",
            title="Section 1.4: Guided Exercise: X",
            grade_result="PASS",
            start_screenshot=start,
        ))
        adoc = report.render_asciidoc()
        assert "frame=all" in adoc
        assert "image::ch01s04_start.png[lab start, 800]" in adoc
        # Caption is the table row after the image, not an AsciiDoc block title.
        assert ".lab start" not in adoc
        assert "| *lab start*" in adoc
        image_at = adoc.index("image::ch01s04_start.png")
        caption_at = adoc.index("| *lab start*")
        assert image_at < caption_at

    def test_old_json_without_defects_loads(self, tmp_path):
        (tmp_path / "qa_data.json").write_text(
            '{"course_id": "rh342-10.2", "environment": "factory",'
            ' "assignee": "", "date": "2026-08-04", "exercises": ['
            '{"chapter_section": "ch01s02", "title": "Section 1.2: A",'
            ' "grade_result": "PASS"}]}',
            encoding="utf-8",
        )
        report = _report(tmp_path)
        assert len(report.exercises) == 1
        assert report.exercises[0].defects == []
        assert report.exercises[0].grade_result == "PASS"

    def test_csv_has_defects_column(self, tmp_path):
        report = _report(tmp_path)
        report.add_exercise(ExerciseResult(
            chapter_section="ch03s05",
            title="Section 3.5: Lab: B",
            grade_result="FAIL",
            defects=["https://issues.redhat.com/browse/PTL-99"],
        ))
        csv_path = report.generate_csv()
        text = Path(csv_path).read_text(encoding="utf-8")
        assert "Defects" in text.splitlines()[0]
        assert "https://issues.redhat.com/browse/PTL-99" in text


class TestApplyScriptTime:
    def test_records_ocr_real_and_ignores_any_prior_wait(self, tmp_path, monkeypatch):
        report = _report(tmp_path)
        start = _touch_shot(report, "ch01s11", "start")
        ex = ExerciseResult(
            chapter_section="ch01s11",
            title="Section 1.11: Lab: Plan and Deploy Red Hat Satellite",
            start_duration_secs=5488.376,
            start_duration_source="manual",
        )
        report.add_exercise(ex)
        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
            lambda path: ParsedTerminalTiming(real_seconds=20.65),
        )
        assert report.apply_script_time_from_screenshot(ex, "start", start) is True
        assert ex.start_duration_secs == 20.65
        assert ex.start_duration_source == "ocr"

    def test_clears_duration_when_screenshot_has_no_real_line(self, tmp_path, monkeypatch):
        report = _report(tmp_path)
        start = _touch_shot(report, "ch01s11", "start")
        ex = ExerciseResult(
            chapter_section="ch01s11",
            title="Section 1.11: Lab: Plan and Deploy Red Hat Satellite",
            start_duration_secs=5488.376,
            start_duration_source="manual",
        )
        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
            lambda path: None,
        )
        assert report.apply_script_time_from_screenshot(ex, "start", start) is False
        assert ex.start_duration_secs == 0.0
        assert ex.start_duration_source != "manual"


class TestRefreshScriptTimes:
    def test_screenshot_real_replaces_wall_clock(self, tmp_path, monkeypatch):
        report = _report(tmp_path, course_id="rh403-6.18")
        start = _touch_shot(report, "ch01s11", "start")
        grade = _touch_shot(report, "ch01s11", "grade")
        finish = _touch_shot(report, "ch01s11", "finish")
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s11",
            title="Section 1.11: Lab: Plan and Deploy Red Hat Satellite",
            grade_result="PASS",
            start_screenshot=start,
            grade_screenshot=grade,
            finish_screenshot=finish,
            start_duration_secs=5488.376,
            grade_duration_secs=755.575,
            finish_duration_secs=179.929,
            start_duration_source="manual",
            grade_duration_source="manual",
            finish_duration_source="manual",
        ))

        def fake_parse(path):
            path = str(path)
            if path.endswith("_start.png"):
                return ParsedTerminalTiming(real_seconds=20.65)
            if path.endswith("_grade.png"):
                return ParsedTerminalTiming(real_seconds=12.35)
            if path.endswith("_finish.png"):
                return ParsedTerminalTiming(real_seconds=2.59)
            return None

        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
            fake_parse,
        )
        updated = report.refresh_script_times_from_screenshots()
        assert updated == 3
        ex = report.exercises[0]
        assert ex.start_duration_secs == 20.65
        assert ex.grade_duration_secs == 12.35
        assert ex.finish_duration_secs == 2.59
        assert ex.start_duration_source == "ocr"

        adoc = report.render_asciidoc()
        assert "91m 28s" not in adoc
        assert "12m 35s" not in adoc
        assert "| 20s " in adoc
        assert "| 12s " in adoc
        assert "| 2s" in adoc

    def test_keeps_wall_clock_when_ocr_finds_nothing(self, tmp_path, monkeypatch):
        report = _report(tmp_path)
        start = _touch_shot(report, "ch01s04", "start")
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s04",
            title="Section 1.4: Guided Exercise: X",
            start_screenshot=start,
            start_duration_secs=237.5,
            start_duration_source="manual",
        ))
        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
            lambda path: None,
        )
        assert report.refresh_script_times_from_screenshots() == 0
        assert report.exercises[0].start_duration_secs == 237.5
        assert report.exercises[0].start_duration_source == "manual"

    def test_skips_phases_already_read_from_ocr(self, tmp_path, monkeypatch):
        report = _report(tmp_path)
        start = _touch_shot(report, "ch01s04", "start")
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s04",
            title="Section 1.4: Guided Exercise: X",
            start_screenshot=start,
            start_duration_secs=16.0,
            start_duration_source="ocr",
        ))
        calls = []

        def fake_parse(path):
            calls.append(path)
            return ParsedTerminalTiming(real_seconds=99.0)

        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
            fake_parse,
        )
        assert report.refresh_script_times_from_screenshots() == 0
        assert calls == []
        assert report.exercises[0].start_duration_secs == 16.0

    def test_save_writes_ocr_times_into_json_and_adoc(self, tmp_path, monkeypatch):
        report = _report(tmp_path, course_id="rh403-6.18")
        start = _touch_shot(report, "ch01s11", "start")
        report.add_exercise(ExerciseResult(
            chapter_section="ch01s11",
            title="Section 1.11: Lab: Plan and Deploy Red Hat Satellite",
            grade_result="PASS",
            start_screenshot=start,
            start_duration_secs=5488.376,
            start_duration_source="manual",
        ))
        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
            lambda path: ParsedTerminalTiming(real_seconds=20.65),
        )
        monkeypatch.setattr(
            "lx_toolbox.core.qa_report.fetch_jira_defect_details",
            lambda urls: {},
        )
        report.save()
        data = json.loads((tmp_path / "qa_data.json").read_text(encoding="utf-8"))
        assert data["exercises"][0]["start_duration_secs"] == 20.65
        assert data["exercises"][0]["start_duration_source"] == "ocr"
        adoc = (tmp_path / "qa_report_rh403-6.18.adoc").read_text(encoding="utf-8")
        assert "91m 28s" not in adoc
        assert "20s" in adoc


class TestDefectDisplay:
    def test_section_short(self):
        assert section_short("ch09s02") == "9.2"
        assert section_short("ch01s10") == "1.10"
        assert section_short("ch12s04") == "12.4"

    def test_display_title_strips_sku_and_section(self):
        assert display_defect_title(
            "RH403: ch07s04 - job runs on capsule"
        ) == "job runs on capsule"
        assert display_defect_title(
            "ch08s05: step 1.4 repository names"
        ) == "step 1.4 repository names"

    def test_development_summary_with_merged_pr_is_fixed(self):
        payload = {
            "summary": {
                "pullrequest": {"overall": {"count": 1, "state": "MERGED"}},
                "repository": {"overall": {"count": 1}},
                "branch": {"overall": {"count": 0}},
            }
        }
        assert defect_has_development(payload) is True

    def test_empty_development_is_not_fixed(self):
        payload = {
            "summary": {
                "pullrequest": {"overall": {"count": 0}},
                "repository": {"overall": {"count": 0}},
                "branch": {"overall": {"count": 0}},
            }
        }
        assert defect_has_development(payload) is False


class TestPassFailTints:
    def test_tints_pass_and_fail_rows(self):
        xml = f'''
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:tbl>
              <w:tr>
                <w:tc><w:p><w:r><w:t>Exercise</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>QA Result</w:t></w:r></w:p></w:tc>
              </w:tr>
              <w:tr>
                <w:tc><w:p><w:r><w:t>Section 1.2</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>PASS</w:t></w:r></w:p></w:tc>
              </w:tr>
              <w:tr>
                <w:tc><w:p><w:r><w:t>Section 3.5</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>FAIL</w:t></w:r></w:p></w:tc>
              </w:tr>
            </w:tbl>
          </w:body>
        </w:document>
        '''
        root = ET.fromstring(xml)
        assert apply_pass_fail_tints(root) == 2
        fills = [
            shd.get(f"{W}fill")
            for shd in root.findall(f".//{W}shd")
        ]
        assert "C6EFCE" in fills
        assert "FFC7CE" in fills
        colors = [
            el.get(f"{W}val")
            for el in root.findall(f".//{W}color")
        ]
        assert "006100" in colors
        assert "9C0006" in colors

    def test_tints_defects_row_from_section_hyperlink(self):
        xml = f'''
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:tbl>
              <w:tr>
                <w:tc>
                  <w:p><w:hyperlink w:anchor="ch07s04"><w:r><w:t>Section 7.4</w:t></w:r></w:hyperlink></w:p>
                </w:tc>
                <w:tc><w:p><w:r><w:t>FAIL</w:t></w:r></w:p></w:tc>
              </w:tr>
            </w:tbl>
            <w:tbl>
              <w:tr>
                <w:tc><w:p><w:r><w:t>CDEV-19663</w:t></w:r></w:p></w:tc>
                <w:tc>
                  <w:p><w:hyperlink w:anchor="ch07s04"><w:r><w:t>7.4</w:t></w:r></w:hyperlink></w:p>
                </w:tc>
                <w:tc><w:p><w:r><w:t>job runs on capsule</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>NO</w:t></w:r></w:p></w:tc>
              </w:tr>
            </w:tbl>
          </w:body>
        </w:document>
        '''
        root = ET.fromstring(xml)
        assert apply_pass_fail_tints(root) == 2
        defect_row = root.findall(f".//{W}tr")[1]
        fills = [shd.get(f"{W}fill") for shd in defect_row.findall(f".//{W}shd")]
        assert fills and all(f == "FFC7CE" for f in fills)

    def test_boxes_screenshot_tables(self):
        xml = f'''
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:tbl>
              <w:tr>
                <w:tc><w:p><w:r><w:drawing /></w:r></w:p></w:tc>
              </w:tr>
              <w:tr>
                <w:tc><w:p><w:r><w:t>lab start</w:t></w:r></w:p></w:tc>
              </w:tr>
            </w:tbl>
          </w:body>
        </w:document>
        '''
        root = ET.fromstring(xml)
        assert box_screenshot_tables(root) == 1
        borders = root.find(f".//{W}tblPr/{W}tblBorders")
        assert borders is not None
        top = borders.find(f"{W}top")
        assert top.get(f"{W}val") == "single"
        inside = borders.find(f"{W}insideH")
        assert inside.get(f"{W}val") == "nil"
