"""Background tabs opened in prelogin_all must be assigned by identity, not by
window_handles index.  Selenium does not guarantee that window_handles order
matches the order in which window.open() calls were made, so taking
handles[0] / handles[1] can silently swap ROL and Jira.

Seam: SnowAIProcessor._open_background_tab
"""

from unittest.mock import patch

from lx_toolbox.core.snow_ai_processor import SnowAIProcessor


def test_open_background_tab_returns_the_newly_opened_handle():
    """Each tab opened gets the handle that appeared after *that* open call."""
    proc = SnowAIProcessor.__new__(SnowAIProcessor)
    handles = ["snow"]

    class FakeDriver:
        window_handles = handles

        def execute_script(self, _script, url):
            if "jira" in url:
                handles.append("jira-tab")
            else:
                handles.append("rol-tab")

    proc.driver = FakeDriver()

    with patch("lx_toolbox.core.snow_ai_processor.WebDriverWait") as wait_cls:
        wait_cls.return_value.until.side_effect = lambda fn: fn(proc.driver)
        jira = proc._open_background_tab("https://issues.redhat.com/jira")
        rol = proc._open_background_tab("https://rol.redhat.com/rol/app/courses/rh124-10.0/pages/pr01")

    assert jira == "jira-tab"
    assert rol == "rol-tab"
    # Regression: old code used handles[1] for ROL after both opens — that is Jira.
    assert handles[1] == "jira-tab", "handle order confirms the old index-based assignment was wrong"
