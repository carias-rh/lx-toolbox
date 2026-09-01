"""Show Solution clicks must terminate on quiz pages where the button label never changes."""

from unittest.mock import MagicMock, patch

from selenium.common.exceptions import TimeoutException

from lx_toolbox.core.lab_manager import LabManager


def _manager():
    with patch("lx_toolbox.core.lab_manager.BaseSeleniumDriver") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.get_driver.return_value = MagicMock()
        mock_instance.wait = MagicMock()
        mock_cls.return_value = mock_instance
        config = MagicMock()
        config.get.side_effect = lambda *args, **kwargs: args[2] if len(args) > 2 else kwargs.get("default")
        return LabManager(config=config, browser_name="firefox", is_headless=True)


def _quiz_button(analytics_id="show-btn-resequencing-ole-lp-rh342-8.4-ch06s08"):
    button = MagicMock()
    button.get_attribute.side_effect = lambda name: analytics_id if name == "data-analytics-id" else None
    button.id = "selenium-elem-quiz"
    return button


def test_quiz_show_solution_that_keeps_its_label_is_clicked_once():
    """Resequencing quizzes keep 'Show Solution' after click; do not loop forever."""
    mgr = _manager()
    button = _quiz_button()
    find_calls = {"n": 0}

    def fake_find(by, xpath):
        find_calls["n"] += 1
        if find_calls["n"] > 20:
            raise RuntimeError("click_on_show_solution_buttons did not terminate")
        return [button]

    mgr.driver.find_elements.side_effect = fake_find

    with patch("lx_toolbox.core.lab_manager.time.sleep"), patch(
        "lx_toolbox.core.lab_manager.WebDriverWait"
    ) as wait_cls:
        wait_cls.return_value.until.return_value = button
        mgr.click_on_show_solution_buttons()

    assert button.click.call_count == 1
    assert find_calls["n"] <= 5


def test_unclickable_show_solution_does_not_loop():
    """A Show Solution button that never becomes clickable must still exit."""
    mgr = _manager()
    button = _quiz_button("show-btn-stuck")
    find_calls = {"n": 0}

    def fake_find(by, xpath):
        find_calls["n"] += 1
        if find_calls["n"] > 20:
            raise RuntimeError("click_on_show_solution_buttons did not terminate")
        return [button]

    mgr.driver.find_elements.side_effect = fake_find

    with patch("lx_toolbox.core.lab_manager.time.sleep"), patch(
        "lx_toolbox.core.lab_manager.WebDriverWait"
    ) as wait_cls:
        wait_cls.return_value.until.side_effect = TimeoutException()
        mgr.click_on_show_solution_buttons()

    assert find_calls["n"] <= 5


def test_new_show_solution_revealed_after_click_is_also_clicked():
    """Expanding one solution may reveal another; click each distinct button once."""
    mgr = _manager()
    first = _quiz_button("show-btn-a")
    second = _quiz_button("show-btn-b")
    remaining = [first, second]
    find_calls = {"n": 0}

    def fake_find(by, xpath):
        find_calls["n"] += 1
        if find_calls["n"] > 20:
            raise RuntimeError("click_on_show_solution_buttons did not terminate")
        return list(remaining)

    def fake_click():
        remaining[:] = [b for b in remaining if b is not first]

    first.click.side_effect = fake_click
    mgr.driver.find_elements.side_effect = fake_find

    with patch("lx_toolbox.core.lab_manager.time.sleep"), patch(
        "lx_toolbox.core.lab_manager.WebDriverWait"
    ) as wait_cls:
        wait_cls.return_value.until.side_effect = lambda *_args, **_kwargs: remaining[-1]
        mgr.click_on_show_solution_buttons()

    assert first.click.call_count == 1
    assert second.click.call_count == 1
