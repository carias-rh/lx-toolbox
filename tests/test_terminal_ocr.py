"""Unit tests for Lab Script `time real` parsing from terminal OCR."""

from lx_toolbox.utils.terminal_ocr import parse_terminal_timing_text


class TestParseTerminalTimingText:
    def test_bash_time_real_line(self):
        text = (
            "[student@workstation ~]$ cd; date; time lab start deploy-review\n"
            "Wed Oct 21 15:18:27 UTC 2026\n"
            "Running: lab start deploy-review\n"
            "real    0m20.650s\n"
            "user    0m1.560s\n"
            "sys     0m0.265s\n"
        )
        parsed = parse_terminal_timing_text(text)
        assert parsed is not None
        assert parsed.real_seconds == 20.65
        assert parsed.started_at == "Wed Oct 21 15:18:27 UTC 2026"

    def test_slashed_zero_glyphs_on_real_line(self):
        # GNOME Terminal slashed zeros come out of tesseract as Q/@/O.
        text = "real    Qm2@.650s\nuser    Qm1.560s\nsys     Om@.265s\n"
        parsed = parse_terminal_timing_text(text)
        assert parsed is not None
        assert parsed.real_seconds == 20.65

    def test_capital_s_is_five_in_duration(self):
        parsed = parse_terminal_timing_text("real    @mS.427s\n")
        assert parsed is not None
        assert parsed.real_seconds == 5.427

    def test_leading_quote_on_real_line(self):
        parsed = parse_terminal_timing_text("'real    Qm3 .983s\n")
        assert parsed is not None
        assert parsed.real_seconds == 3.983

    def test_missing_leading_zero_and_s_as_five(self):
        parsed = parse_terminal_timing_text("real    mS .844s\n")
        assert parsed is not None
        assert parsed.real_seconds == 5.844

    def test_spaces_and_trailing_five_as_seconds_unit(self):
        parsed = parse_terminal_timing_text("real    @m3 . 4915\n")
        assert parsed is not None
        assert parsed.real_seconds == 3.491

    def test_trailing_junk_after_five_as_seconds_unit(self):
        parsed = parse_terminal_timing_text(
            "real    @m3 . 4915                                                                       I\n"
        )
        assert parsed is not None
        assert parsed.real_seconds == 3.491
