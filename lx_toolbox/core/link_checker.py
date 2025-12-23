"""
Link Checker module for Red Hat Learning (ROL) courses.

This module provides functionality to:
1. Navigate through the ROL catalog and courses
2. Extract links from course content, especially the References sections
3. Validate those links and generate reports
"""

import time
import logging
import requests
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
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
    status_code: Optional[int] = None
    is_valid: bool = True
    error_message: Optional[str] = None
    response_time_ms: Optional[float] = None


@dataclass
class CourseCheckReport:
    """Report for all link checks in a course."""
    course_id: str
    total_links: int = 0
    valid_links: int = 0
    broken_links: int = 0
    ignored_links: int = 0
    check_started: Optional[datetime] = None
    check_completed: Optional[datetime] = None
    results: list[LinkCheckResult] = field(default_factory=list)
    sections_checked: list[str] = field(default_factory=list)


class LinkChecker(LabManager):
    """
    Link checker for ROL courses.
    
    Extends LabManager to add functionality for:
    - Navigating through course catalog
    - Extracting links from course content
    - Validating links and generating reports
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
    
    def __init__(self, config: ConfigManager, browser_name: str = None, is_headless: bool = None):
        super().__init__(config, browser_name, is_headless)
        self.reports: list[CourseCheckReport] = []
        self.session = requests.Session()
        # Set a reasonable user agent for HTTP requests
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0'
        })
    
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
    
    def _validate_link(self, url: str, source_page: str, source_section: str, link_text: str) -> LinkCheckResult:
        """Validate a single link by making an HTTP HEAD request."""
        result = LinkCheckResult(
            url=url,
            source_page=source_page,
            source_section=source_section,
            link_text=link_text
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
                result.error_message = f"HTTP {response.status_code}"
                
        except requests.exceptions.Timeout:
            result.is_valid = False
            result.error_message = "Request timed out"
        except requests.exceptions.ConnectionError as e:
            result.is_valid = False
            result.error_message = f"Connection error: {str(e)[:100]}"
        except requests.exceptions.RequestException as e:
            result.is_valid = False
            result.error_message = f"Request error: {str(e)[:100]}"
        except Exception as e:
            result.is_valid = False
            result.error_message = f"Unexpected error: {str(e)[:100]}"
        
        return result
    
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
    
    def get_course_sections(self, course_id: str, environment: str) -> list[dict]:
        """
        Get all sections from a course's table of contents.
        Returns a list of dicts with 'title' and 'url'.
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
            # Click on "Toggle Table of Contents panel" button to open TOC
            toc_button = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(@aria-label, "Table of Contents") or contains(@aria-label, "Toggle Table of Contents")]')
            ))
            
            # Check if TOC is already open by looking for the TOC region
            try:
                toc_region = self.driver.find_element(By.XPATH, '//region[@aria-label="Table of contents"] | //div[contains(@class, "toc")]//nav')
                if not toc_region.is_displayed():
                    toc_button.click()
                    time.sleep(0.5)
            except NoSuchElementException:
                toc_button.click()
                time.sleep(0.5)
            
            # Expand all chapters to see all sections
            try:
                expand_all = self.driver.find_element(
                    By.XPATH, 
                    '//input[@type="checkbox" and following-sibling::*[contains(text(), "Expand all")]] | //button[contains(text(), "Expand all")]'
                )
                if expand_all.is_displayed():
                    expand_all.click()
                    time.sleep(0.5)
            except NoSuchElementException:
                # Expand all not available, try to expand each chapter manually
                try:
                    chapter_buttons = self.driver.find_elements(
                        By.XPATH, 
                        '//button[contains(@aria-expanded, "false") and contains(@class, "chapter")]'
                    )
                    for btn in chapter_buttons:
                        try:
                            btn.click()
                            time.sleep(0.2)
                        except:
                            continue
                except:
                    pass
            
            # Get all section links from TOC
            section_links = self.driver.find_elements(
                By.XPATH,
                '//a[contains(@href, "/pages/")]'
            )
            
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
                        
                        # Avoid duplicates
                        if not any(s['url'] == path for s in sections):
                            sections.append({
                                'title': title,
                                'url': path
                            })
                except Exception as e:
                    logging.debug(f"Error processing section link: {e}")
                    continue
            
            self.logger(f"Found {len(sections)} sections in course {course_id}")
            
        except TimeoutException:
            self.logger(f"Timeout waiting for TOC in course {course_id}")
        except Exception as e:
            self.logger(f"Error getting course sections: {e}")
        
        return sections
    
    def extract_links_from_page(self, page_url: str, page_title: str) -> list[dict]:
        """
        Extract all external links from a course page, especially from the References section.
        Returns a list of dicts with 'url', 'text', and 'section'.
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
    
    def check_course_links(self, course_id: str, environment: str) -> CourseCheckReport:
        """
        Check all links in a course.
        Returns a CourseCheckReport with results.
        """
        self.logger(f"Checking links for course: {course_id}")
        
        report = CourseCheckReport(
            course_id=course_id,
            check_started=datetime.now()
        )
        
        try:
            # Get all sections in the course
            sections = self.get_course_sections(course_id, environment)
            
            for section in sections:
                section_title = section['title']
                section_url = section['url']
                
                self.logger(f"  Checking section: {section_title}")
                report.sections_checked.append(section_title)
                
                # Extract links from this section
                links = self.extract_links_from_page(section_url, section_title)
                
                for link_info in links:
                    url = link_info['url']
                    
                    if self._should_ignore_url(url):
                        report.ignored_links += 1
                        continue
                    
                    report.total_links += 1
                    
                    # Validate the link
                    result = self._validate_link(
                        url=url,
                        source_page=section_title,
                        source_section=link_info['section'],
                        link_text=link_info['text']
                    )
                    
                    report.results.append(result)
                    
                    if result.is_valid:
                        report.valid_links += 1
                        self.logger(f"    ✓ {url[:60]}...")
                    else:
                        report.broken_links += 1
                        self.logger(f"    ✗ {url[:60]}... ({result.error_message})")
            
        except Exception as e:
            self.logger(f"Error checking course {course_id}: {e}")
        
        report.check_completed = datetime.now()
        self.reports.append(report)
        
        return report
    
    def check_all_courses(self, environment: str, limit: int = None) -> list[CourseCheckReport]:
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
            self.logger(f"\n[{i+1}/{len(courses)}] Checking course: {course['title']} ({course['id']})")
            
            try:
                report = self.check_course_links(course['id'], environment)
                reports.append(report)
            except Exception as e:
                self.logger(f"Failed to check course {course['id']}: {e}")
                continue
        
        return reports
    
    def generate_report(self, output_format: str = 'text') -> str:
        """
        Generate a summary report of all link checks.
        Supports 'text' or 'json' format.
        """
        if output_format == 'json':
            return self._generate_json_report()
        else:
            return self._generate_text_report()
    
    def _generate_text_report(self) -> str:
        """Generate a human-readable text report."""
        lines = []
        lines.append("=" * 80)
        lines.append("LINK CHECK REPORT")
        lines.append("=" * 80)
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
        
        lines.append(f"SUMMARY:")
        lines.append(f"  Courses checked: {len(self.reports)}")
        lines.append(f"  Total links: {total_links}")
        lines.append(f"  Valid links: {total_valid}")
        lines.append(f"  Broken links: {total_broken}")
        lines.append(f"  Ignored links: {total_ignored}")
        lines.append("")
        
        # Detailed broken links
        if total_broken > 0:
            lines.append("-" * 80)
            lines.append("BROKEN LINKS:")
            lines.append("-" * 80)
            
            for report in self.reports:
                broken = [r for r in report.results if not r.is_valid]
                if broken:
                    lines.append(f"\nCourse: {report.course_id}")
                    for result in broken:
                        lines.append(f"  Page: {result.source_page}")
                        lines.append(f"  Section: {result.source_section}")
                        lines.append(f"  Link: {result.link_text}")
                        lines.append(f"  URL: {result.url}")
                        lines.append(f"  Error: {result.error_message}")
                        lines.append("")
        
        lines.append("=" * 80)
        lines.append("END OF REPORT")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def _generate_json_report(self) -> str:
        """Generate a JSON report."""
        report_data = {
            'generated': datetime.now().isoformat(),
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
            course_data = {
                'course_id': report.course_id,
                'check_started': report.check_started.isoformat() if report.check_started else None,
                'check_completed': report.check_completed.isoformat() if report.check_completed else None,
                'total_links': report.total_links,
                'valid_links': report.valid_links,
                'broken_links': report.broken_links,
                'ignored_links': report.ignored_links,
                'sections_checked': report.sections_checked,
                'results': [
                    {
                        'url': r.url,
                        'source_page': r.source_page,
                        'source_section': r.source_section,
                        'link_text': r.link_text,
                        'status_code': r.status_code,
                        'is_valid': r.is_valid,
                        'error_message': r.error_message,
                        'response_time_ms': r.response_time_ms,
                    }
                    for r in report.results
                ]
            }
            report_data['courses'].append(course_data)
        
        return json.dumps(report_data, indent=2)
