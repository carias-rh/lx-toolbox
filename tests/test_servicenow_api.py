"""Unit tests for ServiceNowAPIClient."""

import pytest
import requests
from unittest.mock import MagicMock, patch, PropertyMock

from lx_toolbox.core.servicenow_api import (
    ServiceNowAPIClient,
    ServiceNowAPIError,
    FEEDBACK_TABLE,
    parse_journal_display_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(instance_url="https://redhat.service-now.com", user="u", password="p"):
    config = MagicMock()
    config.get.side_effect = lambda section, key: {
        ("ServiceNow", "SNOW_INSTANCE_URL"): instance_url,
        ("ServiceNow", "SNOW_API_USER"): user,
        ("ServiceNow", "SNOW_API_PASSWORD"): password,
    }.get((section, key), "")
    return config


def _mock_response(status_code=200, json_body=None, text=""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = (200 <= status_code < 300)
    resp.json.return_value = json_body or {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_raises_when_instance_url_missing(self):
        config = _make_config(instance_url="")
        with pytest.raises(ServiceNowAPIError, match="credentials missing"):
            ServiceNowAPIClient(config)

    def test_raises_when_user_missing(self):
        config = _make_config(user="")
        with pytest.raises(ServiceNowAPIError, match="credentials missing"):
            ServiceNowAPIClient(config)

    def test_raises_when_password_missing(self):
        config = _make_config(password="")
        with pytest.raises(ServiceNowAPIError, match="credentials missing"):
            ServiceNowAPIClient(config)

    def test_success(self):
        config = _make_config()
        client = ServiceNowAPIClient(config)
        assert client._base_url == "https://redhat.service-now.com"

    def test_trailing_slash_stripped(self):
        config = _make_config(instance_url="https://redhat.service-now.com/")
        client = ServiceNowAPIClient(config)
        assert not client._base_url.endswith("/")


# ---------------------------------------------------------------------------
# get_ticket
# ---------------------------------------------------------------------------

TICKET_RECORD = {
    "sys_id": "abc123",
    "number": "FEEDBACK0001234",
    "description": "Course:  DO180\nVersion:  4.18",
    "contact_source": "Jane Doe",
}


class TestGetTicket:
    def _client(self):
        return ServiceNowAPIClient(_make_config())

    def test_returns_dict_with_expected_keys(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(json_body={"result": [TICKET_RECORD]})
        )
        result = client.get_ticket("FEEDBACK0001234")
        for key in ("sys_id", "number", "description", "contact_source"):
            assert key in result

    def test_returns_correct_values(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(json_body={"result": [TICKET_RECORD]})
        )
        result = client.get_ticket("FEEDBACK0001234")
        assert result["number"] == "FEEDBACK0001234"
        assert result["sys_id"] == "abc123"
        assert result["contact_source"] == "Jane Doe"

    def test_uses_feedback_table_constant(self):
        client = self._client()
        mock_get = MagicMock(
            return_value=_mock_response(json_body={"result": [TICKET_RECORD]})
        )
        client._session.get = mock_get
        client.get_ticket("FEEDBACK0001234")
        called_url = mock_get.call_args[0][0]
        assert FEEDBACK_TABLE in called_url

    def test_401_raises_auth_error(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(status_code=401)
        )
        with pytest.raises(ServiceNowAPIError, match="401"):
            client.get_ticket("FEEDBACK0001234")

    def test_non_200_raises_error(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(status_code=500, text="Internal Server Error")
        )
        with pytest.raises(ServiceNowAPIError, match="500"):
            client.get_ticket("FEEDBACK0001234")

    def test_ticket_not_found_raises_error(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(json_body={"result": []})
        )
        with pytest.raises(ServiceNowAPIError, match="not found"):
            client.get_ticket("FEEDBACK9999999")

    def test_network_error_raises(self):
        client = self._client()
        client._session.get = MagicMock(
            side_effect=requests.RequestException("connection refused")
        )
        with pytest.raises(ServiceNowAPIError, match="Request failed"):
            client.get_ticket("FEEDBACK0001234")


# ---------------------------------------------------------------------------
# get_journal_entries — comments display_value (not sys_journal_field)
# ---------------------------------------------------------------------------

# Shape matches hub.redhat.com: GET x_redha_rht_task/{sys_id}
# with sysparm_display_value=true. sys_journal_field is empty for this API user.
COMMENTS_DISPLAY_VALUE = """\
2026-08-24 02:14:49 - Guest (Additional comments)
Hi,

reloading the browser page, seemed to do the trick.

2026-08-22 17:48:13 - Carlos Arias (Additional comments)
Dear Nils, we confirmed the rendering issue.

2026-08-22 11:06:41 - api_snow_autoassign (Additional comments)
Thanks for submitting your feedback.

2026-08-22 11:06:38 - System (Additional comments)
Received from: rht-earlyaccess@redhat.com
Original ticket description.

2026-08-22 10:00:00 - Engineer (Work notes)
Internal note only.

2026-08-22 09:00:00 - Guest (Additional comments)
   
"""


def _comments_response(comments: str):
    return _mock_response(json_body={"result": {"comments": comments}})


class TestParseJournalDisplayValue:
    def test_empty_string(self):
        assert parse_journal_display_value("") == []
        assert parse_journal_display_value("   ") == []

    def test_splits_entries_on_headers(self):
        entries = parse_journal_display_value(COMMENTS_DISPLAY_VALUE)
        authors = [e["author"] for e in entries]
        assert authors[0] == "Guest"
        assert "Carlos Arias" in authors
        assert "api_snow_autoassign" in authors
        assert "System" in authors


class TestGetJournalEntries:
    def _client(self):
        return ServiceNowAPIClient(_make_config())

    def test_returns_list_of_dicts(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        assert isinstance(result, list)

    def test_each_entry_has_required_keys(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        for entry in result:
            for key in ("timestamp", "author", "type", "text"):
                assert key in entry, f"Missing key '{key}' in entry: {entry}"

    def test_keeps_learner_follow_up_and_agent_reply(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        texts = [e["text"] for e in result]
        assert any("reloading the browser page" in t for t in texts)
        assert any("we confirmed the rendering issue" in t for t in texts)

    def test_filters_out_autoassign_author(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        authors = [e["author"] for e in result]
        assert "api_snow_autoassign" not in authors

    def test_filters_out_system_author(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        authors = [e["author"] for e in client.get_journal_entries("abc123")]
        assert "System" not in authors

    def test_filters_out_irrelevant_element_types(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        types = {e["type"] for e in result}
        assert "Work notes" not in types

    def test_filters_out_empty_text(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        for entry in result:
            assert entry["text"].strip(), f"Empty text in entry: {entry}"

    def test_returns_only_relevant_entries(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response(COMMENTS_DISPLAY_VALUE)
        )
        result = client.get_journal_entries("abc123")
        # Guest follow-up + Carlos reply; System, autoassign, work notes, empty dropped
        assert len(result) == 2

    def test_reads_comments_display_value_from_ticket_record(self):
        client = self._client()
        mock_get = MagicMock(return_value=_comments_response(COMMENTS_DISPLAY_VALUE))
        client._session.get = mock_get
        client.get_journal_entries("abc123")
        called_url = mock_get.call_args[0][0]
        params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params")
        assert FEEDBACK_TABLE in called_url
        assert "abc123" in called_url
        assert "sys_journal_field" not in called_url
        assert params["sysparm_display_value"] == "true"
        assert params["sysparm_fields"] == "comments"

    def test_empty_result(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_comments_response("")
        )
        result = client.get_journal_entries("abc123")
        assert result == []

    def test_401_raises_auth_error(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(status_code=401)
        )
        with pytest.raises(ServiceNowAPIError, match="401"):
            client.get_journal_entries("abc123")

    def test_non_200_raises_error(self):
        client = self._client()
        client._session.get = MagicMock(
            return_value=_mock_response(status_code=403, text="Forbidden")
        )
        with pytest.raises(ServiceNowAPIError, match="403"):
            client.get_journal_entries("abc123")

    def test_network_error_raises(self):
        client = self._client()
        client._session.get = MagicMock(
            side_effect=requests.RequestException("timeout")
        )
        with pytest.raises(ServiceNowAPIError, match="Request failed"):
            client.get_journal_entries("abc123")
