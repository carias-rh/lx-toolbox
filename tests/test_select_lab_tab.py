"""Lab Environment tab click must survive a PatternFly skeleton overlay."""

from unittest.mock import MagicMock, patch

from selenium.common.exceptions import ElementClickInterceptedException

from lx_toolbox.core.lab_manager import LabManager

SKELETON_INTERCEPT_MSG = (
    'Element <button id="pf-tab-lab_env-pf-17872188773664lnfoatfe87" '
    'class="pf-v5-c-tabs__link" type="button"> is not clickable at point '
    '(1472,267) because another element <div class="pf-v5-c-skeleton"> obscures it'
)


def _manager():
    with patch("lx_toolbox.core.lab_manager.BaseSeleniumDriver") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.get_driver.return_value = MagicMock()
        mock_instance.wait = MagicMock()
        mock_cls.return_value = mock_instance
        config = MagicMock()
        config.get.side_effect = lambda *args, **kwargs: args[2] if len(args) > 2 else kwargs.get("default")
        return LabManager(config=config, browser_name="firefox", is_headless=True)


def test_select_tab_new_interface_clicks_through_skeleton_overlay():
    """Native click is intercepted by pf-v5-c-skeleton; JS click should still select the tab."""
    mgr = _manager()
    tab = MagicMock()
    tab.click.side_effect = ElementClickInterceptedException(SKELETON_INTERCEPT_MSG)
    mgr.driver.find_element.return_value.get_attribute.return_value = "true"
    mgr.driver.execute_script.return_value = True

    wait_calls = {"n": 0}

    def fake_until(condition, message=""):
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            return tab
        if callable(condition):
            return condition(mgr.driver)
        return True

    with patch("lx_toolbox.core.lab_manager.WebDriverWait") as wait_cls:
        wait_cls.return_value.until.side_effect = fake_until
        mgr._select_tab_new_interface("Lab Environment", "lab-environment")

    click_calls = [
        args for args, _kwargs in mgr.driver.execute_script.call_args_list
        if args and "click" in str(args[0])
    ]
    assert click_calls, "expected a JavaScript click on the Lab Environment tab"
    assert click_calls[-1][1] is tab
