"""
ServiceNow REST API read client for LX Toolbox.

Provides fast-path ticket info and journal entry retrieval, replacing
the Selenium DOM-scraping path when API credentials are configured.
"""

import logging
import requests

from ..utils.config_manager import ConfigManager

FEEDBACK_TABLE = "x_redha_rht_task"

_log = logging.getLogger(__name__)


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
        Fetch journal entries for a ticket by its ``sys_id``.

        Returns a list of dicts, each with keys ``timestamp``, ``author``,
        ``type``, and ``text`` — the same shape as
        ``ServiceNowHandler.get_customer_updates()``.

        Raises:
            ServiceNowAPIError: on auth failure or non-200 HTTP response.
        """
        url = f"{self._base_url}/api/now/table/sys_journal_field"
        # Matches the display labels used by get_customer_updates() on the DOM path.
        RELEVANT_TYPES = {"Additional comments", "Email received", "Email sent"}
        SKIP_AUTHORS = {"api_snow_autoassign", "System"}

        # Filter server-side only by element_id; type/author filtering is done in
        # Python below so it mirrors get_customer_updates() exactly without
        # depending on internal element field names that may vary by instance.
        params = {
            "sysparm_query": f"element_id={sys_id}",
            "sysparm_fields": "sys_created_on,sys_created_by,element,value",
            "sysparm_limit": "100",
            "sysparm_orderby": "sys_created_on",
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

        entries = []
        for raw in response.json().get("result", []):
            author = raw.get("sys_created_by", "")
            if author in SKIP_AUTHORS:
                continue
            entry_type = raw.get("element", "")
            if entry_type not in RELEVANT_TYPES:
                continue
            text = (raw.get("value") or "").strip()
            if not text:
                continue
            entries.append(
                {
                    "timestamp": raw.get("sys_created_on", ""),
                    "author": author,
                    "type": entry_type,
                    "text": text,
                }
            )

        _log.debug(
            "Fetched %d journal entries via REST API (sys_id=%s)",
            len(entries),
            sys_id,
        )
        return entries
