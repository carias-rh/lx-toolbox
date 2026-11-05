"""Link checker: container retries, browser-page classification, Cloud Jira URLs."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from lx_toolbox.core.link_checker import (
    CourseCheckReport,
    LinkCheckResult,
    LinkChecker,
    SectionInfo,
    build_linkchecker_cmd,
    classify_loaded_page,
    parse_linkchecker_output,
)


UID_ERROR = (
    "Error: potentially insufficient UIDs or GIDs available in user namespace "
    "(requested 4200765:4200765 for /home/carias/.local/share/containers/storage/volumes/abc)"
)

SUCCESS_OUTPUT = """
LinkChecker 10.6.0
That's it. 1 link in 1 URL checked. 0 warnings found. 0 errors found.
"""

VALID_RESULT_OUTPUT = """
URL        `https://docs.fedoraproject.org/en-US/epel'
Result     Valid: 200 OK
"""

ERROR_404_OUTPUT = """
URL        `https://example.invalid/missing'
Result     Error: 404 Not Found
"""


def test_linkchecker_cmd_does_not_pass_host_uid():
    """Rootless podman cannot map LDAP UIDs like 4200765 into the user namespace."""
    cmd = build_linkchecker_cmd("podman", "https://example.com")
    assert cmd[0] == "podman"
    assert "-u" not in cmd
    assert "https://example.com" in cmd


def test_parse_linkchecker_uid_error_is_infrastructure_not_a_broken_link():
    parsed = parse_linkchecker_output(UID_ERROR)
    assert parsed.is_infrastructure_error is True
    assert parsed.is_valid is False


def test_parse_linkchecker_success_zero_errors():
    parsed = parse_linkchecker_output(SUCCESS_OUTPUT)
    assert parsed.is_valid is True
    assert parsed.is_infrastructure_error is False
    assert parsed.status_code == 200


def test_parse_linkchecker_valid_result_line():
    parsed = parse_linkchecker_output(VALID_RESULT_OUTPUT)
    assert parsed.is_valid is True
    assert parsed.status_code == 200


def test_parse_linkchecker_404():
    parsed = parse_linkchecker_output(ERROR_404_OUTPUT)
    assert parsed.is_valid is False
    assert parsed.status_code == 404
    assert parsed.is_infrastructure_error is False


def test_classify_loaded_docs_page_with_http_200_is_valid():
    status, valid, error = classify_loaded_page(
        title="Creating and managing manifests for a connected Satellite Server",
        current_url="https://docs.redhat.com/en/documentation/subscription_central/1-latest/html-single/creating_and_managing_manifests_for_a_connected_satellite_server/index",
        page_source="<html><body>We use cookies on this site. Red Hat Documentation</body></html>",
        nav_status=200,
    )
    assert valid is True
    assert status == 200
    assert error is None


def test_classify_loaded_page_without_nav_status_trusts_real_docs_title():
    """docs.redhat.com WAF 403s Python requests; a loaded docs page is still valid."""
    status, valid, error = classify_loaded_page(
        title="Managing content | Red Hat Satellite | 6.18",
        current_url="https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html-single/managing_content/index",
        page_source="<html><head></head><body><h1>Managing content</h1></body></html>",
        nav_status=None,
    )
    assert valid is True
    assert status == 200
    assert error is None


def test_classify_loaded_page_detects_403_error_page():
    status, valid, error = classify_loaded_page(
        title="403 Forbidden",
        current_url="https://docs.redhat.com/denied",
        page_source="<html><body>Access Denied</body></html>",
        nav_status=403,
    )
    assert valid is False
    assert status == 403
    assert error


def test_broken_links_search_url_uses_atlassian_cloud():
    checker = LinkChecker.__new__(LinkChecker)
    url = checker._build_broken_links_search_url(
        "rh403-6.18",
        ["https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html-single/managing_content/index"],
    )
    assert "redhat.atlassian.net" in url
    assert "issues.redhat.com" not in url


def test_broken_links_description_is_html_table_not_wiki():
    checker = LinkChecker.__new__(LinkChecker)
    checker.config = MagicMock()
    checker.config.get_lab_base_url.return_value = (
        "https://rol.redhat.com/rol/app/courses/"
    )
    report = CourseCheckReport(
        course_id="rh403-6.18",
        check_completed=datetime(2026, 11, 3, 17, 41, 11),
        pdf_file="/tmp/link_check_report_rh403-6.18.pdf",
        sections=[
            SectionInfo(
                title="Section 1.3: Planning",
                url="/rol/app/courses/rh403-6.18/pages/ch01s03",
            )
        ],
    )
    broken = [
        LinkCheckResult(
            url="https://docs.example.com/x?a=1&b=2",
            source_page="Section 1.3: Planning",
            source_section="References",
            link_text="Managing content",
            chapter="01",
            section_number="1.3",
            status_code=404,
            is_valid=False,
            error_message="Not Found",
        )
    ]
    html = checker._broken_links_description_html(
        report,
        broken,
        "rh403",
        "6.18",
        "https://redhat.atlassian.net/issues/?jql=project%20%3D%20PTL",
        report.pdf_file,
        None,
    )
    assert "<ul>" in html
    assert "<li>" in html
    assert "||Chapter||" not in html
    assert "[Managing content|" not in html
    assert "RH403" in html
    assert 'href="https://docs.example.com/x?a=1&amp;b=2"' in html
    assert 'href="https://rol.redhat.com/rol/app/courses/rh403-6.18/pages/ch01s03"' in html
    assert "Search JQL" in html
    assert "link_check_report_rh403-6.18.pdf" in html


def test_retry_keeps_original_result_when_container_hits_uid_error():
    checker = LinkChecker.__new__(LinkChecker)
    checker.logger = lambda *args, **kwargs: None
    original = LinkCheckResult(
        url="https://docs.redhat.com/en/documentation/red_hat_satellite/6.18/html-single/managing_content/index",
        source_page="Section 1.3: Planning",
        source_section="References",
        link_text="Managing content",
        status_code=403,
        is_valid=False,
        error_message="Forbidden",
    )
    report = CourseCheckReport(
        course_id="rh403-6.18",
        total_links=1,
        broken_links=1,
        results=[original],
    )
    checker.reports = [report]
    infra = LinkCheckResult(
        url=original.url,
        source_page=original.source_page,
        source_section=original.source_section,
        link_text=original.link_text,
        is_valid=False,
        error_message=UID_ERROR,
        is_infrastructure_error=True,
    )

    with patch.object(checker, "_get_container_runtime", return_value="podman"), \
         patch.object(checker, "_linkchecker_is_usable", return_value=True), \
         patch.object(checker, "_validate_link_with_linkchecker", return_value=infra):
        checker.retry_failed_links()

    assert original.status_code == 403
    assert original.error_message == "Forbidden"
    assert original.is_valid is False
    assert report.broken_links == 1
