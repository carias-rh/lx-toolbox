"""
Jira Handler module for LX Toolbox.

Provides unified Jira login and authentication handling.
Supports session-based login (if already authenticated) and SSO flow.
Prompts for manual authentication when credentials are not available.
"""

import os
import time
import logging

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from ..utils.config_manager import ConfigManager
from ..utils.helpers import step_logger


class JiraHandler:
    """
    Handles Jira login and authentication.
    
    This class provides a unified way to handle Jira login across different
    modules (link_checker, snow_ai_processor, etc.).
    
    Authentication flow:
    1. Check if already logged in (session login)
    2. If not, attempt SSO login with available credentials
    3. If credentials not available, prompt user for manual authentication
    """
    
    JIRA_BASE_URL = "https://redhat.atlassian.net"
    JIRA_DASHBOARD_URL = "https://redhat.atlassian.net/jira/your-work"
    JIRA_PROJECTS_URL = "https://redhat.atlassian.net/jira/software/c/projects/PTL/issues"
    # Modernised create dialog (2026+): nav Create is a testid, Summary is a
    # textarea, Description starts as a renderer overlay, and other fields are
    # collapsed until their container is clicked.
    CREATE_BUTTON_CSS = '[data-testid="atlassian-navigation--create-button"]'
    COOKIE_ACCEPT_CSS = '[data-testid="accept-all-button"]'
    SUMMARY_FIELD_ID = "summary-field"
    DESCRIPTION_CONTAINER_ID = "description-container"
    DESCRIPTION_EDITOR_CSS = 'div[contenteditable="true"][aria-label^="Description area"]'
    # Painted Bug-template ADF. Click one of these paragraphs with
    # WebElement.click(). ActionChains misses under body zoom 0.8, and a JS
    # click on the overlay button does not enable editing.
    DESCRIPTION_RENDERER_CSS = f'#{DESCRIPTION_CONTAINER_ID} p[data-renderer-start-pos]'
    TYPE_BUG_CSS = 'button[aria-label="Type: Bug"]'
    TYPE_BUTTON_CSS = 'button[aria-label^="Type:"]'
    _PROFILE_TRIGGER_CSS = '[data-testid="atlassian-navigation--secondary-actions--profile--trigger"]'
    # Notifications trigger is equally reliable and rendered earlier than the profile button.
    _NOTIFICATIONS_TRIGGER_CSS = '[data-testid="atlassian-navigation--secondary-actions--notifications--menu-trigger"]'
    _LOGIN_LINK_XPATH = (
        '//a[contains(@href, "id.atlassian.com/login")] | '
        '//a[normalize-space()="Log in"] | '
        '//button[normalize-space()="Log in"]'
    )
    
    def __init__(self, driver, wait: WebDriverWait, config: ConfigManager, logger=None):
        """
        Initialize JiraHandler.
        
        Args:
            driver: Selenium WebDriver instance
            wait: WebDriverWait instance
            config: ConfigManager instance for retrieving credentials
            logger: Optional logger function (defaults to step_logger)
        """
        self.driver = driver
        self.wait = wait
        self.config = config
        self.logger = logger or step_logger
        self._logged_in = False
    
    def _prompt_for_manual_login(self, message: str = None):
        """
        Prompt the user to complete authentication manually.
        Waits until the user presses Enter in the CLI.
        
        Args:
            message: Optional custom message to display
        """
        if message:
            print(f"\n{'='*60}")
            print(message)
            print(f"{'='*60}")
        else:
            print(f"\n{'='*60}")
            print("Manual authentication required.")
            print("Please complete the login in the browser.")
            print(f"{'='*60}")
        
        input("Press Enter once you have completed the login...")
    
    def _is_logged_in(self, timeout: int = 2) -> bool:
        """
        Check if already logged into Jira by looking for logged-in indicators.
        
        Args:
            timeout: How many seconds to wait for logged-in indicators
            
        Returns:
            True if logged in, False otherwise
        """
        if self._on_login_page():
            return False
        if self._has_login_link():
            return False
        if self._has_profile_trigger():
            return True
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.XPATH,
                    '//button[text()="Create"] | '
                    '//input[@placeholder="Search"]'
                ))
            )
            if self._on_login_page() or self._has_login_link():
                return False
            return True
        except TimeoutException:
            return False

    def _has_login_link(self) -> bool:
        """True when Jira is showing a visible login affordance."""
        return bool(self.driver.find_elements(By.XPATH, self._LOGIN_LINK_XPATH))

    def _has_profile_trigger(self) -> bool:
        """True when any authenticated Atlassian nav element is present."""
        return (
            bool(self.driver.find_elements(By.CSS_SELECTOR, self._PROFILE_TRIGGER_CSS))
            or bool(self.driver.find_elements(By.CSS_SELECTOR, self._NOTIFICATIONS_TRIGGER_CSS))
        )
    
    def _on_login_page(self) -> bool:
        """Check if the current URL indicates we're on an authentication page."""
        current_url = self.driver.current_url or ""
        return any(domain in current_url for domain in (
            "id.atlassian.com", "auth.redhat.com", "sso.redhat.com",
            "login.microsoftonline.com",
        ))

    def _attempt_sso_login(self, username: str = None) -> bool:
        """
        Attempt SSO login by autofilling the username, then prompting the user
        to complete password entry manually.

        Atlassian Cloud auth flow:
        1. Redirects to id.atlassian.com → enter email → Continue
        2. May redirect to Red Hat SSO (Keycloak) for SAML auth
        3. Keycloak uses #username / #password / #submit fields

        Args:
            username: Optional username override (uses RH_USERNAME from config if not provided)

        Returns:
            True if the login page was handled, False if no login page found
        """
        try:
            if not username:
                username = self.config.get("Credentials", "RH_USERNAME")

            if not self._on_login_page():
                logging.getLogger(__name__).debug(
                    f"Not on a login page (url={self.driver.current_url}), skipping SSO"
                )
                return False

            # Check for Atlassian Cloud login page (id.atlassian.com)
            try:
                atlassian_email_field = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//input[@name="username"]'
                    ))
                )
                if username:
                    atlassian_email_field.clear()
                    atlassian_email_field.send_keys(f"{username}@redhat.com")
                    try:
                        WebDriverWait(self.driver, 3).until(
                            EC.element_to_be_clickable((By.XPATH,
                                '//*[@id="login-submit"] | '
                                '//button[@type="submit"] | '
                                '//span[text()="Continue"]/parent::button'
                            ))
                        ).click()
                        time.sleep(3)
                    except TimeoutException:
                        pass

                    # After clicking Continue, Atlassian either resumes the cached
                    # session (redirects back to Jira) or forwards to Red Hat SSO.
                    # Poll for up to 15 s so slow redirects don't trigger the prompt.
                    for _tick in range(15):
                        time.sleep(1)
                        if self._is_logged_in(timeout=1):
                            logging.getLogger(__name__).debug(
                                "Detected authenticated Jira chrome after Atlassian redirect."
                            )
                            return True
                        if any(d in (self.driver.current_url or "")
                               for d in ("sso.redhat.com", "auth.redhat.com")):
                            break  # arrived at Red Hat SSO — handle below

                    # After Atlassian redirects to Red Hat SSO (Keycloak), autofill username
                    try:
                        sso_username_field = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))
                        )
                        sso_username_field.send_keys(username)
                        self._prompt_for_manual_login(
                            "Username autofilled. Please enter your password and complete authentication."
                        )
                        return True
                    except TimeoutException:
                        # One last chance: maybe the page settled as logged-in.
                        if self._is_logged_in(timeout=3):
                            return True
                        self._prompt_for_manual_login(
                            "Could not detect Red Hat SSO page. Please complete login manually."
                        )
                        return True
                else:
                    self._prompt_for_manual_login(
                        "Credentials not configured. Please complete the login manually."
                    )
                    return True
            except TimeoutException:
                self._prompt_for_manual_login(
                    "Could not find login form. Please complete the login manually."
                )
                return True

        except Exception as e:
            logging.getLogger(__name__).warning(f"SSO login attempt failed: {e}")
            return False
    
    def login(self, use_session: bool = True) -> bool:
        """
        Login to Jira (Atlassian Cloud at redhat.atlassian.net).
        
        Atlassian Cloud redirects to id.atlassian.com for auth, which may
        then redirect to Red Hat SSO (Keycloak) for SAML-based authentication.
        
        Args:
            use_session: If True, first check if already logged in via session
            
        Returns:
            True if login was successful, False otherwise
        """
        self.logger("Login into Jira")
        
        try:
            self.driver.get(self.JIRA_DASHBOARD_URL)

            # Wait for the page to settle (Atlassian Cloud may redirect
            # multiple times: main page → id.atlassian.com → SSO → back).
            # Poll until the URL stops changing or 15 seconds pass.
            prev_url = ""
            for _ in range(6):
                time.sleep(2)
                cur_url = self.driver.current_url or ""
                if cur_url == prev_url:
                    break
                prev_url = cur_url

            if use_session and self._is_logged_in(timeout=2):
                self.logger("Jira login successful")
                self._logged_in = True
                return True

            # We're not logged in -- attempt SSO if we landed on a login page.
            if self._attempt_sso_login():
                # SSO credentials were submitted; wait for redirect back to Jira.
                if self._is_logged_in(timeout=2):
                    self.logger("Jira login successful")
                    self._logged_in = True
                    return True
            
            # Final check: the user may have completed login manually.
            if self._is_logged_in(timeout=2):
                self.logger("Jira login successful")
                self._logged_in = True
                return True

            self.logger("Jira login could not be verified")
            return False
            
        except Exception as e:
            logging.getLogger(__name__).error(f"Jira login failed: {e}")
            import traceback
            logging.getLogger(__name__).debug(traceback.format_exc())
            return False
    
    def ensure_logged_in(self) -> bool:
        """
        Ensure we are logged into Jira, logging in if necessary.
        
        Returns:
            True if logged in, False otherwise
        """
        if self._logged_in:
            return True
        return self.login()
    
    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged into Jira."""
        return self._logged_in

    @staticmethod
    def format_webdriver_error(exc: BaseException) -> str:
        """Exception type plus message, without the geckodriver stacktrace.

        Firefox often raises ``TimeoutException`` / ``NoSuchElementError`` as
        ``Message:`` plus a chrome://remote stack, which hides the failing step.
        """
        name = type(exc).__name__
        raw = (getattr(exc, "msg", None) or str(exc) or "").strip()
        if "Stacktrace:" in raw:
            raw = raw.split("Stacktrace:", 1)[0].strip()
        if raw.lower().startswith("message:"):
            raw = raw[8:].strip()
        return f"{name}: {raw}" if raw else name

    def _wait_for(self, timeout, condition, what: str):
        try:
            return WebDriverWait(self.driver, timeout).until(condition)
        except TimeoutException as e:
            raise RuntimeError(f"{what} ({self.format_webdriver_error(e)})") from e

    @staticmethod
    def create_dialog_state(driver) -> str | None:
        """Return whether the create dialog is ready or blocked on authentication."""
        if driver.find_elements(By.ID, JiraHandler.SUMMARY_FIELD_ID):
            return "ready"
        for dialog in driver.find_elements(By.XPATH, '//*[@role="dialog"]'):
            text = (getattr(dialog, "text", "") or "").lower()
            if "not authorized" in text or "please log in" in text:
                return "unauthorized"
        return None

    @staticmethod
    def dismiss_atlassian_cookies(driver) -> bool:
        """Accept the Atlassian cookie dialog when it covers the page.

        That dialog is what the create-dialog wait reports when Create never
        opens. A pointer click lands on the banner instead of the nav button.
        """
        buttons = list(driver.find_elements(By.CSS_SELECTOR, JiraHandler.COOKIE_ACCEPT_CSS))
        if not buttons:
            buttons = driver.find_elements(By.XPATH, '//button[contains(., "Accept all")]')
        clicked = False
        for button in buttons:
            try:
                if not button.is_displayed():
                    continue
                driver.execute_script("arguments[0].click();", button)
                clicked = True
            except Exception:
                continue
        return clicked

    @staticmethod
    def _visible_dialog_summary(driver) -> str:
        parts = []
        for dialog in driver.find_elements(By.XPATH, '//*[@role="dialog"]'):
            text = " ".join(((getattr(dialog, "text", "") or "").split()))
            if text:
                parts.append(text[:160])
        return ", ".join(parts) if parts else "no dialogs visible"

    def click_create_button(self, timeout: int = 15) -> None:
        """Click the top-nav Create button, not the dialog footer submit."""
        self.dismiss_atlassian_cookies(self.driver)
        create_btn = self._wait_for(
            timeout,
            EC.element_to_be_clickable((By.CSS_SELECTOR, self.CREATE_BUTTON_CSS)),
            f"nav Create button {self.CREATE_BUTTON_CSS} not clickable",
        )
        try:
            create_btn.click()
        except Exception:
            # The cookie dialog or page zoom intercepts the pointer hit.
            self.driver.execute_script("arguments[0].click();", create_btn)

    def open_create_until_state(self, timeout: int = 25, attempts: int = 4) -> str:
        """Click nav Create until the dialog is ready or asks for login.

        The first click often does nothing, and a second click aborts a dialog
        that is still loading, so each attempt waits before clicking again.
        A cookie banner is dismissed and Create is clicked again immediately.
        """
        log = logging.getLogger(__name__)
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            self.dismiss_atlassian_cookies(self.driver)
            try:
                self.click_create_button()
            except Exception as e:
                last_error = e
                log.info(
                    "Jira Create click %s/%s failed: %s",
                    attempt,
                    attempts,
                    self.format_webdriver_error(e),
                )
                time.sleep(1)
                continue
            try:
                state = WebDriverWait(self.driver, timeout).until(self._create_dialog_wait_state)
            except TimeoutException as e:
                last_error = e
                log.info(
                    "Jira create dialog did not open after click %s/%s "
                    "(url=%s, state=%s)",
                    attempt,
                    attempts,
                    self.driver.current_url,
                    self._visible_dialog_summary(self.driver),
                )
                continue
            if state == "cookies":
                log.info("Dismissed the Atlassian cookie banner; clicking Create again")
                time.sleep(1)
                continue
            return str(state)
        detail = (
            self.format_webdriver_error(last_error) if last_error else "dialog not ready"
        )
        raise RuntimeError(
            "Jira create dialog did not open after "
            f"{attempts} attempts (url={self.driver.current_url}, "
            f"state={self._visible_dialog_summary(self.driver)}; {detail})"
        )

    @staticmethod
    def _create_dialog_wait_state(driver):
        state = JiraHandler.create_dialog_state(driver)
        if state:
            return state
        if JiraHandler.dismiss_atlassian_cookies(driver):
            return "cookies"
        return None

    def open_create_dialog(self, timeout: int = 25) -> None:
        """Open the Jira Cloud create-issue dialog. Raises RuntimeError on failure."""
        state = self.open_create_until_state(timeout=timeout, attempts=4)
        if state != "ready":
            raise RuntimeError(f"Jira create dialog is {state}")

    def set_work_type_bug(self) -> None:
        """Set the create-dialog work type to Bug when it is not already Bug."""
        if self.driver.find_elements(By.CSS_SELECTOR, self.TYPE_BUG_CSS):
            return
        if self.driver.find_elements(
            By.XPATH,
            '//div[@data-testid="issue-field-select-base.ui.format-option-label.c-label"]'
            '[contains(., "Bug")]',
        ):
            return
        try:
            type_btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, self.TYPE_BUTTON_CSS))
            )
            self.driver.execute_script("arguments[0].click();", type_btn)
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@role="option"][contains(., "Bug")]')
                )
            ).click()
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, self.SUMMARY_FIELD_ID))
            )
            return
        except Exception:
            pass
        try:
            work_type_input = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//input[starts-with(@id, "type-picker-")]')
                )
            )
            self.driver.execute_script(
                "arguments[0].focus(); arguments[0].click();", work_type_input
            )
            time.sleep(0.5)
            work_type_input.send_keys("Bug")
            time.sleep(0.5)
            work_type_input.send_keys(Keys.RETURN)
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, self.SUMMARY_FIELD_ID))
            )
        except Exception as e:
            logging.getLogger(__name__).debug(f"Could not set work type to Bug: {e}")

    def fill_summary(self, summary: str) -> None:
        field = self._wait_for(
            10,
            EC.element_to_be_clickable((By.ID, self.SUMMARY_FIELD_ID)),
            f"Summary field #{self.SUMMARY_FIELD_ID} not clickable",
        )
        field.click()
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(Keys.DELETE)
        field.send_keys(summary)

    @staticmethod
    def _visible_description_renderer(driver):
        """First painted Description paragraph. The wrapper matches first in DOM and is the wrong click target."""
        for el in driver.find_elements(By.CSS_SELECTOR, JiraHandler.DESCRIPTION_RENDERER_CSS):
            try:
                if el.is_displayed() and (el.size or {}).get("height", 0) > 0:
                    return el
            except Exception:
                continue
        return None

    def activate_description_editor(self, timeout: int = 10):
        """Click a Description paragraph so ProseMirror mounts.

        The Bug template is read-only ADF until a real click hits the painted
        text. ``ActionChains`` misses that text when the page is zoomed
        (snow-ai sets ``body.style.zoom = 0.8``) and the click does not raise,
        so the editor never appears. ``WebElement.click()`` hits the element.
        """
        existing = self.driver.find_elements(By.CSS_SELECTOR, self.DESCRIPTION_EDITOR_CSS)
        if existing:
            return existing[0]
        self._wait_for(
            timeout,
            EC.presence_of_element_located((By.ID, self.DESCRIPTION_CONTAINER_ID)),
            f"Description container #{self.DESCRIPTION_CONTAINER_ID} not found",
        )
        target = self._wait_for(
            timeout,
            lambda d: self._visible_description_renderer(d),
            "Description renderer did not load",
        )
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", target
        )
        try:
            target.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", target)
        return self._wait_for(
            timeout,
            EC.presence_of_element_located((By.CSS_SELECTOR, self.DESCRIPTION_EDITOR_CSS)),
            "Description editor did not mount after clicking renderer",
        )

    def _select_all_in_editor(self, editor) -> None:
        self.driver.execute_script(
            "var el = arguments[0]; el.focus();"
            "var range = document.createRange(); range.selectNodeContents(el);"
            "var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);",
            editor,
        )

    def fill_description_text(self, text: str) -> None:
        """Replace the description with plain text in the ProseMirror editor."""
        editor = self.activate_description_editor()
        self._select_all_in_editor(editor)
        time.sleep(0.2)
        self.driver.execute_script(
            "document.execCommand('insertText', false, arguments[0]);",
            text,
        )

    def fill_description_html(self, html: str) -> None:
        """Replace the description with HTML (tables, links) in ProseMirror."""
        editor = self.activate_description_editor()
        self._select_all_in_editor(editor)
        time.sleep(0.2)
        self.driver.execute_script(
            "document.execCommand('insertHTML', false, arguments[0]);",
            html,
        )

    def expand_field(self, container_id: str, field_id: str, timeout: int = 10):
        """Click a collapsed create-dialog field so its input mounts."""
        found = self.driver.find_elements(By.ID, field_id)
        if found:
            return found[0]
        container = self._wait_for(
            timeout,
            EC.presence_of_element_located((By.ID, container_id)),
            f"field container #{container_id} not found",
        )
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", container
        )
        btn = container.find_elements(By.CSS_SELECTOR, '[role="button"]')
        if btn and btn[0].get_attribute("aria-expanded") != "true":
            self.driver.execute_script("arguments[0].click();", btn[0])
        elif btn and not self.driver.find_elements(By.ID, field_id):
            self.driver.execute_script("arguments[0].click();", btn[0])
        return self._wait_for(
            timeout,
            EC.presence_of_element_located((By.ID, field_id)),
            f"field #{field_id} did not appear after expanding #{container_id}",
        )

    def _fill_combobox(self, container_id: str, field_id: str, value: str) -> None:
        field = self.expand_field(container_id, field_id)
        self.driver.execute_script("arguments[0].focus(); arguments[0].click();", field)
        time.sleep(0.3)
        field.send_keys(value)
        time.sleep(0.8)
        try:
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, f'//*[@role="option"][contains(., "{value}")]')
                )
            ).click()
        except Exception:
            field.send_keys(Keys.RETURN)
        try:
            field.send_keys(Keys.ESCAPE)
        except Exception:
            pass

    def set_component(self, component: str) -> None:
        if not component:
            return
        try:
            self._fill_combobox("components-container", "components-field", component)
        except Exception as e:
            logging.getLogger(__name__).debug(f"Failed to set component: {e}")

    def set_affects_version(self, version_text: str) -> None:
        if not version_text:
            return
        try:
            self._fill_combobox("versions-container", "versions-field", version_text)
        except Exception as e:
            logging.getLogger(__name__).debug(f"Failed to set Affects versions: {e}")

    def set_priority(self, priority: str = "Minor") -> None:
        try:
            self._fill_combobox("priority-container", "priority-field", priority)
        except Exception as e:
            logging.getLogger(__name__).debug(f"Failed to set priority: {e}")

    def set_chapter(self, chapter: str) -> None:
        if chapter is None or str(chapter).strip() == "":
            return
        try:
            field = self.expand_field("customfield_10709-container", "customfield_10709-field")
            field.click()
            field.send_keys(Keys.CONTROL, "a")
            field.send_keys(Keys.DELETE)
            field.send_keys(str(chapter))
        except Exception as e:
            logging.getLogger(__name__).debug(f"Failed to set Chapter: {e}")

    def attach_files(self, file_paths: list[str]) -> list[str]:
        """Attach local files in the create dialog. Returns basenames that uploaded."""
        attached = []
        for file_path in file_paths:
            try:
                file_inputs = self.driver.find_elements(
                    By.CSS_SELECTOR, 'input[type="file"]'
                )
                if not file_inputs:
                    logging.debug(f"No file input found for {file_path}")
                    continue
                file_input = file_inputs[0]
                self.driver.execute_script("arguments[0].value = '';", file_input)
                time.sleep(0.5)
                file_input.send_keys(file_path)
                attached.append(os.path.basename(file_path))
                time.sleep(5)
                try:
                    WebDriverWait(self.driver, 30).until_not(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, ".upload-progress, .uploading")
                        )
                    )
                except Exception:
                    pass
                time.sleep(2)
            except Exception as e:
                logging.debug(f"Failed to attach file {file_path}: {e}")
        return attached

