"""Jira Cloud create-dialog locators after the 2026 modernised form."""

from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from lx_toolbox.core.jira_handler import JiraHandler
from lx_toolbox.core.snow_ai_processor import SnowAIProcessor


class TestCreateDialogState:
    def test_ready_when_summary_field_id_exists_regardless_of_tag(self):
        driver = MagicMock()

        def find_elements(by, value):
            if by == By.ID and value == "summary-field":
                return [object()]
            return []

        driver.find_elements.side_effect = find_elements
        assert JiraHandler.create_dialog_state(driver) == "ready"

    def test_unauthorized_when_login_dialog_is_shown(self):
        dialog = MagicMock()
        dialog.text = "You are not authorized. Please log in."
        driver = MagicMock()

        def find_elements(by, value):
            if by == By.ID:
                return []
            if by == By.XPATH and "@role=\"dialog\"" in value:
                return [dialog]
            return []

        driver.find_elements.side_effect = find_elements
        assert JiraHandler.create_dialog_state(driver) == "unauthorized"

    def test_none_when_dialog_not_ready(self):
        driver = MagicMock()
        driver.find_elements.return_value = []
        assert JiraHandler.create_dialog_state(driver) is None


class TestModernisedLocators:
    def test_summary_is_targeted_by_id_not_input_tag(self):
        assert JiraHandler.SUMMARY_FIELD_ID == "summary-field"

    def test_description_editor_uses_aria_label_not_duplicate_ids(self):
        css = JiraHandler.DESCRIPTION_EDITOR_CSS
        assert "contenteditable" in css
        assert 'aria-label^="Description area"' in css

    def test_description_renderer_click_target_is_painted_adf(self):
        css = JiraHandler.DESCRIPTION_RENDERER_CSS
        assert "data-renderer-start-pos" in css
        assert "ak-renderer-wrapper" not in css

    def test_create_button_is_nav_testid_not_footer_submit(self):
        assert "atlassian-navigation--create-button" in JiraHandler.CREATE_BUTTON_CSS
        assert "footer" not in JiraHandler.CREATE_BUTTON_CSS

    def test_snow_ai_delegates_to_handler_selectors(self):
        assert SnowAIProcessor._jira_description_editor_selector() == (
            JiraHandler.DESCRIPTION_EDITOR_CSS
        )
        driver = MagicMock()
        driver.find_elements.return_value = [object()]
        assert SnowAIProcessor._jira_create_dialog_state(driver) == "ready"


GECKO_EMPTY = (
    "Message: \n"
    "Stacktrace:\n"
    "RemoteError@chrome://remote/content/shared/RemoteError.sys.mjs:8:8\n"
    "NoSuchElementError@chrome://remote/content/shared/webdriver/Errors.sys.mjs:538:5"
)


class TestFormatWebdriverError:
    def test_strips_empty_message_and_gecko_stack(self):
        exc = TimeoutException(GECKO_EMPTY)
        detail = JiraHandler.format_webdriver_error(exc)
        assert "chrome://remote" not in detail
        assert "Stacktrace" not in detail
        assert detail == "TimeoutException"

    def test_keeps_real_message_without_stack(self):
        exc = TimeoutException(
            "Message: no such element: Summary\nStacktrace:\nRemoteError@chrome://x"
        )
        detail = JiraHandler.format_webdriver_error(exc)
        assert "no such element: Summary" in detail
        assert "chrome://x" not in detail


class TestCreateDialogOpen:
    def _handler(self):
        handler = JiraHandler.__new__(JiraHandler)
        handler.driver = MagicMock()
        handler.driver.current_url = "https://redhat.atlassian.net/jira/software/c/projects/PTL/issues"
        handler.driver.find_elements.return_value = []
        return handler

    def test_dismiss_clicks_a_visible_accept_all_button(self):
        button = MagicMock()
        button.is_displayed.return_value = True
        driver = MagicMock()
        driver.find_elements.return_value = [button]
        assert JiraHandler.dismiss_atlassian_cookies(driver) is True
        driver.execute_script.assert_called_once()

    def test_dismiss_ignores_a_hidden_accept_all_button(self):
        button = MagicMock()
        button.is_displayed.return_value = False
        driver = MagicMock()
        driver.find_elements.return_value = [button]
        assert JiraHandler.dismiss_atlassian_cookies(driver) is False
        driver.execute_script.assert_not_called()

    def test_open_retries_immediately_after_the_cookie_banner(self):
        handler = self._handler()
        handler.click_create_button = MagicMock()
        with patch.object(JiraHandler, "dismiss_atlassian_cookies", return_value=False), \
             patch("lx_toolbox.core.jira_handler.WebDriverWait") as wait_cls:
            wait_cls.return_value.until.side_effect = ["cookies", "ready"]
            assert handler.open_create_until_state(timeout=25, attempts=4) == "ready"
        assert handler.click_create_button.call_count == 2

    def test_open_does_not_click_again_when_the_first_wait_succeeds(self):
        handler = self._handler()
        handler.click_create_button = MagicMock()
        with patch.object(JiraHandler, "dismiss_atlassian_cookies", return_value=False), \
             patch("lx_toolbox.core.jira_handler.WebDriverWait") as wait_cls:
            wait_cls.return_value.until.return_value = "ready"
            assert handler.open_create_until_state(timeout=25, attempts=4) == "ready"
        handler.click_create_button.assert_called_once()

    def test_open_raises_only_after_every_attempt_times_out(self):
        handler = self._handler()
        handler.click_create_button = MagicMock()
        with patch.object(JiraHandler, "dismiss_atlassian_cookies", return_value=False), \
             patch("lx_toolbox.core.jira_handler.WebDriverWait") as wait_cls:
            wait_cls.return_value.until.side_effect = TimeoutException(GECKO_EMPTY)
            with pytest.raises(RuntimeError, match="did not open after 4 attempts") as caught:
                handler.open_create_until_state(timeout=25, attempts=4)
        assert handler.click_create_button.call_count == 4
        assert "chrome://remote" not in str(caught.value)


class TestNamedPrefillFailures:
    def test_fill_summary_timeout_names_the_field(self):
        handler = JiraHandler.__new__(JiraHandler)
        handler.driver = MagicMock()
        with patch("lx_toolbox.core.jira_handler.WebDriverWait") as wait_cls:
            wait_cls.return_value.until.side_effect = TimeoutException(GECKO_EMPTY)
            with pytest.raises(RuntimeError, match="Summary field #summary-field") as caught:
                handler.fill_summary("AU294: ch01s04 - yaml string syntax")
        assert "chrome://remote" not in str(caught.value)

    def test_activate_description_timeout_names_the_editor(self):
        handler = JiraHandler.__new__(JiraHandler)
        handler.driver = MagicMock()
        handler.driver.find_elements.return_value = []
        with patch("lx_toolbox.core.jira_handler.WebDriverWait") as wait_cls:
            wait_cls.return_value.until.side_effect = TimeoutException(GECKO_EMPTY)
            with pytest.raises(RuntimeError, match="Description container") as caught:
                handler.activate_description_editor()
        assert "chrome://remote" not in str(caught.value)

    def test_activate_description_clicks_the_renderer_element(self):
        handler = JiraHandler.__new__(JiraHandler)
        handler.driver = MagicMock()
        handler.driver.find_elements.return_value = []
        renderer = MagicMock()
        with patch("lx_toolbox.core.jira_handler.WebDriverWait") as wait_cls:
            wait_cls.return_value.until.side_effect = [
                MagicMock(),
                renderer,
                TimeoutException(GECKO_EMPTY),
            ]
            with pytest.raises(RuntimeError, match="clicking renderer"):
                handler.activate_description_editor()
        renderer.click.assert_called_once()

    def test_prefill_failure_detail_strips_gecko_stack(self):
        detail = SnowAIProcessor._jira_prefill_failure_detail(
            TimeoutException(GECKO_EMPTY)
        )
        assert "chrome://remote" not in detail
        assert "TimeoutException" in detail

    def test_prefill_failure_detail_keeps_named_step(self):
        exc = RuntimeError(
            "Jira prefill failed during fill Summary: "
            "Summary field #summary-field not clickable (TimeoutException)"
        )
        assert SnowAIProcessor._jira_prefill_failure_detail(exc) == str(exc)

    def test_prefill_step_prefixes_the_step_name(self):
        proc = SnowAIProcessor.__new__(SnowAIProcessor)

        def boom():
            raise TimeoutException(GECKO_EMPTY)

        with pytest.raises(RuntimeError, match="failed during fill Summary") as caught:
            proc._jira_prefill_step("fill Summary", boom)
        assert "chrome://remote" not in str(caught.value)
