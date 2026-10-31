"""Live QA records Lab Script time from screenshot OCR, never button-wait."""

from unittest.mock import MagicMock, patch

from lx_toolbox.core.lab_manager import LabManager
from lx_toolbox.core.qa_report import ExerciseResult, QAReport
from lx_toolbox.utils.terminal_ocr import ParsedTerminalTiming


def _manager():
    with patch("lx_toolbox.core.lab_manager.BaseSeleniumDriver") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.get_driver.return_value = MagicMock()
        mock_instance.wait = MagicMock()
        mock_cls.return_value = mock_instance
        config = MagicMock()
        config.get.side_effect = lambda *args, **kwargs: args[2] if len(args) > 2 else kwargs.get("default")
        return LabManager(config=config, browser_name="firefox", is_headless=True)


def test_qa_records_ocr_time_not_button_wait(tmp_path, monkeypatch):
    mgr = _manager()
    report = QAReport(course_id="rh403-6.18", environment="factory", report_dir=tmp_path)
    path = report.screenshot_path("ch01s11", "start")
    ex = ExerciseResult(
        chapter_section="ch01s11",
        title="Section 1.11: Lab: Plan and Deploy Red Hat Satellite",
        start_duration_secs=9999,
        start_duration_source="manual",
    )
    report.add_exercise(ex)
    mgr._qa_report = report
    monkeypatch.setattr(mgr, "_screenshot_monitor_console", lambda p: None)
    monkeypatch.setattr(
        "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
        lambda p: ParsedTerminalTiming(real_seconds=20.65),
    )
    mgr._record_script_time(ex, "start", path, "when the start script has finished.")
    assert ex.start_duration_secs == 20.65
    assert ex.start_duration_source == "ocr"


def test_qa_retries_screenshot_until_real_line_is_visible(tmp_path, monkeypatch):
    mgr = _manager()
    report = QAReport(course_id="rh403-6.18", environment="factory", report_dir=tmp_path)
    path = report.screenshot_path("ch01s11", "start")
    ex = ExerciseResult(
        chapter_section="ch01s11",
        title="Section 1.11: Lab: Plan and Deploy Red Hat Satellite",
    )
    report.add_exercise(ex)
    mgr._qa_report = report
    shots = {"n": 0}
    prompts = []

    def fake_parse(p):
        shots["n"] += 1
        if shots["n"] == 1:
            return None
        return ParsedTerminalTiming(real_seconds=5.427)

    monkeypatch.setattr(mgr, "_screenshot_monitor_console", lambda p: None)
    monkeypatch.setattr(mgr, "_prompt_user_to_continue", lambda msg: prompts.append(msg))
    monkeypatch.setattr(
        "lx_toolbox.core.qa_report.parse_terminal_timing_from_screenshot",
        fake_parse,
    )
    mgr._record_script_time(ex, "start", path, "when the start script has finished.")
    assert shots["n"] == 2
    assert len(prompts) == 1
    assert ex.start_duration_secs == 5.427
    assert ex.start_duration_source == "ocr"
