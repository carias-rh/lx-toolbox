"""Catalog scrape must not wipe courses-list.txt, and must read the current ROL catalog."""

from unittest.mock import MagicMock, patch

import pytest

from lx_toolbox.core.lab_manager import LabManager


def _manager():
    with patch("lx_toolbox.core.lab_manager.BaseSeleniumDriver") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.get_driver.return_value = MagicMock()
        mock_instance.wait = MagicMock()
        mock_cls.return_value = mock_instance
        config = MagicMock()
        config.get.side_effect = lambda *args, **kwargs: args[2] if len(args) > 2 else kwargs.get("default")
        mgr = LabManager(config=config, browser_name="firefox", is_headless=True)
        mgr.logger = MagicMock()
        return mgr


def test_lesson_filter_uses_module_checkbox_id():
    """ROL still labels the format Lesson, but the checkbox id is module_checkbox."""
    assert LabManager.catalog_format_checkbox_id("Lesson") == "module_checkbox"
    assert LabManager.catalog_format_checkbox_id("Course") == "course_checkbox"


def test_continue_href_is_the_versioned_catalog_id():
    """Offering cards now expose Continue as a link whose href already includes the version."""
    assert (
        LabManager.versioned_id_from_offering_href("/rol/app/courses/do336-4.16")
        == "do336-4.16"
    )
    assert (
        LabManager.versioned_id_from_offering_href("/rol/app/courses/au0013l-9.4?foo=1")
        == "au0013l-9.4"
    )
    assert LabManager.versioned_id_from_offering_href("/rol/app/courses/do336") is None
    assert LabManager.versioned_id_from_offering_href(None) is None


def test_update_courses_list_keeps_existing_file_when_scrape_returns_nothing(tmp_path):
    """An empty catalog fetch must not replace the known list with a blank file."""
    existing = tmp_path / "courses-list.txt"
    existing.write_text("do180-4.18\nrh124-10.0\n")
    mgr = _manager()
    mgr._scrape_catalog_format = MagicMock(return_value=[])

    with pytest.raises(RuntimeError, match="no courses"):
        mgr.update_courses_list("rol", output_path=existing)

    assert existing.read_text() == "do180-4.18\nrh124-10.0\n"
    mgr.logger.assert_any_call(
        "Catalog scrape returned no entries; leaving existing courses-list.txt unchanged."
    )
