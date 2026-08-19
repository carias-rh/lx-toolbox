"""
Jira Handler module for LX Toolbox.

Provides unified Jira login and authentication handling.
Supports session-based login (if already authenticated) and SSO flow.
Prompts for manual authentication when credentials are not available.
"""

import time
import logging

from selenium.webdriver.common.by import By
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
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.XPATH,
                    '//button[contains(@aria-label, "@redhat.com")] | '
                    '//button[text()="Create"] | '
                    '//input[@placeholder="Search"]'
                ))
            )
            return True
        except TimeoutException:
            return False
    
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

                    # Check for "Create" button — indicates user is already logged in
                    try:
                        create_btn = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, '//button/span[text()="Create"]'))
                        )
                        if create_btn:
                            logging.getLogger(__name__).debug("Detected 'Create' button; Jira appears logged in. Skipping SSO.")
                            return True
                    except Exception as e:
                        logging.getLogger(__name__).debug(f"Error checking for Jira 'Create' button: {e}")

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

