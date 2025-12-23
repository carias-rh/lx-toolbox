"""
Link Checker module for Red Hat Learning (ROL) courses.

This module provides functionality to:
1. Navigate through the ROL catalog and courses
2. Extract links from course content, especially the References sections
3. Validate those links and generate reports
4. Take screenshots of visited pages for human review
"""

import os
import re
import time
import logging
import requests
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from pathlib import Path
import json

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from .lab_manager import LabManager
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
    
    def __init__(self, config: ConfigManager, browser_name: str = None, is_headless: bool = None,
                 screenshots_dir: str = None):
        super().__init__(config, browser_name, is_headless)
        self.reports: list[CourseCheckReport] = []
        self.session = requests.Session()
        # Set a reasonable user agent for HTTP requests
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0'
        })
        
        # Setup screenshots directory
        if screenshots_dir:
            self.screenshots_base_dir = Path(screenshots_dir)
        else:
            self.screenshots_base_dir = Path.cwd() / "link_checker_screenshots"
        
        self.screenshots_base_dir.mkdir(parents=True, exist_ok=True)
        self.current_screenshots_dir: Optional[Path] = None
    
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
        
        # Take screenshot of the external link page if requested
        if take_screenshot and result.is_valid:
            result.screenshot_path = self._screenshot_external_link(url, link_text, source_page)
        
        return result
    
    def _screenshot_external_link(self, url: str, link_text: str, source_page: str) -> Optional[str]:
        """
        Navigate to an external link and take a screenshot.
        Returns the path to the screenshot file.
        """
        try:
            self.logger(f"      📸 Visiting external link for screenshot...")
            
            # Store current URL to return later
            original_url = self.driver.current_url
            
            # Navigate to the external link
            self.driver.get(url)
            time.sleep(3)  # Wait for page to load
            
            # Create a descriptive filename
            # Use section and link text for organization
            section_safe = self._sanitize_filename(source_page)
            link_safe = self._sanitize_filename(link_text)[:50]
            
            # Create subdirectory for the section
            section_dir = self.current_screenshots_dir / section_safe
            section_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{timestamp}_{link_safe}.png"
            filepath = section_dir / filename
            
            # Take the screenshot
            self.driver.save_screenshot(str(filepath))
            self.logger(f"      📸 Screenshot saved: {section_safe}/{filename}")
            
            # Navigate back to ROL (we'll navigate to the next section anyway)
            # Don't navigate back - let the course navigation handle it
            
            return str(filepath)
            
        except Exception as e:
            self.logger(f"      ⚠ Failed to screenshot external link: {e}")
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
                (By.XPATH, '//div[text()="Course"]/preceding-sibling::input[@type="checkbox"] | //div[text()="Course"]/..//input[@type="checkbox"]')
            ))
            
            # Check if already checked
            if not course_checkbox.is_selected():
                course_checkbox.click()
                time.sleep(1)  # Wait for filter to apply
            
            self.logger("Filter applied: Showing courses only")
            
        except TimeoutException:
            self.logger("Could not apply course filter. Proceeding with current view.")
    
    def get_all_courses(self) -> list[dict]:
        """
        Get all courses from the catalog.
        Returns a list of dicts with 'id', 'title', and 'url'.
        """
        self.logger("Getting list of all courses from catalog...")
        courses = []
        
        try:
            # Wait for course cards to load
            self.wait.until(EC.presence_of_element_located(
                (By.XPATH, '//a[contains(@href, "/rol/app/courses/")]')
            ))
            
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
            
            self.logger(f"Found {len(courses)} courses in catalog")
            
        except TimeoutException:
            self.logger("Timeout waiting for course list. Catalog might be empty or slow to load.")
        except Exception as e:
            self.logger(f"Error getting courses: {e}")
        
        return courses
    
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
    
    def check_course_links(self, course_id: str, environment: str, 
                           take_screenshots: bool = True) -> CourseCheckReport:
        """
        Check all links in a course.
        Returns a CourseCheckReport with results.
        """
        self.logger(f"Checking links for course: {course_id}")
        
        # Setup screenshots directory for this course
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_screenshots_dir = self.screenshots_base_dir / f"{course_id}_{timestamp}"
        self.current_screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        report = CourseCheckReport(
            course_id=course_id,
            check_started=datetime.now(),
            screenshots_dir=str(self.current_screenshots_dir)
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
                
                for link_info in links:
                    url = link_info['url']
                    
                    if self._should_ignore_url(url):
                        report.ignored_links += 1
                        continue
                    
                    report.total_links += 1
                    
                    # Validate the link and optionally screenshot the external page
                    result = self._validate_link(
                        url=url,
                        source_page=section_title,
                        source_section=link_info['section'],
                        link_text=link_info['text'],
                        chapter=chapter,
                        section_number=section_number,
                        take_screenshot=take_screenshots
                    )
                    
                    report.results.append(result)
                    
                    status_str = f"[{result.status_code or 'ERR'}]"
                    if result.is_valid:
                        report.valid_links += 1
                        self.logger(f"      ✓ {status_str} {link_info['text'][:50]}")
                    else:
                        report.broken_links += 1
                        self.logger(f"      ✗ {status_str} {link_info['text'][:50]} - {result.error_message}")
            
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
        self.logger("Starting link check for all courses...")
        
        # Go to catalog and filter by courses
        self.go_to_catalog(environment)
        self.filter_by_courses()
        
        # Get list of all courses
        courses = self.get_all_courses()
        
        if limit:
            courses = courses[:limit]
        
        self.logger(f"Will check {len(courses)} courses")
        
        reports = []
        for i, course in enumerate(courses):
            self.logger(f"\n{'='*60}")
            self.logger(f"[{i+1}/{len(courses)}] Checking course: {course['title']} ({course['id']})")
            self.logger(f"{'='*60}")
            
            try:
                report = self.check_course_links(course['id'], environment, take_screenshots)
                reports.append(report)
            except Exception as e:
                self.logger(f"Failed to check course {course['id']}: {e}")
                continue
        
        return reports
    
    def generate_report(self, output_format: str = 'text') -> str:
        """
        Generate a summary report of all link checks.
        Supports 'text', 'json', or 'detailed' format.
        """
        if output_format == 'json':
            return self._generate_json_report()
        elif output_format == 'detailed':
            return self._generate_detailed_text_report()
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
                    lines.append(f"     URL: {section.url}")
                    
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
            
            # Course summary
            lines.append("")
            lines.append("COURSE SUMMARY")
            lines.append("-" * 50)
            lines.append(f"  Sections checked: {len(report.sections)}")
            lines.append(f"  Total links:      {report.total_links}")
            lines.append(f"  Valid links:      {report.valid_links}")
            lines.append(f"  Broken links:     {report.broken_links}")
            lines.append(f"  Ignored links:    {report.ignored_links}")
        
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
