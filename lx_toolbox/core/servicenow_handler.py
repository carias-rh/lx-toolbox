"""
ServiceNow Handler module for LX Toolbox.

Provides Selenium-based ServiceNow login and common operations.
This is separate from servicenow_autoassign.py which uses the REST API.
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


class ServiceNowHandler:
    """
    Handles ServiceNow Selenium-based login and common operations.
    
    This class provides:
    - SSO login to ServiceNow via browser
    - Iframe switching for ServiceNow UI
    - Ticket queue navigation
    - Common ticket operations
    """
    
    def __init__(self, driver, wait: WebDriverWait, config: ConfigManager, logger=None):
        """
        Initialize ServiceNowSeleniumHandler.
        
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
        
        # ServiceNow URLs
        self.base_url = config.get("ServiceNow", "SNOW_BASE_URL")
        self.feedback_queue_path = config.get("ServiceNow", "SNOW_FEEDBACK_QUEUE_PATH")
        
        # Default queue URLs
        self.feedback_queue_url = (
            f"{self.base_url}{self.feedback_queue_path}"
        )
    
    def _prompt_for_manual_login(self, message: str = None):
        """
        Prompt the user to complete authentication manually.
        Waits until the user presses Enter in the CLI.
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
    
    def _get_auth_token(self, auth_helper: str) -> str:
        """Execute auth helper command and return the token."""
        if not auth_helper:
            return ""
        try:
            return os.popen(auth_helper).read().strip()
        except Exception as e:
            logging.getLogger(__name__).debug(f"Auth helper returned empty: {e}")
            return ""
    
    def _is_logged_in(self) -> bool:
        """
        Check if already logged into ServiceNow.
        
        Returns:
            True if logged in, False otherwise
        """
        try:
            # Check for ServiceNow UI elements that indicate logged-in state
            WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.XPATH, 
                    '//*[contains(@class, "navpage-header")] | '
                    '//*[@id="gsft_nav"] | '
                    '//*[contains(@class, "sn-polaris-nav")]'
                ))
            )
            return True
        except TimeoutException:
            return False
    
    def login(self, use_session: bool = True) -> bool:
        """
        Login to ServiceNow using SSO.
        
        First tries session login (if already authenticated).
        If not logged in, attempts SSO login with available credentials.
        If credentials are not available, prompts for manual authentication.
        
        Args:
            use_session: If True, first check if already logged in via session
            
        Returns:
            True if login was successful, False otherwise
        """
        self.logger("Login into ServiceNow")
        
        username = self.config.get("Credentials", "RH_USERNAME")
        password = self.config.get("Credentials", "RH_PASSWORD")
        auth_helper = self.config.get("Credentials", "RH_AUTH_HELPER")
        
        try:
            # Check if we're already on the SSO page (username field present)
            try:
                username_field = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="username"]'))
                )
                
                if username:
                    username_field.send_keys(username)
                    
                    if password:
                        # Build full credential string
                        auth_token = self._get_auth_token(auth_helper)
                        full_credential = f"{str(password)}{auth_token}"
                        self.wait.until(EC.element_to_be_clickable(
                            (By.XPATH, '//*[@id="password"]')
                        )).send_keys(full_credential)
                        self.wait.until(EC.element_to_be_clickable(
                            (By.XPATH, '//*[@id="submit"]')
                        )).click()
                        time.sleep(15)
                        self._logged_in = True
                        return True
                    else:
                        self._prompt_for_manual_login(
                            "Username autofilled. Please enter your password and complete authentication."
                        )
                        self._logged_in = True
                        return True
                else:
                    self._prompt_for_manual_login(
                        "Credentials not configured. Please complete the login manually."
                    )
                    self._logged_in = True
                    return True
                    
            except TimeoutException:
                # No SSO page - might already be logged in
                if use_session and self._is_logged_in():
                    self.logger("Already logged into ServiceNow (session active)")
                    self._logged_in = True
                    return True
                    
        except Exception as e:
            logging.getLogger(__name__).error(f"ServiceNow login failed: {e}")
            return False
        
        self._logged_in = True
        return True
    
    def switch_to_iframe(self):
        """
        Switch to ServiceNow content iframe through macroponent shadow DOM.
        
        ServiceNow uses a complex iframe structure with shadow DOM.
        This method navigates through it to access the actual content.
        """
        try:
            self.driver.switch_to.default_content()
            macroponent = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.XPATH, '//*[starts-with(local-name(), "macro")]'))
            )
            shadow_root = self.driver.execute_script('return arguments[0].shadowRoot', macroponent)
            iframe = shadow_root.find_element(By.CSS_SELECTOR, 'iframe#gsft_main')
            WebDriverWait(self.driver, 3).until(EC.frame_to_be_available_and_switch_to_it(iframe))
        except Exception as e:
            raise RuntimeError(f"Unable to switch to ServiceNow iframe: {e}")
    
    def switch_to_default_content(self):
        """Switch back to default content from iframe."""
        self.driver.switch_to.default_content()
    
    def navigate_to_ticket(self, ticket_id: str):
        """
        Navigate to a specific ticket by ID.
        
        Args:
            ticket_id: The ServiceNow ticket number (e.g., RITM0123456)
        """
        self.driver.get(f"{self.base_url}/surl.do?n={ticket_id}")
        time.sleep(5)
        self.switch_to_iframe()
    
    def navigate_to_feedback_queue(self):
        """Navigate to the default feedback queue."""
        self.driver.get(self.feedback_queue_url)
        time.sleep(3)
    
    def get_ticket_ids_from_queue(self) -> list:
        """
        Get list of ticket IDs from the current queue view.
        
        Returns:
            List of ticket ID strings, empty if none found
        """
        ticket_ids = []
        try:
            self.switch_to_iframe()
            tickets_in_line = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training"]/div[1]')
                )
            ).text
            
            if tickets_in_line.strip() == 'No records to display':
                return ticket_ids
            
            table_body = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//tbody[@class="list2_body -sticky-group-headers"]')
                )
            )
            ticket_elements = table_body.find_elements(
                By.XPATH, './/a[@class="linked formlink"]'
            )
            
            for ticket in ticket_elements:
                ticket_id = ticket.text.strip()
                if ticket_id:
                    ticket_ids.append(ticket_id)
                    
        except Exception as e:
            logging.getLogger(__name__).error(f"Error retrieving ticket IDs: {e}")
        
        return ticket_ids
    
    def get_field_value(self, field_id: str) -> str:
        """
        Get the value of a form field by ID.
        
        Args:
            field_id: The HTML ID of the form field
            
        Returns:
            The field value or empty string if not found
        """
        try:
            element = self.driver.find_element(By.XPATH, f'//*[@id="{field_id}"]')
            return element.get_attribute('value') or element.text or ""
        except Exception:
            return ""
    
    def set_field_value(self, field_id: str, value: str):
        """
        Set the value of a form field by ID.
        
        Args:
            field_id: The HTML ID of the form field
            value: The value to set
        """
        try:
            element = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, f'//*[@id="{field_id}"]'))
            )
            element.clear()
            element.send_keys(value)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not set field {field_id}: {e}")
    
    def add_work_note(self, note: str):
        """
        Add a work note to the current ticket.
        
        Args:
            note: The work note text to add
        """
        try:
            work_notes_field = self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]')
                )
            )
            work_notes_field.send_keys(note)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not add work note: {e}")
    
    def add_customer_comment(self, comment: str):
        """
        Add a customer-visible comment to the current ticket.
        
        Args:
            comment: The comment text to add
        """
        try:
            comments_field = self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.comments"]')
                )
            )
            comments_field.send_keys(comment)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not add comment: {e}")
    
    def ensure_logged_in(self) -> bool:
        """
        Ensure we are logged into ServiceNow, logging in if necessary.
        
        Returns:
            True if logged in, False otherwise
        """
        if self._logged_in:
            return True
        return self.login()
    
    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged into ServiceNow."""
        return self._logged_in
