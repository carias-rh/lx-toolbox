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
    
    JIRA_DASHBOARD_URL = "https://issues.redhat.com/secure/Dashboard.jspa"
    JIRA_PROJECTS_URL = "https://issues.redhat.com/projects/PTL/issues"
    
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
    
    def _is_logged_in(self) -> bool:
        """
        Check if already logged into Jira by looking for logged-in indicators.
        
        Returns:
            True if logged in, False otherwise
        """
        try:
            # Look for user profile elements that indicate logged-in state
            # Check for avatar or user menu
            WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.XPATH, 
                    '//*[@id="header-details-user-fullname"] | '
                    '//*[contains(@class, "aui-avatar")] | '
                    '//a[contains(@href, "/secure/ViewProfile.jspa")]'
                ))
            )
            return True
        except TimeoutException:
            return False
    
    def _get_auth_token(self, auth_helper: str) -> str:
        """Execute auth helper command and return the token."""
        if not auth_helper:
            return ""
        try:
            return os.popen(auth_helper).read().strip()
        except Exception as e:
            logging.getLogger(__name__).debug(f"Auth helper returned empty: {e}")
            return ""

    def _attempt_sso_login(self, username: str = None, password: str = None) -> bool:
        """
        Attempt SSO login with provided or stored credentials.
        
        Args:
            username: Optional username (uses config if not provided)
            password: Optional password (uses config if not provided)
            
        Returns:
            True if credentials were autofilled, False if manual login required
        """
        try:
            # Get credentials from config if not provided
            if not username:
                username = self.config.get("Credentials", "RH_USERNAME")
            if not password:
                password = self.config.get("Credentials", "RH_PASSWORD")
            auth_helper = self.config.get("Credentials", "RH_AUTH_HELPER")
            
            # Try to find SSO login form elements
            try:
                # First, check for the username-verification field (Red Hat login page)
                username_verification = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="username-verification"]'))
                )
                
                # Autofill username if available
                if username:
                    username_verification.send_keys(f"{username}@redhat.com")
                    WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="login-show-step2"]'))
                    ).click()
                    time.sleep(1)
                    
                    # Now wait for SSO page and fill credentials
                    try:
                        sso_username_field = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))
                        )
                        sso_username_field.send_keys(username)
                        
                        if password:
                            # Build full credential string
                            auth_token = self._get_auth_token(auth_helper)
                            full_credential = f"{str(password)}{auth_token}"
                            WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))
                            ).send_keys(full_credential)
                            
                            WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))
                            ).click()
                            time.sleep(5)
                            return True
                        else:
                            self._prompt_for_manual_login(
                                "Username autofilled. Please enter your password and complete authentication."
                            )
                            return True
                    except TimeoutException:
                        return True
                else:
                    self._prompt_for_manual_login(
                        "Credentials not configured. Please complete the login manually."
                    )
                    return True
                    
            except TimeoutException:
                # Username-verification field not found, check for direct SSO
                try:
                    sso_username_field = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))
                    )
                    
                    if username:
                        sso_username_field.send_keys(username)
                        
                        if password:
                            auth_token = self._get_auth_token(auth_helper)
                            full_credential = f"{str(password)}{auth_token}"
                            WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))
                            ).send_keys(full_credential)
                            
                            WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))
                            ).click()
                            time.sleep(5)
                            return True
                        else:
                            self._prompt_for_manual_login(
                                "Username autofilled. Please enter your password and complete authentication."
                            )
                            return True
                    else:
                        self._prompt_for_manual_login(
                            "Credentials not configured. Please complete the login manually."
                        )
                        return True
                        
                except TimeoutException:
                    return True
                    
        except Exception as e:
            logging.getLogger(__name__).warning(f"SSO login attempt failed: {e}")
            return False
    
    def login(self, use_session: bool = True) -> bool:
        """
        Login to Jira.
        
        First tries session login (if already authenticated via SSO from another service).
        If not logged in, attempts SSO login with available credentials.
        If credentials are not available, prompts for manual authentication.
        
        Args:
            use_session: If True, first check if already logged in via session
            
        Returns:
            True if login was successful, False otherwise
        """
        self.logger("Login into Jira")
        
        try:
            # Navigate to Jira dashboard
            self.driver.get(self.JIRA_DASHBOARD_URL)
            time.sleep(3)
            
            # Check if already logged in (session login)
            if use_session and self._is_logged_in():
                # Already logged into Jira (session active)
                self._logged_in = True
                return True
            
            # Look for login link in navbar (if on Jira but not logged in)
            try:
                login_link = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, 
                        '/html/body/div[1]/header/nav/div/div[3]/ul/li[3]/a | '
                        '//a[contains(@href, "/login.jsp")] | '
                        '//a[contains(text(), "Log in")]'
                    ))
                )
                login_link.click()
                time.sleep(2)
            except TimeoutException:
                # No login link found - might be redirected to SSO or already logged in
                pass
            
            # Attempt SSO login
            if self._attempt_sso_login():
                # Wait for page to load and verify login
                time.sleep(3)
                if self._is_logged_in():
                    self.logger("Jira login successful")
                    self._logged_in = True
                    return True
                else:
                    # Check if we're still on login page
                    time.sleep(5)
                    if self._is_logged_in():
                        self.logger("Jira login successful")
                        self._logged_in = True
                        return True
            
            # If we get here, login may have succeeded (user completed manually)
            self.logger("Jira login process completed")
            self._logged_in = True
            return True
            
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

