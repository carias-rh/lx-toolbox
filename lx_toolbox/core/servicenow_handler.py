"""
ServiceNow Handler module for LX Toolbox.

Provides Selenium-based ServiceNow login and common operations.
This is separate from servicenow_autoassign.py which uses the REST API.
"""

import time
import logging
import traceback

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

        try:
            # Check if we're already on the SSO page (username field present)
            try:
                username_field = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="username"]'))
                )

                if username:
                    username_field.send_keys(username)
                    self._prompt_for_manual_login(
                        "Username autofilled. Please enter your password and complete authentication."
                    )
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
        No-op on hub.redhat.com: the classic .do form renders directly in the
        main document without an iframe.  Kept for call-site compatibility.
        """
        self.driver.switch_to.default_content()
    
    def switch_to_default_content(self):
        """Switch back to default content from iframe."""
        self.driver.switch_to.default_content()
    
    def navigate_to_ticket(self, ticket_id: str):
        """
        Navigate to a specific ticket in the classic form view.

        Goes directly to the classic .do URL using the ticket number query
        parameter, bypassing the workspace redirect entirely.
        """
        self.driver.get(f"{self.base_url}/x_redha_rht_task.do?sysparm_query=number={ticket_id}")
        time.sleep(5)
    
    def navigate_to_feedback_queue(self):
        """Navigate to the default feedback queue."""
        self.driver.get(self.feedback_queue_url)
        time.sleep(3)
    
    def get_ticket_ids_from_queue(self) -> list:
        """
        Get list of ticket IDs from the feedback queue.

        On hub.redhat.com the classic task-list page is served at a direct
        .do URL with no surrounding iframe, so we navigate there explicitly
        and query the formlink anchors directly from the main document.

        Returns:
            List of ticket ID strings, empty if none found
        """
        ticket_ids = []
        try:
            # Navigate to the classic list URL (already set as feedback_queue_url)
            self.driver.get(self.feedback_queue_url)
            time.sleep(3)

            # Check for empty queue
            page_text = self.driver.find_element(By.TAG_NAME, 'body').text
            if 'No records to display' in page_text:
                return ticket_ids

            ticket_elements = WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, 'a.linked.formlink')
                )
            )

            for ticket in ticket_elements:
                ticket_id = ticket.text.strip()
                if ticket_id:
                    ticket_ids.append(ticket_id)

        except Exception as e:
            current_url = self.driver.current_url
            page_title = self.driver.title
            logging.getLogger(__name__).error(
                f"Error retrieving ticket IDs from queue: {e}\n"
                f"  Current URL: {current_url}\n"
                f"  Page title: {page_title}\n"
                f"{traceback.format_exc()}"
            )

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
    
    
    def get_customer_updates(self, max_entries: int = 10) -> list[dict]:
        """
        Extract journal entries (customer messages and our own replies) from
        the ticket activity journal.

        hub.redhat.com wraps the classic .do form inside iframe#gsft_main,
        which itself sits inside the shadow root of a MACROPONENT element.
        All DOM queries must therefore pierce that shadow root to reach the
        iframe's contentDocument.

        Relevant entries appear as li.h-card with type label:
          - "Additional comments" — a direct reply typed in the portal
          - "Email received" — an email sent by the student to the hub address
          - "Email sent" — an email we sent to the student (contains our own
            investigation reply; kept so downstream code has visibility into
            what was already communicated, and can be filtered by author)
        System / api_snow_autoassign entries are skipped.

        For "Additional comments" the text is inline in
        .sn-widget-textblock-body_formatted. For "Email received"/"Email
        sent" the actual body is NOT in the DOM until the "Show email
        details" link is clicked, which lazily injects a nested
        <iframe class="activity-stream-email-iframe"> pointing at
        /email_display.do?email_id=... — that iframe's contentDocument holds
        the real message text. This method clicks any unexpanded links first,
        waits for the nested iframes to load, then reads their body text.
        Falls back to the visible metadata table (subject/from) if a body
        never becomes available.

        Args:
            max_entries: Maximum number of entries to return (newest first)

        Returns:
            List of dicts with 'timestamp', 'author', 'type', and 'text' keys
        """
        try:
            _log = logging.getLogger(__name__)

            FIND_GSFT_DOC_JS = """
                function findGsftDoc() {
                    // Walk every element's shadow root looking for iframe#gsft_main.
                    // This avoids hardcoding the MACROPONENT-<sys_id> tag name which
                    // can change across ServiceNow upgrades or instance migrations.
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var sr = all[i].shadowRoot;
                        if (!sr) continue;
                        var fr = sr.querySelector('iframe#gsft_main');
                        if (fr) return fr.contentDocument || fr.contentWindow.document;
                    }
                    return document;
                }
            """

            # Scroll the activity stream into view inside the iframe
            self.driver.execute_script(FIND_GSFT_DOC_JS + """
                var doc = findGsftDoc();
                var el = doc.querySelector('#sn_form_inline_stream_entries')
                         || doc.querySelector('.activities-form');
                if (el) el.scrollIntoView({block: 'center'});
            """)
            time.sleep(2)

            # Expand every collapsed "Show email details" link so the nested
            # email-body iframes get injected into the DOM. Entries that are
            # already expanded show "Hide email details" and are skipped.
            num_expanded = self.driver.execute_script(FIND_GSFT_DOC_JS + """
                var doc = findGsftDoc();
                var links = doc.querySelectorAll('a.stream-action[action-type="show-email"]');
                for (var i = 0; i < links.length; i++) {
                    links[i].click();
                }
                return links.length;
            """)
            if num_expanded:
                time.sleep(2)  # let the nested email_display.do iframes load

            updates = self.driver.execute_script(FIND_GSFT_DOC_JS + """
                var doc = findGsftDoc();

                var results = [];
                var maxEntries = arguments[0];
                var SKIP_AUTHORS = ['api_snow_autoassign', 'System'];
                var RELEVANT_LABELS = ['Additional comments', 'Email received', 'Email sent'];

                var cards = doc.querySelectorAll('li.h-card');

                for (var i = 0; i < cards.length && results.length < maxEntries; i++) {
                    var card = cards[i];

                    var timeSpan = card.querySelector('.sn-card-component-time');
                    if (!timeSpan) continue;
                    var typeLabel = timeSpan.querySelector('span:first-child');
                    if (!typeLabel) continue;
                    var label = typeLabel.textContent.trim();
                    if (RELEVANT_LABELS.indexOf(label) === -1) continue;

                    var createdBy = card.querySelector('.sn-card-component-createdby');
                    var author = createdBy ? createdBy.textContent.trim() : '';
                    if (SKIP_AUTHORS.indexOf(author) !== -1) continue;

                    var dateEl = card.querySelector('.date-calendar');
                    var timestamp = dateEl ? dateEl.textContent.trim() : '';

                    var text = '';

                    // "Additional comments": text is inline in .sn-widget-textblock-body_formatted
                    var bodySpans = card.querySelectorAll('.sn-widget-textblock-body_formatted');
                    for (var b = 0; b < bodySpans.length; b++) {
                        var bt = bodySpans[b].textContent || '';
                        if (bt.trim()) { text = bt; break; }
                    }

                    // "Email received"/"Email sent": real body lives in a nested
                    // iframe injected after clicking "Show email details" above.
                    if (!text.trim()) {
                        var emailIframe = card.querySelector('iframe.activity-stream-email-iframe');
                        if (emailIframe) {
                            try {
                                var emailDoc = emailIframe.contentDocument || emailIframe.contentWindow.document;
                                if (emailDoc && emailDoc.body) {
                                    text = emailDoc.body.innerText || '';
                                }
                            } catch (e) { /* cross-origin or not-yet-loaded, fall through */ }
                        }
                    }

                    // Last resort: visible metadata table (subject/from) when the
                    // body iframe never loaded in time.
                    if (!text.trim()) {
                        var cells = card.querySelectorAll('.sn-widget-list-table-cell');
                        var parts = [];
                        for (var c = 0; c < cells.length; c++) {
                            var ct = cells[c].textContent.trim();
                            if (ct) parts.push(ct);
                        }
                        text = parts.join(' | ');
                    }

                    text = (text || '').trim();
                    if (text) {
                        results.push({
                            timestamp: timestamp,
                            author: author,
                            type: label,
                            text: text
                        });
                    }
                }
                return results;
            """, max_entries)

            _log.info(
                f"Extracted {len(updates or [])} customer update(s) from activity journal"
                + (
                    ":\n " + "\n ".join(
                        f"[{u.get('timestamp','')}] {u.get('author','')}: {u.get('text','')[:200]}"
                        for u in updates
                    )
                    if updates else ""
                )
            )
            for u in (updates or []):
                _log.debug(
                    f"  [{u.get('timestamp','')}] ({u.get('type','')}) "
                    f"{u.get('author','')}: {u.get('text','')[:500]}"
                )
            return updates or []
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to extract customer updates: {e}")
            return []

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
