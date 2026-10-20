"""PF5 lab-card footer order is Start/Stop then Delete; callers expect Delete first."""

from unittest.mock import MagicMock, patch

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


def _btn(text, analytics_id=None):
    button = MagicMock()
    button.text = text
    button.get_attribute.side_effect = lambda name: analytics_id if name == "data-analytics-id" else None
    return button


def _buttons(mgr, elements):
    with patch("lx_toolbox.core.lab_manager.WebDriverWait") as wait_cls:
        wait_cls.return_value.until.return_value = elements
        return mgr._get_lab_buttons_by_position()


def test_pf5_stopped_lab_maps_delete_first_and_start_second():
    mgr = _manager()
    start = _btn("Start", LabManager._LAB_START_ANALYTICS_ID)
    delete = _btn("Delete", LabManager._LAB_DELETE_ANALYTICS_ID)

    first, first_text, second, second_text = _buttons(mgr, [start, delete])

    assert first is delete
    assert first_text == "DELETE"
    assert second is start
    assert second_text == "START"


def test_pf5_running_lab_maps_delete_first_and_stop_second():
    mgr = _manager()
    stop = _btn("Stop", LabManager._LAB_STOP_ANALYTICS_ID)
    delete = _btn("Delete", LabManager._LAB_DELETE_ANALYTICS_ID)

    first, first_text, second, second_text = _buttons(mgr, [stop, delete])

    assert first is delete
    assert first_text == "DELETE"
    assert second is stop
    assert second_text == "STOP"


def test_pf5_starting_lab_maps_delete_first_and_starting_second():
    mgr = _manager()
    starting = _btn("Starting", LabManager._LAB_START_ANALYTICS_ID)
    delete = _btn("Delete", LabManager._LAB_DELETE_ANALYTICS_ID)

    _, first_text, _, second_text = _buttons(mgr, [starting, delete])

    assert first_text == "DELETE"
    assert second_text == "STARTING"


def test_pf5_no_lab_maps_create_first():
    mgr = _manager()
    create = _btn("Create", LabManager._LAB_CREATE_ANALYTICS_ID)

    first, first_text, second, second_text = _buttons(mgr, [create])

    assert first is create
    assert first_text == "CREATE"
    assert second is None
    assert second_text is None


def test_old_interface_keeps_dom_order():
    mgr = _manager()
    delete = _btn("Delete")
    stop = _btn("Stop")

    first, first_text, second, second_text = _buttons(mgr, [delete, stop])

    assert first is delete
    assert first_text == "DELETE"
    assert second is stop
    assert second_text == "STOP"


def test_pf5_stop_and_delete_actions_use_semantic_positions():
    mgr = _manager()
    stop = _btn("Stop", LabManager._LAB_STOP_ANALYTICS_ID)
    delete = _btn("Delete", LabManager._LAB_DELETE_ANALYTICS_ID)

    with patch("lx_toolbox.core.lab_manager.WebDriverWait") as wait_cls:
        wait_cls.return_value.until.return_value = [stop, delete]
        stop_btn, stop_text = mgr._get_lab_action_button(["Stop"], position="second")
        delete_btn, delete_text = mgr._get_lab_action_button(["Delete"], position="first")

    assert stop_btn is stop
    assert stop_text == "STOP"
    assert delete_btn is delete
    assert delete_text == "DELETE"


def test_pf5_start_action_uses_second_position_when_stopped():
    mgr = _manager()
    start = _btn("Start", LabManager._LAB_START_ANALYTICS_ID)
    delete = _btn("Delete", LabManager._LAB_DELETE_ANALYTICS_ID)

    with patch("lx_toolbox.core.lab_manager.WebDriverWait") as wait_cls:
        wait_cls.return_value.until.return_value = [start, delete]
        start_btn, start_text = mgr._get_lab_action_button(["Start"], position="second")

    assert start_btn is start
    assert start_text == "START"
