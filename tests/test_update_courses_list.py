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


def test_trailing_l_skus_are_lessons_course_skus_are_not():
    """Lessons append L after the digits (rh0006l); course families like cl110 do not."""
    assert LabManager.is_lesson_sku("rh0006l")
    assert LabManager.is_lesson_sku("rh0006l-10.0")
    assert LabManager.is_lesson_sku("AU0013L")
    assert not LabManager.is_lesson_sku("cl110")
    assert not LabManager.is_lesson_sku("rh124-10.0")
    assert not LabManager.is_lesson_sku("do336-4.16")
    assert not LabManager.is_lesson_sku("tl250")


def test_update_courses_list_scrapes_lessons_before_courses(tmp_path):
    """Lessons are collected from catalog URLs first; courses are version-fetched after."""
    existing = tmp_path / "courses-list.txt"
    existing.write_text("do180-4.18\n")
    mgr = _manager()
    mgr._scrape_catalog_format = MagicMock(side_effect=[
        ["rh0006l-10.0"],
        ["do180-4.18"],
    ])

    mgr.update_courses_list("rol", output_path=existing)

    formats = [call.args[0] for call in mgr._scrape_catalog_format.call_args_list]
    assert formats == ["Lesson", "Course"]
    assert existing.read_text() == "do180-4.18\nrh0006l-10.0\n"


def test_lesson_entries_keep_catalog_href_without_opening_settings():
    """Lessons keep the versioned Continue href; only courses visit the settings dropdown."""
    mgr = _manager()
    mgr.go_to_catalog = MagicMock()
    mgr.filter_by_format = MagicMock()
    mgr.get_all_courses = MagicMock(return_value=[
        {
            "id": "rh0006l-10.0",
            "sku": "rh0006l",
            "title": "A lesson",
            "url": "/rol/app/courses/rh0006l-10.0",
        },
        {
            "id": "do336-4.16",
            "sku": "do336",
            "title": "A course",
            "url": "/rol/app/courses/do336-4.16",
        },
    ])
    mgr.get_available_versions = MagicMock(return_value=["4.16", "4.14"])

    with patch("lx_toolbox.core.lab_manager.time.sleep"):
        entries = mgr._scrape_catalog_format("Course", "rol", {})

    mgr.get_available_versions.assert_called_once_with("do336-4.16", "rol")
    assert "rh0006l-10.0" in entries
    assert "do336-4.16" in entries
    assert "do336-4.14" in entries


def test_lesson_format_uses_catalog_hrefs_only():
    """A Lesson scrape must not open settings; the Continue URL already has the version."""
    mgr = _manager()
    mgr.go_to_catalog = MagicMock()
    mgr.filter_by_format = MagicMock()
    mgr.get_all_courses = MagicMock(return_value=[
        {
            "id": "au0013l-9.4",
            "sku": "au0013l",
            "title": "Accessing Block-based Network Storage",
            "url": "/rol/app/courses/au0013l-9.4",
        },
    ])
    mgr.get_available_versions = MagicMock(return_value=["9.4", "8.0"])

    with patch("lx_toolbox.core.lab_manager.time.sleep"):
        entries = mgr._scrape_catalog_format("Lesson", "rol", {})

    mgr.get_available_versions.assert_not_called()
    assert entries == ["au0013l-9.4"]


def test_filter_by_format_checks_only_the_requested_checkbox():
    """A leftover Course check must be cleared when filtering to Lesson."""
    mgr = _manager()
    delivery_btn = MagicMock()
    delivery_btn.is_displayed.return_value = True
    delivery_btn.get_attribute.return_value = "true"

    lesson_cb = MagicMock()
    lesson_cb.is_selected.return_value = False
    lesson_cb.get_attribute.side_effect = lambda name: (
        "module_checkbox" if name == "id" else "module-deliveryFormats-filt-cat-lp"
    )

    course_cb = MagicMock()
    course_cb.is_selected.return_value = True
    course_cb.get_attribute.side_effect = lambda name: (
        "course_checkbox" if name == "id" else "course-deliveryFormats-filt-cat-lp"
    )

    mgr.driver.find_element.return_value = delivery_btn
    mgr.driver.find_elements.return_value = [lesson_cb, course_cb]

    with patch("lx_toolbox.core.lab_manager.WebDriverWait") as wait_cls, patch(
        "lx_toolbox.core.lab_manager.time.sleep"
    ):
        wait_cls.return_value.until.side_effect = [delivery_btn, lesson_cb]
        mgr.filter_by_format("Lesson")

    clicked = [
        args[1]
        for args, _kwargs in mgr.driver.execute_script.call_args_list
        if args and "click" in str(args[0])
    ]
    assert lesson_cb in clicked
    assert course_cb in clicked


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
