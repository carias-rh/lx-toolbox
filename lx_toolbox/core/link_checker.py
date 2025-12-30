"""
Link Checker module for Red Hat Learning (ROL) courses.

This module provides functionality to:
1. Navigate through the ROL catalog and courses
2. Extract links from course content, especially the References sections
3. Validate those links and generate reports
4. Take screenshots of visited pages for human review
5. Generate PDF reports with embedded screenshots and hyperlinks
"""

import os
import re
import time
import logging
import requests
import subprocess
import shutil
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# PDF generation imports
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
        PageBreak, KeepTogether, ListFlowable, ListItem
    )
    from reportlab.platypus.tableofcontents import TableOfContents
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from PIL import Image as PILImage
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

from .lab_manager import LabManager
from .jira_handler import JiraHandler
from ..utils.config_manager import ConfigManager


@dataclass
class LinkCheckResult:
    """Result of checking a single link."""
    url: str
    source_page: str
    source_section: str
    link_text: str
    chapter: str = ""
    section_number: str = ""
    status_code: Optional[int] = None
    is_valid: bool = True
    error_message: Optional[str] = None
    response_time_ms: Optional[float] = None
    screenshot_path: Optional[str] = None


@dataclass
class SectionInfo:
    """Information about a course section."""
    title: str
    url: str
    chapter: str = ""
    section_number: str = ""
    screenshot_path: Optional[str] = None
    links: list[dict] = field(default_factory=list)


@dataclass
class CourseCheckReport:
    """Report for all link checks in a course."""
    course_id: str
    course_title: str = ""
    total_links: int = 0
    valid_links: int = 0
    broken_links: int = 0
    ignored_links: int = 0
    check_started: Optional[datetime] = None
    check_completed: Optional[datetime] = None
    results: list[LinkCheckResult] = field(default_factory=list)
    sections: list[SectionInfo] = field(default_factory=list)
    screenshots_dir: str = ""
    environment: str = "rol"  # Lab environment used (rol, rol_stage, china)
    pdf_file: str = ""  # Path to generated PDF report for this course
    json_file: str = ""  # Path to generated JSON report for this course


class LinkChecker(LabManager):
    """
    Link checker for ROL courses.
    
    Extends LabManager to add functionality for:
    - Navigating through course catalog
    - Extracting links from course content
    - Validating links and generating reports
    - Taking screenshots for human review
    """
    
    # URLs to ignore (patterns)
    IGNORED_URL_PATTERNS = [
        'example.com',
        'localhost',
        '127.0.0.1',
        'example.org',
    ]
    
    # Request timeout for link validation
    REQUEST_TIMEOUT = 10
    MAX_LINK_WORKERS = 10  # Max threads for parallel link checking
    
    def __init__(self, config: ConfigManager, browser_name: str = None, is_headless: bool = None,
                 screenshots_dir: str = None):
        super().__init__(config, browser_name, is_headless)
        self.reports: list[CourseCheckReport] = []
        self.session = requests.Session()
        # Set a reasonable user agent for HTTP requests
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0'
        })
        
        # Initialize JiraHandler for Jira login
        self.jira_handler = JiraHandler(
            driver=self.driver,
            wait=self.wait,
            config=config,
            logger=self.logger
        )
        
        # Setup reports directory with timestamp for this run
        # Structure: link_check_reports/timestamp/screenshots/
        self.reports_base_dir = Path.cwd() / "link_check_reports"
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_reports_dir = self.reports_base_dir / self.run_timestamp
        self.run_reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Screenshots go inside the run directory
        if screenshots_dir:
            self.screenshots_base_dir = Path(screenshots_dir)
            self.run_screenshots_dir = self.screenshots_base_dir / self.run_timestamp
        else:
            self.screenshots_base_dir = self.run_reports_dir / "screenshots"
            self.run_screenshots_dir = self.screenshots_base_dir
        self.run_screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_screenshots_dir: Optional[Path] = None
    
    def _parse_course_id(self, course_id: str) -> tuple[str, str]:
        """
        Parse a course ID into course name and version.
        Examples:
            'do280-4.18' -> ('do280', '4.18')
            'rh124-9.3' -> ('rh124', '9.3')
            'ad141-9.0' -> ('ad141', '9.0')
        """
        # Split on the last hyphen followed by a digit (version pattern)
        match = re.match(r'^([a-zA-Z]+\d*)[-_](\d+\.\d+.*)$', course_id)
        if match:
            return match.group(1), match.group(2)
        
        # Fallback: try splitting on hyphen
        if '-' in course_id:
            parts = course_id.rsplit('-', 1)
            return parts[0], parts[1]
        
        # No version found, use course_id as name and "unknown" as version
        return course_id, "unknown"
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for use as a filename."""
        # Replace problematic characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
        sanitized = re.sub(r'\s+', '_', sanitized)
        return sanitized[:100]  # Limit length
    
    def _take_screenshot(self, name: str, subdir: str = None) -> Optional[str]:
        """Take a screenshot and save it to the screenshots directory."""
        try:
            if subdir:
                screenshot_dir = self.current_screenshots_dir / subdir
            else:
                screenshot_dir = self.current_screenshots_dir
            
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{timestamp}_{self._sanitize_filename(name)}.png"
            filepath = screenshot_dir / filename
            
            self.driver.save_screenshot(str(filepath))
            self.logger(f"    📸 Screenshot saved: {filepath.name}")
            
            return str(filepath)
        except Exception as e:
            self.logger(f"    ⚠ Failed to take screenshot: {e}")
            return None
    
    def _parse_section_title(self, title: str) -> tuple[str, str, str]:
        """
        Parse a section title to extract chapter and section info.
        Returns (chapter, section_number, clean_title)
        
        Examples:
        - "Section 1.3: The Intertwined Pillars" -> ("Chapter 1", "1.3", "The Intertwined Pillars")
        - "Chapter 1: Introduction" -> ("Chapter 1", "", "Introduction")
        - "Preface A: Introduction" -> ("Preface A", "", "Introduction")
        """
        chapter = ""
        section_number = ""
        clean_title = title
        
        # Match patterns like "Section 1.3:", "Chapter 1:", "Preface A:"
        section_match = re.match(r'^Section\s+(\d+\.\d+):\s*(.*)$', title, re.IGNORECASE)
        chapter_match = re.match(r'^(Chapter\s+\d+|Preface\s+\w+):\s*(.*)$', title, re.IGNORECASE)
        
        if section_match:
            section_number = section_match.group(1)
            clean_title = section_match.group(2)
            chapter_num = section_number.split('.')[0]
            chapter = f"Chapter {chapter_num}"
        elif chapter_match:
            chapter = chapter_match.group(1)
            clean_title = chapter_match.group(2)
        
        return chapter, section_number, clean_title
    
    def _should_ignore_url(self, url: str) -> bool:
        """Check if a URL should be ignored based on patterns."""
        if not url:
            return True
        url_lower = url.lower()
        # Ignore internal ROL links
        if 'rol.redhat.com' in url_lower:
            return True
        # Ignore anchor-only links
        if url.startswith('#'):
            return True
        # Ignore javascript links
        if url.startswith('javascript:'):
            return True
        # Ignore mailto links
        if url.startswith('mailto:'):
            return True
        # Check against ignore patterns
        for pattern in self.IGNORED_URL_PATTERNS:
            if pattern in url_lower:
                return True
        return False
    
    def _get_http_status_description(self, status_code: Optional[int]) -> str:
        """Get a human-readable description for an HTTP status code."""
        if status_code is None:
            return "No Response"
        
        descriptions = {
            200: "OK",
            201: "Created",
            204: "No Content",
            301: "Moved Permanently",
            302: "Found (Redirect)",
            304: "Not Modified",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            408: "Request Timeout",
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
            504: "Gateway Timeout",
        }
        
        return descriptions.get(status_code, f"HTTP {status_code}")
    
    def _validate_link(self, url: str, source_page: str, source_section: str, 
                       link_text: str, chapter: str = "", section_number: str = "",
                       take_screenshot: bool = False) -> LinkCheckResult:
        """
        Validate a single link by making an HTTP HEAD request.
        Optionally navigate to the link and take a screenshot for human review.
        """
        result = LinkCheckResult(
            url=url,
            source_page=source_page,
            source_section=source_section,
            link_text=link_text,
            chapter=chapter,
            section_number=section_number
        )
        
        try:
            start_time = time.time()
            # Use HEAD request first for efficiency
            response = self.session.head(url, timeout=self.REQUEST_TIMEOUT, allow_redirects=True)
            
            # Some servers don't support HEAD, fall back to GET
            if response.status_code == 405:
                response = self.session.get(url, timeout=self.REQUEST_TIMEOUT, allow_redirects=True, stream=True)
            
            result.response_time_ms = (time.time() - start_time) * 1000
            result.status_code = response.status_code
            result.is_valid = response.status_code < 400
            
            if not result.is_valid:
                result.error_message = self._get_http_status_description(response.status_code)
                
        except requests.exceptions.Timeout:
            result.is_valid = False
            result.error_message = "Request Timeout"
        except requests.exceptions.ConnectionError as e:
            result.is_valid = False
            result.error_message = f"Connection Error: {str(e)[:100]}"
        except requests.exceptions.RequestException as e:
            result.is_valid = False
            result.error_message = f"Request Error: {str(e)[:100]}"
        except Exception as e:
            result.is_valid = False
            result.error_message = f"Unexpected Error: {str(e)[:100]}"
        
        # Take screenshot of the external link page if requested (both valid and invalid)
        if take_screenshot:
            result.screenshot_path = self._screenshot_external_link(url, link_text, source_page)
        
        return result
    
    def _get_container_runtime(self) -> Optional[str]:
        """Get the container runtime command (podman or docker), or None if not available."""
        # Check for podman first (preferred on RHEL/Fedora)
        if shutil.which('podman'):
            return 'podman'
        # Fall back to docker
        if shutil.which('docker'):
            return 'docker'
        return None
    
    def _validate_link_with_linkchecker(self, url: str, source_page: str, source_section: str,
                                        link_text: str, chapter: str = "", section_number: str = "") -> LinkCheckResult:
        """
        Validate a link using the linkchecker container tool.
        This is faster and more efficient than using Selenium for retries.
        
        Returns a LinkCheckResult with validation information.
        """
        result = LinkCheckResult(
            url=url,
            source_page=source_page,
            source_section=source_section,
            link_text=link_text,
            chapter=chapter,
            section_number=section_number
        )
        
        container_runtime = self._get_container_runtime()
        if not container_runtime:
            result.is_valid = False
            result.error_message = "Container runtime (podman/docker) not available"
            return result
        
        try:
            start_time = time.time()
            
            # Get current user ID and group ID for container execution
            uid = os.getuid()
            gid = os.getgid()
            
            # Run linkchecker container
            # -r 0: no recursion (only check the URL itself)
            # --check-extern: check external URLs
            # -o text: use text output format (more reliable than SQL for single URLs)
            cmd = [
                container_runtime, 'run', '--rm',
                '-u', f'{uid}:{gid}',
                'ghcr.io/linkchecker/linkchecker:latest',
                '-r', '0',  # No recursion
                '--check-extern',  # Check external URLs
                '-o', 'text',  # Text output format (SQL doesn't output clean 200 OK results)
                url
            ]
            
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout per link
                check=False  # Don't raise on non-zero exit
            )
            
            result.response_time_ms = (time.time() - start_time) * 1000
            
            # Parse text output
            # Example output:
            # URL        `https://example.com'
            # Real URL   https://example.com
            # Check time 0.176 seconds
            # Result     Valid: 200 OK
            # or
            # Result     Error: 404 Not Found
            output = process.stdout + process.stderr
            
            # Extract Result line - this is the key field
            # Format: "Result     Valid: 200 OK" or "Result     Error: 404 Not Found"
            result_match = re.search(r'Result\s+(Valid|Error):\s*(.+?)(?:\n|$)', output)
            
            if result_match:
                validity = result_match.group(1)
                status_text = result_match.group(2).strip()
                
                result.is_valid = (validity == 'Valid')
                
                # Extract status code from status text (e.g., "200 OK" -> 200)
                status_code_match = re.match(r'(\d{3})\s*', status_text)
                if status_code_match:
                    result.status_code = int(status_code_match.group(1))
                else:
                    # Try to find any 3-digit code in the status text
                    code_match = re.search(r'\b(\d{3})\b', status_text)
                    if code_match:
                        result.status_code = int(code_match.group(1))
                
                if not result.is_valid:
                    result.error_message = status_text
                    
                # Check for warnings (e.g., redirects)
                warning_match = re.search(r'Warning\s+(.+?)(?:\n\n|\Z)', output, re.DOTALL)
                if warning_match:
                    warning_text = warning_match.group(1).strip()
                    if result.is_valid:
                        result.error_message = warning_text[:150]
                    else:
                        result.error_message = f"{status_text} ({warning_text[:100]})"
            else:
                # Check for common error patterns
                if '0 errors found' in output and '1 link' in output:
                    # Success case - linkchecker found no errors
                    result.is_valid = True
                    result.status_code = 200
                elif 'errors found' in output:
                    # Error case
                    result.is_valid = False
                    error_count = re.search(r'(\d+)\s+errors?\s+found', output)
                    result.error_message = f"Linkchecker found {error_count.group(1) if error_count else 'unknown'} error(s)"
                else:
                    # Couldn't parse output
                    result.is_valid = False
                    result.error_message = f"Could not parse linkchecker output: {output[:200]}"
                    
        except subprocess.TimeoutExpired:
            result.is_valid = False
            result.error_message = "Linkchecker timeout (30s)"
        except FileNotFoundError:
            result.is_valid = False
            result.error_message = "Container runtime not found"
        except Exception as e:
            result.is_valid = False
            result.error_message = f"Linkchecker error: {str(e)[:200]}"
        
        return result
    
    def _parse_sql_values(self, values_str: str) -> list[str]:
        """
        Parse comma-separated SQL values, respecting quoted strings.
        Handles: 'value',NULL,123,'string with, comma'
        """
        values = []
        current = ""
        in_quotes = False
        quote_char = None
        
        for char in values_str:
            if char in ("'", '"') and (not in_quotes or char == quote_char):
                if in_quotes and char == quote_char:
                    in_quotes = False
                    quote_char = None
                else:
                    in_quotes = True
                    quote_char = char
                current += char
            elif char == ',' and not in_quotes:
                values.append(current.strip())
                current = ""
            else:
                current += char
        
        if current.strip():
            values.append(current.strip())
        
        return values
    
    def _screenshot_external_link(self, url: str, link_text: str, source_page: str, max_retries: int = 3) -> Optional[str]:
        """
        Navigate to an external link and take a screenshot.
        Returns the path to the screenshot file.
        Retries on failure up to max_retries times.
        """
        section_safe = self._sanitize_filename(source_page)
        link_safe = self._sanitize_filename(link_text)[:50]
        
        for attempt in range(max_retries):
            try:
                # Navigate to the external link
                self.driver.get(url)
                time.sleep(3)  # Wait for page to load
                
                # Create subdirectory for the section
                section_dir = self.current_screenshots_dir / section_safe
                section_dir.mkdir(parents=True, exist_ok=True)
                
                timestamp = datetime.now().strftime("%H%M%S")
                filename = f"{timestamp}_{link_safe}.png"
                filepath = section_dir / filename
                
                # Take the screenshot
                self.driver.save_screenshot(str(filepath))
                
                return str(filepath)
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logging.debug(f"Screenshot attempt {attempt + 1} failed for {url}: {e}, retrying...")
                    time.sleep(2)  # Wait before retry
                else:
                    logging.warning(f"Failed to screenshot {url} after {max_retries} attempts: {e}")
                    return None
        
        return None
    
    def go_to_catalog(self, environment: str):
        """Navigate to the ROL catalog page."""
        self.logger("Navigating to course catalog...")
        base_url = self.config.get_lab_base_url(environment)
        # The catalog URL is at /rol/app/catalog
        catalog_url = base_url.replace('/courses/', '/catalog')
        self.selenium_driver.go_to_url(catalog_url)
        time.sleep(2)  # Allow page to load
    
    def filter_by_courses(self):
        """Filter the catalog to show only courses."""
        self.logger("Filtering catalog by 'Course' delivery format...")
        try:
            # Click on "Delivery formats" dropdown
            delivery_formats_btn = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(text(), "Delivery formats")]')
            ))
            delivery_formats_btn.click()
            time.sleep(0.5)
            
            # Click on "Course" checkbox
            # The checkbox is inside a listitem with text "Course"
            course_checkbox = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//input[@id="course_checkbox"]')
            ))
            
            # Check if already checked
            if not course_checkbox.is_selected():
                course_checkbox.click()
                time.sleep(1)  # Wait for filter to apply
            
            self.logger("Filter applied: Showing courses only")
            
        except TimeoutException:
            self.logger("Could not apply course filter. Proceeding with current view.")
    
    def _get_courses_from_current_page(self) -> list[dict]:
        """
        Extract courses from the current catalog page.
        Returns a list of dicts with 'id', 'title', and 'url'.
        """
        courses = []
        
        # Find all course links
        # Course links have format: /rol/app/courses/{course-id}
        course_links = self.driver.find_elements(
            By.XPATH, 
            '//a[contains(@href, "/rol/app/courses/") and (text()="Launch" or text()="View" or text()="Access" or text()="LAUNCH")]'
        )
        
        for link in course_links:
            try:
                url = link.get_attribute('href')
                if url and '/rol/app/courses/' in url:
                    # Extract course ID from URL
                    # URL format: /rol/app/courses/do0042l-4.20 or /rol/app/courses/do0042l-4.20/pages/overview
                    parts = url.split('/rol/app/courses/')
                    if len(parts) > 1:
                        course_id = parts[1].split('/')[0]
                        
                        # Get course title from nearby heading
                        try:
                            parent = link.find_element(By.XPATH, './ancestor::div[contains(@class, "pf-")]')
                            title_elem = parent.find_element(By.XPATH, './/h4')
                            title = title_elem.text
                        except:
                            title = course_id
                        
                        # Avoid duplicates
                        if not any(c['id'] == course_id for c in courses):
                            courses.append({
                                'id': course_id,
                                'title': title,
                                'url': f"/rol/app/courses/{course_id}"
                            })
            except Exception as e:
                logging.debug(f"Error processing course link: {e}")
                continue
        
        return courses
    
    def _get_total_pages(self) -> int:
        """
        Get the total number of pagination pages.
        Returns 1 if no pagination is found.
        """
        try:
            # Look for pagination element
            pagination = self.driver.find_elements(
                By.XPATH,
                '//ul[contains(@class, "pagination")]//li[not(contains(., "«")) and not(contains(., "»")) and not(contains(., "‹")) and not(contains(., "›"))]//a'
            )
            
            if pagination:
                # Get the highest page number
                page_numbers = []
                for page_link in pagination:
                    try:
                        page_num = int(page_link.text.strip())
                        page_numbers.append(page_num)
                    except ValueError:
                        continue
                
                if page_numbers:
                    return max(page_numbers)
            
            return 1
        except Exception:
            return 1
    
    def _go_to_page(self, page_number: int) -> bool:
        """
        Navigate to a specific page in the pagination.
        Returns True if successful, False otherwise.
        """
        try:
            # Find and click the page number link
            page_link = self.driver.find_element(
                By.XPATH,
                f'//ul[contains(@class, "pagination")]//li//a[text()="{page_number}"]'
            )
            
            # Use JavaScript click to avoid overlay issues
            self.driver.execute_script("arguments[0].click();", page_link)
            time.sleep(2)  # Wait for page to load
            
            return True
        except NoSuchElementException:
            return False
        except Exception as e:
            logging.debug(f"Error navigating to page {page_number}: {e}")
            return False
    
    def _click_next_page(self) -> bool:
        """
        Click the "next" (›) button to go to the next page.
        Returns True if successful, False if no next page.
        """
        try:
            # Find the "next" button (›)
            next_button = self.driver.find_element(
                By.XPATH,
                '//ul[contains(@class, "pagination")]//li//a[contains(text(), "›")]'
            )
            
            # Check if it's disabled or the last page
            parent_li = next_button.find_element(By.XPATH, './..')
            if 'disabled' in parent_li.get_attribute('class') or '':
                return False
            
            # Use JavaScript click to avoid overlay issues
            self.driver.execute_script("arguments[0].click();", next_button)
            time.sleep(2)  # Wait for page to load
            
            return True
        except NoSuchElementException:
            return False
        except Exception as e:
            logging.debug(f"Error clicking next page: {e}")
            return False
    
    def get_all_courses(self) -> list[dict]:
        """
        Get all courses from the catalog, navigating through all pagination pages.
        Returns a list of dicts with 'id', 'title', and 'url'.
        """
        self.logger("Getting list of all courses from catalog...")
        all_courses = []
        
        try:
            # Wait for course cards to load
            self.wait.until(EC.presence_of_element_located(
                (By.XPATH, '//a[contains(@href, "/rol/app/courses/")]')
            ))
            
            # Get total number of pages
            total_pages = self._get_total_pages()
            self.logger(f"  Found {total_pages} page(s) of courses")
            
            # Collect courses from each page
            current_page = 1
            while True:
                self.logger(f"  Fetching courses from page {current_page}/{total_pages}...")
                
                # Get courses from current page
                page_courses = self._get_courses_from_current_page()
                
                # Add to all courses (avoiding duplicates)
                for course in page_courses:
                    if not any(c['id'] == course['id'] for c in all_courses):
                        all_courses.append(course)
                
                self.logger(f"    Found {len(page_courses)} courses on page {current_page}")
                
                # Check if we've processed all pages
                if current_page >= total_pages:
                    break
                
                # Try to go to the next page
                if not self._click_next_page():
                    # If next button doesn't work, try direct page navigation
                    current_page += 1
                    if not self._go_to_page(current_page):
                        self.logger(f"    Could not navigate to page {current_page}, stopping.")
                        break
                else:
                    current_page += 1
                
                # Wait for new content to load
                time.sleep(1)
            
            self.logger(f"Found {len(all_courses)} total courses across {current_page} page(s)")
            
        except TimeoutException:
            self.logger("Timeout waiting for course list. Catalog might be empty or slow to load.")
        except Exception as e:
            self.logger(f"Error getting courses: {e}")
        
        return all_courses
    
    # Sections to exclude from link checking (lowercase for case-insensitive matching)
    EXCLUDED_SECTION_KEYWORDS = [
        "summary",
        "lab:",
        "guided exercise:",
        "quiz:",
        "comprehensive review",
        "preface"
    ]
    
    def _should_exclude_section(self, title: str) -> bool:
        """Check if a section should be excluded based on its title."""
        title_lower = title.lower()
        for keyword in self.EXCLUDED_SECTION_KEYWORDS:
            if keyword in title_lower:
                return True
        return False
    
    def get_course_sections(self, course_id: str, environment: str) -> list[dict]:
        """
        Get all sections from a course's table of contents.
        Returns a list of dicts with 'title', 'url', 'chapter', 'section_number'.
        Filters out sections based on EXCLUDED_SECTION_KEYWORDS.
        """
        self.logger(f"Getting sections for course: {course_id}")
        sections = []
        
        # Navigate to course if not already there
        base_url = self.config.get_lab_base_url(environment)
        course_url = f"{base_url}{course_id}"
        
        if course_id not in self.driver.current_url:
            self.selenium_driver.go_to_url(course_url)
            time.sleep(3)  # Wait for course to load
        
        try:
            # Wait for any backdrop/modal overlay to disappear before interacting
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.invisibility_of_element_located((By.XPATH, '//div[contains(@class, "pf-v5-c-backdrop")]'))
                )
            except TimeoutException:
                # If backdrop doesn't disappear, try to close it or continue anyway
                self.logger("  ⚠ Backdrop overlay still present, attempting to continue...")
                try:
                    # Try clicking outside or pressing Escape to dismiss any modal
                    self.driver.execute_script("document.body.click();")
                    time.sleep(0.5)
                except:
                    pass
            
            # Click on "Toggle Table of Contents panel" button to open TOC
            toc_button = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(@aria-label, "Table of Contents") or contains(@aria-label, "Toggle Table of Contents")]')
            ))
            
            # Check if TOC is already open by looking for the TOC region
            try:
                toc_region = self.driver.find_element(By.XPATH, '//div[contains(@class, "ToC")] | //div[@aria-label="Table of contents"]')
                if not toc_region.is_displayed():
                    # Use JavaScript click to bypass any overlay issues
                    self.driver.execute_script("arguments[0].click();", toc_button)
                    time.sleep(1)
            except NoSuchElementException:
                # Use JavaScript click to bypass any overlay issues
                self.driver.execute_script("arguments[0].click();", toc_button)
                time.sleep(1)
            
            # Click on "Expand all" toggle switch to show all chapters
            # The toggle is a switch with class "pf-v5-c-switch" and label "Expand all"
            try:
                # Try to find the Expand all switch/toggle
                expand_all_selectors = [
                    # PatternFly v5 switch with "Expand all" label
                    '//label[contains(@class, "pf-v5-c-switch") and .//span[contains(text(), "Expand all")]]',
                    '//span[contains(@class, "pf-v5-c-switch__label") and contains(text(), "Expand all")]/..',
                    '//input[following-sibling::*[contains(text(), "Expand all")]]',
                    '//button[contains(text(), "Expand all")]',
                    # Generic switch/toggle near "Expand all" text
                    '//*[contains(text(), "Expand all")]/ancestor::label[contains(@class, "switch")]//input',
                ]
                
                expand_all = None
                for selector in expand_all_selectors:
                    try:
                        expand_all = self.driver.find_element(By.XPATH, selector)
                        if expand_all.is_displayed():
                            break
                    except NoSuchElementException:
                        continue
                
                if expand_all and expand_all.is_displayed():
                    # Check if already expanded by looking for aria-checked or checked state
                    is_checked = expand_all.get_attribute('aria-checked') == 'true' or expand_all.get_attribute('checked')
                    if not is_checked:
                        # Use JavaScript click to bypass overlay issues
                        self.driver.execute_script("arguments[0].click();", expand_all)
                        time.sleep(1)
                        self.logger("  Clicked 'Expand all' toggle")
                else:
                    self.logger("  'Expand all' toggle not found, expanding chapters manually...")
            except Exception as e:
                logging.debug(f"Could not use 'Expand all' toggle: {e}")
            
            # Ensure all chapters are expanded by clicking collapsed accordion toggles
            try:
                # Find all collapsed chapter accordion buttons
                collapsed_chapters = self.driver.find_elements(
                    By.XPATH,
                    '//button[contains(@class, "pf-v5-c-accordion__toggle") and @aria-expanded="false"]'
                )
                
                if collapsed_chapters:
                    self.logger(f"  Expanding {len(collapsed_chapters)} collapsed chapters...")
                    for btn in collapsed_chapters:
                        try:
                            self.driver.execute_script("arguments[0].click();", btn)
                            time.sleep(0.3)
                        except Exception as e:
                            logging.debug(f"Could not expand chapter: {e}")
                            continue
                    time.sleep(0.5)
            except Exception as e:
                logging.debug(f"Error expanding chapters: {e}")
            
            # Get all section links from TOC
            # Look for links that point to course pages
            section_links = self.driver.find_elements(
                By.XPATH,
                '//a[contains(@href, "/pages/") and @data-analytics-id="toc-link-ole-lp"]'
            )
            
            # If no links found with specific data-analytics-id, try broader search
            if not section_links:
                section_links = self.driver.find_elements(
                    By.XPATH,
                    '//div[contains(@class, "ToC")]//a[contains(@href, "/pages/")]'
                )
            
            # If still no links, try even broader search
            if not section_links:
                section_links = self.driver.find_elements(
                    By.XPATH,
                    '//a[contains(@href, "/pages/")]'
                )
            
            all_sections = []
            for link in section_links:
                try:
                    url = link.get_attribute('href')
                    title = link.text.strip()
                    
                    if url and title and '/pages/' in url:
                        # Extract just the path
                        if 'rol.redhat.com' in url:
                            path = url.split('rol.redhat.com')[1]
                        else:
                            path = url
                        
                        # Parse chapter and section from title
                        chapter, section_number, clean_title = self._parse_section_title(title)
                        
                        # Avoid duplicates
                        if not any(s['url'] == path for s in all_sections):
                            all_sections.append({
                                'title': title,
                                'clean_title': clean_title,
                                'url': path,
                                'chapter': chapter,
                                'section_number': section_number
                            })
                except Exception as e:
                    logging.debug(f"Error processing section link: {e}")
                    continue
            
            # Filter out excluded sections
            for section in all_sections:
                if not self._should_exclude_section(section['title']):
                    sections.append(section)
            
            excluded_count = len(all_sections) - len(sections)
            self.logger(f"Found {len(all_sections)} total sections, {excluded_count} excluded, {len(sections)} to check")
            
        except TimeoutException:
            self.logger(f"Timeout waiting for TOC in course {course_id}")
        except Exception as e:
            self.logger(f"Error getting course sections: {e}")
        
        return sections
    
    def extract_links_from_page(self, page_url: str, page_title: str) -> list[dict]:
        """
        Extract all external links from a course page, especially from the References section.
        Returns a list of link dicts with 'url', 'text', and 'section'.
        """
        links = []
        
        try:
            # Navigate to the page
            full_url = f"https://rol.redhat.com{page_url}" if not page_url.startswith('http') else page_url
            self.selenium_driver.go_to_url(full_url)
            time.sleep(4)  # Wait for page content to load
            
            # First, try to find the References section
            try:
                # Find the References heading
                references_heading = self.driver.find_element(
                    By.XPATH,
                    '//h3[contains(text(), "References")] | //h3[@class="title" and contains(text(), "References")]'
                )
                
                # Get the parent container of the references section
                # The structure is: <div class="note references"><h3>References</h3>...<a>links</a>...</div>
                references_container = references_heading.find_element(By.XPATH, './..')
                
                # Find all links within the references section
                ref_links = references_container.find_elements(By.XPATH, './/a[@href]')
                
                for link in ref_links:
                    try:
                        url = link.get_attribute('href')
                        text = link.text.strip()
                        
                        if url and not self._should_ignore_url(url):
                            links.append({
                                'url': url,
                                'text': text or url,
                                'section': 'References'
                            })
                    except:
                        continue
                        
            except NoSuchElementException:
                # No References section on this page
                pass
            
            # Also extract other external links from the main content
            try:
                # Find links in the main content area (excluding navigation)
                main_content = self.driver.find_element(
                    By.XPATH,
                    '//main//div[contains(@class, "content")] | //div[@role="tabpanel"] | //article'
                )
                
                content_links = main_content.find_elements(By.XPATH, './/a[@href and @class="ulink"]')
                
                for link in content_links:
                    try:
                        url = link.get_attribute('href')
                        text = link.text.strip()
                        
                        if url and not self._should_ignore_url(url):
                            # Check if we already have this link from References
                            if not any(l['url'] == url for l in links):
                                links.append({
                                    'url': url,
                                    'text': text or url,
                                    'section': 'Content'
                                })
                    except:
                        continue
                        
            except NoSuchElementException:
                pass
                
        except Exception as e:
            self.logger(f"Error extracting links from {page_url}: {e}")
        
        return links
    
    def get_available_versions(self, course_id: str, environment: str) -> list[str]:
        """
        Get all available versions for a course by checking the settings panel.
        Returns a list of version strings (e.g., ['10.0', '9.3', '8.2']).
        """
        versions = []
        
        try:
            # Navigate to the course first
            course_url = self.config.get_lab_base_url(environment) + course_id
            self.logger(f"Getting available versions for {course_id}...")
            self.driver.get(course_url)
            time.sleep(3)  # Wait for page to load
            
            # Wait for any loading overlay to disappear
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, ".pf-v5-c-backdrop"))
                )
            except:
                pass
            
            # Click the settings button
            settings_btn = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "HUD__dock-item__btn--settings"))
            )
            self.driver.execute_script("arguments[0].click();", settings_btn)
            time.sleep(1)
            
            # Click the version dropdown
            version_dropdown = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH, 
                    "//div[contains(@class, 'settings-panel-version-selector')]//button[contains(@class, 'menu-toggle')]"
                ))
            )
            self.driver.execute_script("arguments[0].click();", version_dropdown)
            time.sleep(0.5)
            
            # Get all version options
            version_items = self.driver.find_elements(
                By.XPATH,
                "//div[contains(@class, 'settings-panel-version-selector')]//ul[@role='menu']//button[@role='menuitem']"
            )
            
            for item in version_items:
                try:
                    version_text = item.find_element(
                        By.XPATH, ".//span[contains(@class, 'menu__item-text')]"
                    ).text.strip()
                    if version_text:
                        versions.append(version_text)
                except:
                    pass
            
            # Close the dropdown by clicking elsewhere
            self.driver.find_element(By.TAG_NAME, "body").click()
            time.sleep(0.3)
            
            # Close settings panel
            try:
                settings_btn = self.driver.find_element(By.ID, "HUD__dock-item__btn--settings")
                self.driver.execute_script("arguments[0].click();", settings_btn)
            except:
                pass
                
        except Exception as e:
            self.logger(f"Error getting versions for {course_id}: {e}")
        
        return versions
    
    def check_all_course_versions(self, course_id: str, environment: str,
                                   take_screenshots: bool = True) -> list[CourseCheckReport]:
        """
        Check links in all available versions of a course.
        Navigates directly to each version URL (e.g., /courses/do280-4.18/).
        Returns a list of CourseCheckReports, one per version.
        """
        # Parse the course ID to get the base course name
        course_name, current_version = self._parse_course_id(course_id)
        
        # Get all available versions
        versions = self.get_available_versions(course_id, environment)
        
        if not versions:
            print(f"  ⚠ No versions found for {course_name}, checking {course_id} only")
            report = self.check_course_links(course_id, environment, take_screenshots)
            return [report]
        
        print(f"  📚 Found {len(versions)} version(s) for {course_name}: {', '.join(versions)}")
        
        reports = []
        
        for idx, version in enumerate(versions):
            version_course_id = f"{course_name}-{version}"
            print(f"\n  🔄 Checking version {idx + 1}/{len(versions)}: {version_course_id}")
            
            # Navigate directly to this version - check_course_links handles navigation
            # URL structure: https://rol.redhat.com/rol/app/courses/{course_id}/pages/...
            # Note: check_course_links already appends to self.reports
            report = self.check_course_links(version_course_id, environment, take_screenshots)
            
            reports.append(report)
        
        return reports
    
    def check_course_links(self, course_id: str, environment: str, 
                           take_screenshots: bool = True) -> CourseCheckReport:
        """
        Check all links in a course.
        Returns a CourseCheckReport with results.
        """
        self.logger(f"Checking links for course: {course_id}")
        
        # Setup screenshots directory for this course: run_dir/course_name/version/
        course_name, version = self._parse_course_id(course_id)
        self.current_screenshots_dir = self.run_screenshots_dir / course_name / version
        self.current_screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine validation method: use linkchecker container when screenshots disabled (faster)
        use_linkchecker = not take_screenshots and self._get_container_runtime() is not None
        if use_linkchecker:
            self.logger("  Using linkchecker container for fast validation (no screenshots)")
        elif take_screenshots:
            self.logger("  Using Selenium for validation with screenshots")
        else:
            self.logger("  Using HTTP requests for validation (container not available)")
        
        report = CourseCheckReport(
            course_id=course_id,
            check_started=datetime.now(),
            screenshots_dir=str(self.current_screenshots_dir),
            environment=environment
        )
        
        try:
            # Get all sections in the course
            sections = self.get_course_sections(course_id, environment)
            
            # Get course title from first section's chapter or course ID
            if sections:
                report.course_title = sections[0].get('chapter', '') or course_id
            
            current_chapter = ""
            
            for idx, section in enumerate(sections):
                section_title = section['title']
                section_url = section['url']
                chapter = section.get('chapter', '')
                section_number = section.get('section_number', '')
                
                # Log chapter changes
                if chapter and chapter != current_chapter:
                    current_chapter = chapter
                    self.logger(f"\n  📚 {chapter}")
                
                self.logger(f"    [{idx+1}/{len(sections)}] {section_title}")
                
                # Extract links from this section
                links = self.extract_links_from_page(section_url, section_title)
                
                # Create section info
                section_info = SectionInfo(
                    title=section_title,
                    url=section_url,
                    chapter=chapter,
                    section_number=section_number,
                    links=links
                )
                report.sections.append(section_info)
                
                # Filter out ignored URLs and prepare links for checking
                links_to_check = []
                for link_info in links:
                    url = link_info['url']
                    if self._should_ignore_url(url):
                        report.ignored_links += 1
                    else:
                        links_to_check.append({
                            'url': url,
                            'source_page': section_title,
                            'source_section': link_info['section'],
                            'link_text': link_info['text'],
                            'chapter': chapter,
                            'section_number': section_number
                        })
                
                report.total_links += len(links_to_check)
                
                # Use parallel checking with linkchecker, sequential with Selenium
                if use_linkchecker and links_to_check:
                    # Parallel link checking with ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=self.MAX_LINK_WORKERS) as executor:
                        # Submit all link checks
                        future_to_link = {
                            executor.submit(
                                self._validate_link_with_linkchecker,
                                link['url'],
                                link['source_page'],
                                link['source_section'],
                                link['link_text'],
                                link['chapter'],
                                link['section_number']
                            ): link for link in links_to_check
                        }
                        
                        # Process results as they complete
                        for future in as_completed(future_to_link):
                            link = future_to_link[future]
                            try:
                                result = future.result()
                            except Exception as e:
                                result = LinkCheckResult(
                                    url=link['url'],
                                    source_page=link['source_page'],
                                    source_section=link['source_section'],
                                    link_text=link['link_text'],
                                    chapter=link['chapter'],
                                    section_number=link['section_number'],
                                    is_valid=False,
                                    error_message=f"Thread error: {str(e)[:100]}"
                                )
                            
                            report.results.append(result)
                            
                            status_code = result.status_code or 'ERR'
                            if result.is_valid:
                                report.valid_links += 1
                                print(f"      ✓ [{status_code}] {link['url']}")
                            else:
                                report.broken_links += 1
                                print(f"      ✗ [{status_code}] {link['url']} - {result.error_message}")
                else:
                    # Sequential checking (with Selenium for screenshots)
                    for link in links_to_check:
                        result = self._validate_link(
                            url=link['url'],
                            source_page=link['source_page'],
                            source_section=link['source_section'],
                            link_text=link['link_text'],
                            chapter=link['chapter'],
                            section_number=link['section_number'],
                            take_screenshot=take_screenshots
                        )
                        
                        report.results.append(result)
                        
                        status_code = result.status_code or 'ERR'
                        if result.is_valid:
                            report.valid_links += 1
                            print(f"      ✓ [{status_code}] {link['url']}")
                        else:
                            report.broken_links += 1
                            print(f"      ✗ [{status_code}] {link['url']} - {result.error_message}")
            
        except Exception as e:
            self.logger(f"Error checking course {course_id}: {e}")
        
        report.check_completed = datetime.now()
        self.reports.append(report)
        
        return report
    
    def check_all_courses(self, environment: str, limit: int = None,
                          take_screenshots: bool = True) -> list[CourseCheckReport]:
        """
        Check links in all courses in the catalog.
        Returns a list of CourseCheckReport objects.
        """
        self.logger("Fetching course catalog...")
        
        # Go to catalog and filter by courses
        self.go_to_catalog(environment)
        self.filter_by_courses()
        
        # Get list of all courses
        courses = self.get_all_courses()
        
        if limit:
            courses = courses[:limit]
        
        print(f"  Found {len(courses)} courses to check")
        
        reports = []
        for i, course in enumerate(courses):
            self.logger(f"Checking course [{i+1}/{len(courses)}]: {course['id']}")
            
            try:
                version_reports = self.check_all_course_versions(course['id'], environment, take_screenshots)
                reports.extend(version_reports)
                # Sum up stats across all versions
                total_valid = sum(r.valid_links for r in version_reports)
                total_broken = sum(r.broken_links for r in version_reports)
                print(f"    ✓ {total_valid} valid, {total_broken} broken links across {len(version_reports)} version(s)")
            except Exception as e:
                print(f"    ✗ Failed: {e}")
                continue
        
        return reports
    
    def retry_failed_links(self, take_screenshots: bool = False) -> int:
        """
        Retry all links that failed in the first round using the linkchecker container.
        This is faster and more efficient than using Selenium for retries.
        Updates the reports in place with new results.
        Returns the number of links that were fixed (now valid).
        """
        total_failed = sum(r.broken_links for r in self.reports)
        if total_failed == 0:
            print("No failed links to retry.")
            return 0
        
        # Check if linkchecker is available
        container_runtime = self._get_container_runtime()
        use_linkchecker = container_runtime is not None
        if use_linkchecker:
            print(f"🔗 Using linkchecker container for {total_failed} failed link(s)...")
        else:
            print("⚠ Warning: linkchecker container not available (podman/docker not found).")
            print("  Falling back to Selenium-based validation (slower).")
            print("  Install podman or docker to use faster linkchecker retries.\n")
        
        self.logger(f"Retrying {total_failed} failed link(s) using {'linkchecker' if use_linkchecker else 'Selenium'}...")
        
        fixed_count = 0
        
        for report in self.reports:
            failed_results = [r for r in report.results if not r.is_valid]
            
            if not failed_results:
                continue
            
            print(f"\n  📚 {report.course_id} ({len(failed_results)} failed links)")
            
            for result in failed_results:
                # Use linkchecker if available, otherwise fall back to Selenium
                if use_linkchecker:
                    new_result = self._validate_link_with_linkchecker(
                        url=result.url,
                        source_page=result.source_page,
                        source_section=result.source_section,
                        link_text=result.link_text,
                        chapter=result.chapter,
                        section_number=result.section_number
                    )
                else:
                    # Fallback to Selenium-based validation
                    new_result = self._validate_link(
                        url=result.url,
                        source_page=result.source_page,
                        source_section=result.source_section,
                        link_text=result.link_text,
                        chapter=result.chapter,
                        section_number=result.section_number,
                        take_screenshot=take_screenshots
                    )
                
                # Update the result in place
                result.status_code = new_result.status_code
                result.is_valid = new_result.is_valid
                result.error_message = new_result.error_message
                result.response_time_ms = new_result.response_time_ms
                
                # Update screenshot if a new one was taken (only if using Selenium fallback)
                if new_result.screenshot_path:
                    result.screenshot_path = new_result.screenshot_path
                
                status_code = result.status_code or 'ERR'
                if result.is_valid:
                    fixed_count += 1
                    report.broken_links -= 1
                    report.valid_links += 1
                    print(f"      ✓ [{status_code}] FIXED: {result.url}")
                else:
                    print(f"      ✗ [{status_code}] {result.url} - {result.error_message}")
        
        print(f"\n  ✓ Retry complete: {fixed_count}/{total_failed} links fixed")
        
        return fixed_count
    
    def login_jira(self) -> bool:
        """
        Login to Jira using the JiraHandler.
        
        First tries session login (SSO may already be active from ROL).
        If not logged in, attempts SSO login with available credentials.
        If credentials are not available, prompts for manual authentication.
        
        Returns True if login was successful or already logged in.
        """
        return self.jira_handler.login(use_session=True)
    
    def _build_broken_links_jql(self, course_id: str, broken_urls: list[str]) -> str:
        """
        Build a JQL query to search for existing tickets with the same broken URLs.
        This helps avoid creating duplicate tickets.
        """
        from urllib.parse import quote
        
        # Parse course name (component) from course_id
        course_name, version = self._parse_course_id(course_id)
        course_name_upper = course_name.upper()
        
        # Handle RH199 special case (like in snow_ai_processor.py)
        if course_name_upper == "RH199":
            component_clause = 'component in (RH134, RH199, RH124)'
        else:
            component_clause = f'component = "{course_name_upper}"'
        
        # Build URL search clause - search for any of the broken URLs in description
        # Limit to first 5 URLs to avoid JQL length limits
        url_searches = []
        for url in broken_urls[:5]:
            # Extract domain and path for searching (avoid special chars issues)
            url_clean = url.replace('https://', '').replace('http://', '').split('#')[0]
            if len(url_clean) > 50:
                url_clean = url_clean[:50]
            url_searches.append(f'text ~ "{url_clean}"')
        
        url_clause = f"({' OR '.join(url_searches)})" if url_searches else ""
        
        # Build full JQL
        jql = f'project = PTL AND resolution = Unresolved AND {component_clause}'
        if url_clause:
            jql += f' AND {url_clause}'
        jql += ' ORDER BY created DESC'
        
        return jql
    
    def _build_broken_links_search_url(self, course_id: str, broken_urls: list[str]) -> str:
        """Build Jira search URL to check for existing broken link tickets."""
        from urllib.parse import quote
        jql = self._build_broken_links_jql(course_id, broken_urls)
        return f"https://issues.redhat.com/issues/?jql={quote(jql)}"
    
    def create_jira_for_broken_links(self, report: 'CourseCheckReport') -> bool:
        """
        Create a Jira ticket for broken links found in a course.
        Opens a new tab with the prefilled ticket for human review.
        Uses the report's own pdf_file and json_file paths.
        
        Args:
            report: CourseCheckReport with broken links
            
        Returns:
            True if Jira creation was initiated, False otherwise
        """
        from urllib.parse import quote
        from selenium.webdriver.common.keys import Keys
        
        # Get broken links from report
        broken_results = [r for r in report.results if not r.is_valid]
        
        if not broken_results:
            self.logger("No broken links to report for this course")
            return False
        
        # Use report's own files
        pdf_file = report.pdf_file if report.pdf_file else None
        json_file = report.json_file if report.json_file else None
        
        broken_urls = [r.url for r in broken_results]
        course_name, version = self._parse_course_id(report.course_id)
        
        self.logger(f"Creating Jira ticket for {len(broken_results)} broken links in {report.course_id}")
        
        # First, open search to check for existing tickets
        search_url = self._build_broken_links_search_url(report.course_id, broken_urls)
        
        # Open in new window to not interfere with ongoing course link checking
        self.driver.switch_to.new_window('window')
        
        # Navigate to Jira project
        self.driver.get("https://issues.redhat.com/projects/PTL/issues")
        time.sleep(5)
        
        try:
            # Click Create button
            WebDriverWait(self.driver, 20).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="create_link"]'))
            ).click()
            time.sleep(2)
            
            # Select Text mode for description
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="description-wiki-edit"]/nav/div/div/ul/li[2]/button'))
                ).click()
            except:
                pass
            
            # Fill Summary
            summary = f"{course_name.upper()}-{version}: Broken Links Report - {len(broken_results)} broken link(s)"
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="summary"]'))
            ).send_keys(summary)
            
            # Build description with broken links
            description_lines = [
                f"*Broken Links Report for {course_name.upper()} v{version}*",
                f"",
                f"||Chapter||Section||Link Text||URL||Status Code||Error||",
            ]
            
            # Build lookup from section title to section URL for creating hyperlinks
            # Get base URL from config based on environment used for this report
            # Config URLs are like https://rol.redhat.com/rol/app/courses/ - extract just the domain
            config_url = self.config.get_lab_base_url(report.environment) or "https://rol.redhat.com/rol/app/courses/"
            rol_base_url = config_url.split('/rol/')[0] if '/rol/' in config_url else config_url.rstrip('/')
            section_url_lookup = {section.title: section.url for section in report.sections}
            
            for result in broken_results:
                status = result.status_code or 'ERR'
                error = (result.error_message or 'Unknown')[:50]
                link_text = (result.link_text or 'N/A')[:30]
                # Create hyperlink to the course section page for easy developer access
                section_url = section_url_lookup.get(result.source_page, "")
                if section_url:
                    full_url = f"{rol_base_url}{section_url}" if section_url.startswith('/') else section_url
                    section_link = f"[{result.section_number}|{full_url}]"
                else:
                    section_link = result.section_number
                description_lines.append(
                    f"|{result.chapter}|{section_link}|{link_text}|[{result.url}]|{status}|{error}|"
                )
            
            description_lines.extend([
                "",
                "*Search for existing tickets:*",
                f"[Search JQL|{search_url}]",
                "",
                f"_Report generated: {report.check_completed.strftime('%Y-%m-%d %H:%M:%S') if report.check_completed else 'N/A'}_",
            ])
            
            if pdf_file:
                description_lines.append(f"_PDF Report: {Path(pdf_file).name}_")
            if json_file:
                description_lines.append(f"_JSON Report: {Path(json_file).name}_")
            
            description = "\n".join(description_lines)
            
            # Fill description
            desc_field = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="description"]'))
            )
            desc_field.clear()
            desc_field.send_keys(description)
            
            # Switch back to Visual mode
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="description-wiki-edit"]/nav/div/div/ul/li[1]/button'))
                ).click()
            except:
                pass
            
            # Set Component (course)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="components-textarea"]'))
                ).send_keys(course_name.upper())
            except Exception as e:
                logging.debug(f"Failed to set component: {e}")
            
            
            # Attach files (PDF and JSON reports)
            files_to_attach = []
            if pdf_file and os.path.exists(pdf_file):
                files_to_attach.append(os.path.abspath(pdf_file))
            if json_file and os.path.exists(json_file):
                files_to_attach.append(os.path.abspath(json_file))
            
            attached_files = []
            for file_path in files_to_attach:
                try:
                    print(f"Attaching file: {file_path}")
                    # Find the file input element - need to find it fresh each time
                    # as Jira may recreate the element after each upload
                    file_inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
                    if file_inputs:
                        file_input = file_inputs[0]
                        # Clear any existing value using JavaScript
                        self.driver.execute_script("arguments[0].value = '';", file_input)
                        time.sleep(0.5)
                        # Send the file path
                        file_input.send_keys(file_path)
                        attached_files.append(os.path.basename(file_path))
                        # Wait for upload to complete - check for upload progress to finish
                        time.sleep(5)
                        # Wait until no upload progress bars are visible
                        try:
                            WebDriverWait(self.driver, 30).until_not(
                                EC.presence_of_element_located((By.CSS_SELECTOR, '.upload-progress, .uploading'))
                            )
                        except:
                            pass  # Continue if no progress indicator found
                        time.sleep(2)  # Extra buffer between uploads
                    else:
                        logging.debug(f"No file input found for {file_path}")
                except Exception as e:
                    logging.debug(f"Failed to attach file {file_path}: {e}")        

            # Set Version
            try:
                version_field = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="versions-textarea"]'))
                )
                version_field.send_keys(Keys.HOME)
                version_field.send_keys(course_name.upper())
            except Exception as e:
                logging.debug(f"Failed to set version: {e}")

            # Set Priority to Minor
            try:
                # Click Priority tab
                WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//ul[@role="tablist"]//strong[text()="Priority"]'))
                ).click()
                time.sleep(0.5)
                
                priority_field = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="priority-field"]'))
                )
                priority_field.send_keys(Keys.CONTROL + "a")
                priority_field.send_keys(Keys.DELETE)
                priority_field.send_keys("Minor")
                priority_field.send_keys(Keys.TAB)
            except Exception as e:
                logging.debug(f"Failed to set priority: {e}")

            
            print(f"\n  📝 Jira ticket prefilled in new tab")
            print(f"     Summary: {summary}")
            print(f"     Broken links: {len(broken_results)}")
            if attached_files:
                print(f"     📎 Attached: {', '.join(attached_files)}")
            else:
                if pdf_file:
                    print(f"     📎 Please attach manually: {pdf_file}")
                if json_file:
                    print(f"     📎 Please attach manually: {json_file}")
            print(f"     ⚠️  Review and submit manually")
            
            return True
            
        except Exception as e:
            self.logger(f"Failed to create Jira ticket: {e}")
            return False
    
    def create_jiras_for_all_broken_links(self) -> int:
        """
        Create Jira tickets for all courses with broken links.
        Opens one tab per course that has broken links.
        Each ticket uses its own course-specific PDF and JSON report files.
        
        Returns the number of Jira tickets initiated.
        """
        # Login to Jira first
        if not self.login_jira():
            self.logger("Failed to login to Jira - skipping ticket creation")
            return 0
        
        jira_count = 0
        
        for report in self.reports:
            broken_count = sum(1 for r in report.results if not r.is_valid)
            if broken_count > 0:
                if self.create_jira_for_broken_links(report):
                    jira_count += 1
                time.sleep(2)  # Brief pause between tabs
        
        if jira_count > 0:
            print(f"\n{'='*60}")
            print(f"Created {jira_count} Jira ticket draft(s) in new tabs")
            print(f"Please review each tab and submit manually")
            print(f"{'='*60}")
        
        return jira_count
    
    def generate_report(self, output_format: str = 'text', output_file: str = None) -> str:
        """
        Generate a summary report of all link checks.
        Supports 'text', 'json', 'detailed', or 'pdf' format.
        
        For PDF format, output_file is required and the method returns the file path.
        """
        if output_format == 'json':
            return self._generate_json_report()
        elif output_format == 'detailed':
            return self._generate_detailed_text_report()
        elif output_format == 'pdf':
            if not PDF_SUPPORT:
                raise ImportError("PDF support requires 'reportlab' and 'Pillow'. Install with: pip install reportlab Pillow")
            if not output_file:
                # Generate default filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"link_check_report_{timestamp}.pdf"
            return self._generate_pdf_report(output_file)
        else:
            return self._generate_text_report()
    
    def _generate_text_report(self) -> str:
        """Generate a human-readable text report."""
        lines = []
        lines.append("=" * 100)
        lines.append("LINK CHECK REPORT")
        lines.append("=" * 100)
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        total_links = 0
        total_valid = 0
        total_broken = 0
        total_ignored = 0
        
        for report in self.reports:
            total_links += report.total_links
            total_valid += report.valid_links
            total_broken += report.broken_links
            total_ignored += report.ignored_links
        
        lines.append("SUMMARY")
        lines.append("-" * 50)
        lines.append(f"  Courses checked:  {len(self.reports)}")
        lines.append(f"  Total links:      {total_links}")
        lines.append(f"  Valid links:      {total_valid}")
        lines.append(f"  Broken links:     {total_broken}")
        lines.append(f"  Ignored links:    {total_ignored}")
        lines.append("")
        
        # Detailed broken links
        if total_broken > 0:
            lines.append("-" * 100)
            lines.append("BROKEN LINKS")
            lines.append("-" * 100)
            
            for report in self.reports:
                broken = [r for r in report.results if not r.is_valid]
                if broken:
                    lines.append(f"\n📕 Course: {report.course_id}")
                    lines.append(f"   Screenshots: {report.screenshots_dir}")
                    
                    for result in broken:
                        lines.append(f"\n   📍 Location:")
                        lines.append(f"      Chapter:  {result.chapter or 'N/A'}")
                        lines.append(f"      Section:  {result.section_number or 'N/A'} - {result.source_page}")
                        lines.append(f"      Area:     {result.source_section}")
                        lines.append(f"   🔗 Link:")
                        lines.append(f"      Text:     {result.link_text}")
                        lines.append(f"      URL:      {result.url}")
                        lines.append(f"   ❌ Error:")
                        lines.append(f"      Status:   {result.status_code or 'N/A'} - {result.error_message}")
                        if result.screenshot_path:
                            lines.append(f"   📸 Screenshot: {result.screenshot_path}")
        
        lines.append("")
        lines.append("=" * 100)
        lines.append("END OF REPORT")
        lines.append("=" * 100)
        
        return "\n".join(lines)
    
    def _generate_detailed_text_report(self) -> str:
        """Generate a detailed text report with full chapter/section hierarchy."""
        ROL_BASE_URL = "https://rol.redhat.com"
        
        lines = []
        lines.append("=" * 100)
        lines.append("DETAILED LINK CHECK REPORT")
        lines.append("=" * 100)
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        for report in self.reports:
            lines.append("")
            lines.append("*" * 100)
            lines.append(f"COURSE: {report.course_id}")
            lines.append(f"Started: {report.check_started}")
            lines.append(f"Completed: {report.check_completed}")
            lines.append(f"Screenshots Directory: {report.screenshots_dir}")
            lines.append("*" * 100)
            
            # Course summary at the beginning
            lines.append("")
            lines.append("COURSE SUMMARY")
            lines.append("-" * 50)
            lines.append(f"  Sections checked: {len(report.sections)}")
            lines.append(f"  Total links:      {report.total_links}")
            lines.append(f"  Valid links:      {report.valid_links}")
            lines.append(f"  Broken links:     {report.broken_links}")
            lines.append(f"  Ignored links:    {report.ignored_links}")
            
            # Broken links summary at the top with references
            broken = [r for r in report.results if not r.is_valid]
            if broken:
                lines.append("")
                lines.append("⚠️  BROKEN LINKS IN THIS COURSE:")
                lines.append("-" * 50)
                for idx, result in enumerate(broken, 1):
                    lines.append(f"  {idx}. {result.link_text}")
                    lines.append(f"     URL: {result.url}")
                    lines.append(f"     Location: {result.chapter} → {result.source_page}")
                    lines.append(f"     Error: [{result.status_code or 'N/A'}] {result.error_message}")
                    if result.screenshot_path:
                        lines.append(f"     Screenshot: {result.screenshot_path}")
                    lines.append("")
            
            # Group sections by chapter
            chapters = {}
            for section in report.sections:
                chapter = section.chapter or "General"
                if chapter not in chapters:
                    chapters[chapter] = []
                chapters[chapter].append(section)
            
            # Output by chapter
            for chapter_name, chapter_sections in chapters.items():
                lines.append("")
                lines.append(f"📚 {chapter_name}")
                lines.append("=" * 80)
                
                for section in chapter_sections:
                    lines.append("")
                    section_header = section.section_number or ""
                    if section_header:
                        section_header = f"[{section_header}] "
                    lines.append(f"  📄 {section_header}{section.title}")
                    # Full ROL URL
                    full_url = f"{ROL_BASE_URL}{section.url}" if section.url.startswith('/') else section.url
                    lines.append(f"     ROL URL: {full_url}")
                    
                    if section.screenshot_path:
                        lines.append(f"     📸 Screenshot: {os.path.basename(section.screenshot_path)}")
                    
                    if section.links:
                        lines.append(f"     Links found: {len(section.links)}")
                        lines.append("")
                        
                        # Get results for this section
                        section_results = [r for r in report.results if r.source_page == section.title]
                        
                        # Group by section type (References vs Content)
                        references_links = [r for r in section_results if r.source_section == 'References']
                        content_links = [r for r in section_results if r.source_section == 'Content']
                        
                        if references_links:
                            lines.append("     📎 REFERENCES:")
                            for result in references_links:
                                status_icon = "✓" if result.is_valid else "✗"
                                status_code = result.status_code if result.status_code else "ERR"
                                status_desc = self._get_http_status_description(result.status_code)
                                
                                lines.append(f"        {status_icon} [{status_code}] {result.link_text}")
                                lines.append(f"           URL: {result.url}")
                                lines.append(f"           Status: {status_desc}")
                                if result.response_time_ms:
                                    lines.append(f"           Response Time: {result.response_time_ms:.0f}ms")
                                if not result.is_valid:
                                    lines.append(f"           ⚠ Error: {result.error_message}")
                                lines.append("")
                        
                        if content_links:
                            lines.append("     📝 CONTENT LINKS:")
                            for result in content_links:
                                status_icon = "✓" if result.is_valid else "✗"
                                status_code = result.status_code if result.status_code else "ERR"
                                status_desc = self._get_http_status_description(result.status_code)
                                
                                lines.append(f"        {status_icon} [{status_code}] {result.link_text}")
                                lines.append(f"           URL: {result.url}")
                                lines.append(f"           Status: {status_desc}")
                                if result.response_time_ms:
                                    lines.append(f"           Response Time: {result.response_time_ms:.0f}ms")
                                if not result.is_valid:
                                    lines.append(f"           ⚠ Error: {result.error_message}")
                                lines.append("")
                    else:
                        lines.append("     (No external links found)")
                    
                    lines.append("-" * 80)
        
        lines.append("")
        lines.append("=" * 100)
        lines.append("END OF DETAILED REPORT")
        lines.append("=" * 100)
        
        return "\n".join(lines)
    
    def _generate_json_report(self) -> str:
        """Generate a JSON report with full details."""
        report_data = {
            'generated': datetime.now().isoformat(),
            'screenshots_base_dir': str(self.screenshots_base_dir),
            'summary': {
                'courses_checked': len(self.reports),
                'total_links': sum(r.total_links for r in self.reports),
                'valid_links': sum(r.valid_links for r in self.reports),
                'broken_links': sum(r.broken_links for r in self.reports),
                'ignored_links': sum(r.ignored_links for r in self.reports),
            },
            'courses': []
        }
        
        for report in self.reports:
            # Group sections by chapter
            chapters = {}
            for section in report.sections:
                chapter = section.chapter or "General"
                if chapter not in chapters:
                    chapters[chapter] = []
                
                section_data = {
                    'title': section.title,
                    'section_number': section.section_number,
                    'url': section.url,
                    'screenshot': section.screenshot_path,
                    'links': []
                }
                
                # Get results for this section
                section_results = [r for r in report.results if r.source_page == section.title]
                for result in section_results:
                    section_data['links'].append({
                        'text': result.link_text,
                        'url': result.url,
                        'location': result.source_section,
                        'status_code': result.status_code,
                        'status_description': self._get_http_status_description(result.status_code),
                        'is_valid': result.is_valid,
                        'error': result.error_message,
                        'response_time_ms': result.response_time_ms,
                    })
                
                chapters[chapter].append(section_data)
            
            course_data = {
                'course_id': report.course_id,
                'course_title': report.course_title,
                'screenshots_dir': report.screenshots_dir,
                'check_started': report.check_started.isoformat() if report.check_started else None,
                'check_completed': report.check_completed.isoformat() if report.check_completed else None,
                'summary': {
                    'total_links': report.total_links,
                    'valid_links': report.valid_links,
                    'broken_links': report.broken_links,
                    'ignored_links': report.ignored_links,
                    'sections_checked': len(report.sections),
                },
                'chapters': chapters
            }
            report_data['courses'].append(course_data)
        
        return json.dumps(report_data, indent=2, ensure_ascii=False)
    
    def generate_course_reports(self, report: 'CourseCheckReport') -> tuple[str, str]:
        """
        Generate PDF and JSON reports for a single course.
        Saves files to the run_reports_dir and stores paths in the report object.
        
        Returns:
            Tuple of (pdf_path, json_path)
        """
        course_name, version = self._parse_course_id(report.course_id)
        base_filename = f"link_check_report_{report.course_id}_{self.run_timestamp}"
        
        pdf_file = str(self.run_reports_dir / f"{base_filename}.pdf")
        json_file = str(self.run_reports_dir / f"{base_filename}.json")
        
        # Temporarily save current reports and replace with single report
        original_reports = self.reports
        self.reports = [report]
        
        try:
            # Generate PDF
            if PDF_SUPPORT:
                self._generate_pdf_report(pdf_file)
                report.pdf_file = pdf_file
                print(f"  📄 PDF: {pdf_file}")
            
            # Generate JSON
            json_content = self._generate_json_report()
            with open(json_file, 'w') as f:
                f.write(json_content)
            report.json_file = json_file
            print(f"  📋 JSON: {json_file}")
            
        finally:
            # Restore original reports
            self.reports = original_reports
        
        return pdf_file, json_file
    
    def _generate_pdf_report(self, output_file: str) -> str:
        """
        Generate a comprehensive PDF report with screenshots and hyperlinks.
        
        The PDF includes:
        - Title page with summary
        - Table of broken links with hyperlinks to detailed sections
        - Course chapters and sections with full URLs
        - Embedded screenshots of external links
        """
        from reportlab.platypus import Flowable
        
        # Custom anchor for internal links
        class Anchor(Flowable):
            def __init__(self, name):
                Flowable.__init__(self)
                self.name = name
                self.width = 0
                self.height = 0
            
            def draw(self):
                self.canv.bookmarkPage(self.name)
        
        # Setup document
        doc = SimpleDocTemplate(
            output_file,
            pagesize=A4,
            rightMargin=1.5*cm,
            leftMargin=1.5*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )
        
        # Define styles
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#CC0000')  # Red Hat red
        )
        
        heading1_style = ParagraphStyle(
            'CustomHeading1',
            parent=styles['Heading1'],
            fontSize=18,
            spaceBefore=20,
            spaceAfter=10,
            textColor=colors.HexColor('#CC0000')
        )
        
        heading2_style = ParagraphStyle(
            'CustomHeading2',
            parent=styles['Heading2'],
            fontSize=14,
            spaceBefore=15,
            spaceAfter=8,
            textColor=colors.HexColor('#333333')
        )
        
        heading3_style = ParagraphStyle(
            'CustomHeading3',
            parent=styles['Heading3'],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=5,
            textColor=colors.HexColor('#555555')
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceBefore=3,
            spaceAfter=3
        )
        
        url_style = ParagraphStyle(
            'URLStyle',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#0066CC'),
            spaceBefore=2,
            spaceAfter=2
        )
        
        error_style = ParagraphStyle(
            'ErrorStyle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#CC0000'),
            spaceBefore=2,
            spaceAfter=2
        )
        
        success_style = ParagraphStyle(
            'SuccessStyle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#006600'),
            spaceBefore=2,
            spaceAfter=2
        )
        
        # Build the document content
        story = []
        
        # === TITLE PAGE ===
        story.append(Spacer(1, 2*inch))
        story.append(Paragraph("Link Check Report", title_style))
        story.append(Spacer(1, 0.5*inch))
        story.append(Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ParagraphStyle('DateStyle', parent=normal_style, alignment=TA_CENTER)
        ))
        story.append(Spacer(1, 1*inch))
        
        # Overall summary
        total_links = sum(r.total_links for r in self.reports)
        total_valid = sum(r.valid_links for r in self.reports)
        total_broken = sum(r.broken_links for r in self.reports)
        total_ignored = sum(r.ignored_links for r in self.reports)
        
        summary_data = [
            ['Metric', 'Count'],
            ['Courses Checked', str(len(self.reports))],
            ['Total Links', str(total_links)],
            ['Valid Links', str(total_valid)],
            ['Broken Links', str(total_broken)],
            ['Ignored Links', str(total_ignored)],
        ]
        
        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#CC0000')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F5F5F5')),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#CCCCCC')),
            ('FONTSIZE', (0, 1), (-1, -1), 11),
            # Highlight broken links row
            ('BACKGROUND', (0, 4), (-1, 4), colors.HexColor('#FFEEEE') if total_broken > 0 else colors.HexColor('#F5F5F5')),
            ('TEXTCOLOR', (1, 4), (1, 4), colors.HexColor('#CC0000') if total_broken > 0 else colors.black),
        ]))
        story.append(summary_table)
        
        story.append(PageBreak())
        
        # === BROKEN LINKS SUMMARY (with hyperlinks) ===
        if total_broken > 0:
            story.append(Paragraph("⚠️ Broken Links Summary", heading1_style))
            story.append(Paragraph(
                "The following links returned errors. Click on any link to jump to its detailed section.",
                normal_style
            ))
            story.append(Spacer(1, 0.3*inch))
            
            broken_count = 0
            for report in self.reports:
                broken = [r for r in report.results if not r.is_valid]
                if broken:
                    story.append(Paragraph(f"<b>Course: {report.course_id}</b>", heading2_style))
                    
                    for result in broken:
                        broken_count += 1
                        anchor_name = f"broken_{broken_count}"
                        
                        # Create hyperlink to the detailed section in the PDF
                        link_para = Paragraph(
                            f'<a href="#{anchor_name}" color="blue"><u>{result.link_text}</u></a> '
                            f'<font size="8">(jump to details)</font>',
                            normal_style
                        )
                        story.append(link_para)
                        # Make the URL clickable to test it directly in a browser
                        story.append(Paragraph(
                            f'URL: <a href="{result.url}" color="#0066cc"><u>{result.url}</u></a>',
                            url_style
                        ))
                        story.append(Paragraph(
                            f"Location: {result.chapter} → {result.source_page}",
                            normal_style
                        ))
                        story.append(Paragraph(
                            f"Error: [{result.status_code or 'N/A'}] {result.error_message}",
                            error_style
                        ))
                        story.append(Spacer(1, 0.2*inch))
            
            story.append(PageBreak())
        
        # === DETAILED COURSE REPORTS ===
        broken_counter = 0
        
        for report in self.reports:
            # Course header
            story.append(Paragraph(f"📚 Course: {report.course_id}", heading1_style))
            
            # Course info table
            rol_base_url = "https://rol.redhat.com"
            course_info = [
                ['Property', 'Value'],
                ['Course ID', report.course_id],
                ['Check Started', str(report.check_started.strftime('%Y-%m-%d %H:%M:%S') if report.check_started else 'N/A')],
                ['Check Completed', str(report.check_completed.strftime('%Y-%m-%d %H:%M:%S') if report.check_completed else 'N/A')],
                ['Sections Checked', str(len(report.sections))],
                ['Total Links', str(report.total_links)],
                ['Valid Links', str(report.valid_links)],
                ['Broken Links', str(report.broken_links)],
            ]
            
            info_table = Table(course_info, colWidths=[2.5*inch, 4*inch])
            info_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#333333')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F9F9F9')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#DDDDDD')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(info_table)
            story.append(Spacer(1, 0.3*inch))
            
            # Group sections by chapter
            chapters = {}
            for section in report.sections:
                chapter = section.chapter or "General"
                if chapter not in chapters:
                    chapters[chapter] = []
                chapters[chapter].append(section)
            
            # Output each chapter
            for chapter_name, chapter_sections in chapters.items():
                story.append(Paragraph(f"📖 {chapter_name}", heading2_style))
                
                for section in chapter_sections:
                    section_header = f"[{section.section_number}] " if section.section_number else ""
                    story.append(Paragraph(f"📄 {section_header}{section.title}", heading3_style))
                    
                    # Full ROL URL
                    full_url = f"{rol_base_url}{section.url}" if section.url.startswith('/') else section.url
                    story.append(Paragraph(
                        f'ROL URL: <a href="{full_url}" color="blue">{full_url}</a>',
                        url_style
                    ))
                    
                    # Get results for this section
                    section_results = [r for r in report.results if r.source_page == section.title]
                    
                    if section_results:
                        story.append(Spacer(1, 0.1*inch))
                        
                        # Group by References vs Content
                        references_links = [r for r in section_results if r.source_section == 'References']
                        content_links = [r for r in section_results if r.source_section == 'Content']
                        
                        for link_group, group_name in [(references_links, "📎 References"), (content_links, "📝 Content Links")]:
                            if link_group:
                                story.append(Paragraph(f"<b>{group_name}:</b>", normal_style))
                                
                                for result in link_group:
                                    # Add anchor for broken links
                                    if not result.is_valid:
                                        broken_counter += 1
                                        story.append(Anchor(f"broken_{broken_counter}"))
                                    
                                    status_icon = "✓" if result.is_valid else "✗"
                                    status_code = result.status_code if result.status_code else "ERR"
                                    status_desc = self._get_http_status_description(result.status_code)
                                    
                                    # Link status line
                                    style = success_style if result.is_valid else error_style
                                    story.append(Paragraph(
                                        f"{status_icon} [{status_code}] {result.link_text}",
                                        style
                                    ))
                                    
                                    # Full URL (clickable)
                                    story.append(Paragraph(
                                        f'<a href="{result.url}" color="blue">{result.url}</a>',
                                        url_style
                                    ))
                                    
                                    # Status description
                                    story.append(Paragraph(f"Status: {status_desc}", normal_style))
                                    
                                    # Response time if available
                                    if result.response_time_ms:
                                        story.append(Paragraph(
                                            f"Response Time: {result.response_time_ms:.0f}ms",
                                            normal_style
                                        ))
                                    
                                    # Error message for broken links
                                    if not result.is_valid and result.error_message:
                                        story.append(Paragraph(
                                            f"⚠️ Error: {result.error_message}",
                                            error_style
                                        ))
                                    
                                    # Screenshot
                                    if result.screenshot_path and os.path.exists(result.screenshot_path):
                                        try:
                                            # Get image dimensions and scale appropriately
                                            with PILImage.open(result.screenshot_path) as img:
                                                img_width, img_height = img.size
                                            
                                            # Scale to fit page width (max 6 inches)
                                            max_width = 6 * inch
                                            max_height = 4 * inch
                                            
                                            scale_w = max_width / img_width
                                            scale_h = max_height / img_height
                                            scale = min(scale_w, scale_h, 1.0)
                                            
                                            display_width = img_width * scale
                                            display_height = img_height * scale
                                            
                                            story.append(Spacer(1, 0.1*inch))
                                            story.append(Paragraph("📸 Screenshot:", normal_style))
                                            story.append(Image(
                                                result.screenshot_path,
                                                width=display_width,
                                                height=display_height
                                            ))
                                        except Exception as e:
                                            story.append(Paragraph(
                                                f"Screenshot: {os.path.basename(result.screenshot_path)} (could not embed: {e})",
                                                normal_style
                                            ))
                                    elif result.screenshot_path:
                                        story.append(Paragraph(
                                            f"Screenshot: {result.screenshot_path}",
                                            normal_style
                                        ))
                                    
                                    story.append(Spacer(1, 0.15*inch))
                    else:
                        story.append(Paragraph("(No external links found)", normal_style))
                    
                    story.append(Spacer(1, 0.2*inch))
            
            # Page break between courses
            if report != self.reports[-1]:
                story.append(PageBreak())
        
        # Build PDF
        doc.build(story)
        
        return output_file
