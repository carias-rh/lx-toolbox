"""
ServiceNow REST API read client for LX Toolbox.

Provides fast-path ticket info and journal entry retrieval, replacing
the Selenium DOM-scraping path when API credentials are configured.
"""

import logging
import re

import requests

from ..utils.config_manager import ConfigManager

FEEDBACK_TABLE = "x_redha_rht_task"

# ServiceNow journal display_value headers: "YYYY-MM-DD HH:MM:SS - Author (Type)"
_JOURNAL_HEADER = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (.+?) \(([^)]+)\)\s*$"
)

# Matches the display labels used by get_customer_updates() on the DOM path.
# The comments field only stores Additional comments; Email received/sent are
# copied into that journal as Additional comments on this instance.
_RELEVANT_JOURNAL_TYPES = {"Additional comments", "Email received", "Email sent"}
_SKIP_JOURNAL_AUTHORS = {"api_snow_autoassign", "System"}

_log = logging.getLogger(__name__)


def parse_journal_display_value(comments: str) -> list[dict]:
    """Split a ServiceNow journal ``display_value`` string into entry dicts.

    Each entry has ``timestamp``, ``author``, ``type``, and ``text``.
    """
    if not comments or not str(comments).strip():
        return []

    entries: list[dict] = []
    current: dict | None = None
    body_lines: list[str] = []

    def _flush():
        if current is None:
            return
        current["text"] = "\n".join(body_lines).strip()
        entries.append(current)

    for line in str(comments).splitlines():
        match = _JOURNAL_HEADER.match(line)
        if match:
            _flush()
            current = {
                "timestamp": match.group(1),
                "author": match.group(2).strip(),
                "type": match.group(3).strip(),
                "text": "",
            }
            body_lines = []
        elif current is not None:
            body_lines.append(line)

    _flush()
    return entries


class ServiceNowAPIError(Exception):
    """Raised when the ServiceNow API returns an error or credentials are missing."""


class ServiceNowAPIClient:
    """
    Read-only REST client for ServiceNow Feedback ticket operations.

    Uses HTTP Basic Auth with the same credential keys as
    ``ServiceNowAutoAssign``: ``SNOW_INSTANCE_URL``, ``SNOW_API_USER``,
    and ``SNOW_API_PASSWORD`` sourced from ``ConfigManager``.
    """

    def __init__(self, config: ConfigManager):
        instance_url = config.get("ServiceNow", "SNOW_INSTANCE_URL")
        username = config.get("ServiceNow", "SNOW_API_USER")
        password = config.get("ServiceNow", "SNOW_API_PASSWORD")

        if not all([instance_url, username, password]):
            raise ServiceNowAPIError(
                "ServiceNow API credentials missing. "
                "Set SNOW_INSTANCE_URL, SNOW_API_USER, and SNOW_API_PASSWORD."
            )

        self._base_url = instance_url.rstrip("/")
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )

    def get_ticket(self, ticket_number: str) -> dict:
        """
        Fetch a Feedback ticket record by number.

        Returns a dict with at minimum: ``description``, ``contact_source``,
        ``sys_id``, and ``number``.

        Raises:
            ServiceNowAPIError: on auth failure, ticket not found, or any
                non-200 HTTP response.
        """
        url = f"{self._base_url}/api/now/table/{FEEDBACK_TABLE}"
        params = {
            "sysparm_query": f"number={ticket_number}",
            "sysparm_fields": "sys_id,number,description,contact_source",
            "sysparm_limit": "1",
        }

        try:
            response = self._session.get(url, params=params)
        except requests.RequestException as exc:
            raise ServiceNowAPIError(f"Request failed: {exc}") from exc

        if response.status_code == 401:
            raise ServiceNowAPIError(
                "ServiceNow API authentication failed (HTTP 401). "
                "Check SNOW_API_USER and SNOW_API_PASSWORD."
            )
        if not response.ok:
            raise ServiceNowAPIError(
                f"ServiceNow API returned HTTP {response.status_code} "
                f"for ticket {ticket_number}: {response.text[:200]}"
            )

        results = response.json().get("result", [])
        if not results:
            raise ServiceNowAPIError(
                f"Ticket {ticket_number} not found in {FEEDBACK_TABLE}."
            )

        record = results[0]
        _log.debug("Fetched ticket %s via REST API (sys_id=%s)", ticket_number, record.get("sys_id"))
        return record

    def get_journal_entries(self, sys_id: str) -> list[dict]:
        """
        Fetch Learner Follow-up journal entries for a ticket by its ``sys_id``.

        Reads the ticket ``comments`` field with ``sysparm_display_value=true``.
        ``sys_journal_field`` is not used: this API account receives an empty
        result for that table even when Additional comments exist.

        Returns a list of dicts, each with keys ``timestamp``, ``author``,
        ``type``, and ``text`` — the same shape as
        ``ServiceNowHandler.get_customer_updates()``.

        Raises:
            ServiceNowAPIError: on auth failure or non-200 HTTP response.
        """
        url = f"{self._base_url}/api/now/table/{FEEDBACK_TABLE}/{sys_id}"
        # Raw journal values are empty; only the display_value contains the
        # concatenated Additional comments history.
        params = {
            "sysparm_fields": "comments",
            "sysparm_display_value": "true",
        }

        try:
            response = self._session.get(url, params=params)
        except requests.RequestException as exc:
            raise ServiceNowAPIError(f"Request failed: {exc}") from exc

        if response.status_code == 401:
            raise ServiceNowAPIError(
                "ServiceNow API authentication failed (HTTP 401) "
                "while fetching journal entries."
            )
        if not response.ok:
            raise ServiceNowAPIError(
                f"ServiceNow API returned HTTP {response.status_code} "
                f"for journal entries (sys_id={sys_id}): {response.text[:200]}"
            )

        record = response.json().get("result") or {}
        comments = record.get("comments", "") if isinstance(record, dict) else ""
        entries = []
        for raw in parse_journal_display_value(comments):
            author = raw.get("author", "")
            if author in _SKIP_JOURNAL_AUTHORS:
                continue
            if raw.get("type") not in _RELEVANT_JOURNAL_TYPES:
                continue
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            entries.append(raw)

        _log.debug(
            "Fetched %d journal entries via REST API (sys_id=%s)",
            len(entries),
            sys_id,
        )
        return entries
