"""Auto-Assign peek/commit HTTP contract (issues #32, #33)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from lx_toolbox.core.servicenow_autoassign import ServiceNowAutoAssign, TeamConfig


def _config():
    config = MagicMock()

    def get(section, key):
        return {
            ("ServiceNow", "SNOW_INSTANCE_URL"): "https://example.service-now.com",
            ("ServiceNow", "SNOW_API_USER"): "user",
            ("ServiceNow", "SNOW_API_PASSWORD"): "pass",
            ("T1", "FRONTEND_OPENSHIFT_ROUTE"): "http://t1-frontend",
            ("T1", "ASSIGNMENT_GROUP_ID"): "t1-ag",
            ("T2", "FRONTEND_OPENSHIFT_ROUTE"): "http://t2-frontend",
            ("T2", "ASSIGNMENT_GROUP_ID"): "t2-ag",
        }.get((section, key), "")

    config.get.side_effect = get
    return config


@pytest.fixture
def assigner():
    return ServiceNowAutoAssign(_config())


def _ticket(number="RHT0001"):
    return {"sys_id": "sys1", "number": number, "description": "", "short_description": ""}


def test_t2_empty_queue_does_not_call_shift_frontend(assigner):
    with patch.object(assigner, "auto_resolve_tickets_by_reporter", return_value=0), \
         patch.object(assigner, "get_unassigned_tickets", return_value=[]), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get") as mock_get, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post") as mock_post:
        assigner.run_auto_assignment("t2")
    mock_get.assert_not_called()
    mock_post.assert_not_called()


def _shift_response(name, round_robin=True):
    resp = MagicMock(spec=requests.Response)
    resp.ok = True
    resp.json.return_value = {"name": name, "on_shift": True, "round_robin": round_robin}
    return resp


def test_t2_peek_assign_commit_name(assigner):
    peek = _shift_response("Samik Sanyal")
    commit = _shift_response("Shashi Singh")
    with patch.object(assigner, "auto_resolve_tickets_by_reporter", return_value=0), \
         patch.object(assigner, "get_unassigned_tickets", return_value=[_ticket()]), \
         patch.object(assigner, "process_t2_ticket", return_value=True) as process, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek) as mock_get, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post", return_value=commit) as mock_post:
        stats = assigner.run_auto_assignment("t2")
    assert stats["assigned"] == 1
    process.assert_called_once()
    assert process.call_args[0][2] == "Samik Sanyal"
    assert mock_get.call_args[0][0] == "http://t2-frontend/api/shift"
    assert "advance" not in mock_get.call_args[1].get("params", {})
    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == "http://t2-frontend/api/shift/commit"
    assert mock_post.call_args[1]["json"] == {"assigned_name": "Samik Sanyal"}


def test_t2_failed_assign_does_not_commit(assigner):
    peek = _shift_response("Samik Sanyal")
    with patch.object(assigner, "auto_resolve_tickets_by_reporter", return_value=0), \
         patch.object(assigner, "get_unassigned_tickets", return_value=[_ticket()]), \
         patch.object(assigner, "process_t2_ticket", return_value=False), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post") as mock_post:
        stats = assigner.run_auto_assignment("t2")
    assert stats["assigned"] == 0
    mock_post.assert_not_called()


def test_t2_in_batch_uses_commit_next_name(assigner):
    peek = _shift_response("Samik Sanyal")
    first_commit = _shift_response("Shashi Singh")
    second_commit = _shift_response("Wasim Raja")
    with patch.object(assigner, "auto_resolve_tickets_by_reporter", return_value=0), \
         patch.object(assigner, "get_unassigned_tickets", return_value=[_ticket("RHT1"), _ticket("RHT2")]), \
         patch.object(assigner, "process_t2_ticket", return_value=True) as process, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post", side_effect=[first_commit, second_commit]):
        assigner.run_auto_assignment("t2")
    names = [call[0][2] for call in process.call_args_list]
    assert names == ["Samik Sanyal", "Shashi Singh"]


def test_t1_empty_queue_does_not_call_shift_frontend(assigner):
    with patch.object(assigner, "get_unassigned_tickets", return_value=[]), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get") as mock_get, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post") as mock_post:
        assigner.run_auto_assignment("t1")
    mock_get.assert_not_called()
    mock_post.assert_not_called()


def test_t1_peek_assign_commit_name(assigner):
    peek = _shift_response("Abdul Patel")
    commit = _shift_response("Bhawyya Mittal")
    with patch.object(assigner, "get_unassigned_tickets", return_value=[_ticket()]), \
         patch.object(assigner, "process_t1_ticket", return_value=True) as process, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek) as mock_get, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post", return_value=commit) as mock_post:
        stats = assigner.run_auto_assignment("t1")
    assert stats["assigned"] == 1
    process.assert_called_once()
    assert process.call_args[0][2] == "Abdul Patel"
    assert mock_get.call_args[0][0] == "http://t1-frontend/api/shift"
    get_urls = [call[0][0] for call in mock_get.call_args_list]
    assert all("/api/round_robin" not in url for url in get_urls)
    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == "http://t1-frontend/api/shift/commit"
    assert mock_post.call_args[1]["json"] == {"assigned_name": "Abdul Patel"}


def test_cx_commit_uses_audit_pool_group(assigner):
    from lx_toolbox.core.servicenow_autoassign import TeamConfig
    anz = TeamConfig(
        team_name="GLS CX - ANZ",
        assignment_group_id=["ag"],
        frontend_shift_manager_url="http://cx-frontend",
        frontend_group_param="anz",
    )
    audit = TeamConfig(
        team_name="GLS CX - Audit APAC",
        assignment_group_id=["ag-audit"],
        frontend_shift_manager_url="http://cx-frontend",
        frontend_group_param="audit-apac",
    )
    assigner.teams["gls-cx-apac-anz"] = anz
    peek = _shift_response("Zone Person")
    commit = _shift_response("Next Person")
    with patch.object(assigner, "get_unassigned_tickets", return_value=[_ticket()]), \
         patch.object(assigner, "process_gls_cx_ticket", return_value=("Audit Person", audit)), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post", return_value=commit) as mock_post:
        stats = assigner.run_auto_assignment("gls-cx-apac-anz")
    assert stats["assigned"] == 1
    assert mock_post.call_args[1]["json"] == {"assigned_name": "Audit Person"}
    assert mock_post.call_args[1]["params"] == {"group": "audit-apac"}


# ---------------------------------------------------------------------------
# Helpers for GLS CX subject-routing tests (Features 1 & 2)
# ---------------------------------------------------------------------------

def _cx_assigner():
    """Assigner with an APAC zone that has ANZ, rhls-entitlement, and tua schedule blocks."""
    sa = ServiceNowAutoAssign(_config())
    sa._gls_cx_team_zone = {}
    sa._email_alias_to_team_key = {}
    sa._schedule_only_team_keys = set()

    sa.teams["gls-cx-apac-anz"] = TeamConfig(
        team_name="GLS CX - ANZ",
        assignment_group_id=["ag-anz"],
        frontend_shift_manager_url="http://cx-frontend",
        frontend_group_param="anz",
    )
    sa._gls_cx_team_zone["gls-cx-apac-anz"] = "apac"

    sa.teams["gls-cx-apac-audit-apac"] = TeamConfig(
        team_name="GLS CX - Audit APAC",
        assignment_group_id=["ag-audit"],
        frontend_shift_manager_url="http://cx-frontend",
        frontend_group_param="audit-apac",
    )
    sa._gls_cx_team_zone["gls-cx-apac-audit-apac"] = "apac"
    sa._schedule_only_team_keys.add("gls-cx-apac-audit-apac")

    sa.teams["gls-cx-apac-rhls-entitlement-apac"] = TeamConfig(
        team_name="GLS CX - RHLS Entitlement APAC",
        assignment_group_id=["ag-rhls"],
        frontend_shift_manager_url="http://cx-frontend",
        frontend_group_param="rhls-entitlement-apac",
    )
    sa._gls_cx_team_zone["gls-cx-apac-rhls-entitlement-apac"] = "apac"
    sa._schedule_only_team_keys.add("gls-cx-apac-rhls-entitlement-apac")

    sa.teams["gls-cx-apac-tua-apac"] = TeamConfig(
        team_name="GLS CX - TUA APAC",
        assignment_group_id=["ag-tua"],
        frontend_shift_manager_url="http://cx-frontend",
        frontend_group_param="tua-apac",
    )
    sa._gls_cx_team_zone["gls-cx-apac-tua-apac"] = "apac"
    sa._schedule_only_team_keys.add("gls-cx-apac-tua-apac")

    return sa


def _cx_ticket(short_desc="", email=""):
    return {
        "sys_id": "sys1",
        "number": "RHT0001",
        "description": "",
        "short_description": short_desc,
        "u_email_from_address": email,
        "contact_source": "",
    }


# ---------------------------------------------------------------------------
# Feature 1 — RHLS Course Entitlement routing
# ---------------------------------------------------------------------------

def test_rhls_entitlement_ticket_routes_to_rhls_schedule():
    sa = _cx_assigner()
    ticket = _cx_ticket("New RHLS Course Entitlement")
    with patch.object(sa, "peek_shift", return_value="Rajini Kumar") as mock_peek, \
         patch.object(sa, "lookup_user_sys_id", return_value="sys-raj"), \
         patch.object(sa, "update_ticket", return_value=True):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is not None
    assignee_name, commit_config = result
    assert assignee_name == "Rajini Kumar"
    assert commit_config is sa.teams["gls-cx-apac-rhls-entitlement-apac"]
    mock_peek.assert_called_once_with(sa.teams["gls-cx-apac-rhls-entitlement-apac"])


def test_rhls_entitlement_with_suffix_in_subject_still_matches():
    sa = _cx_assigner()
    ticket = _cx_ticket("New RHLS Course Entitlement - DO180LS - Start Date: 10-APR-2026")
    with patch.object(sa, "peek_shift", return_value="Malini Aloysius"), \
         patch.object(sa, "lookup_user_sys_id", return_value="sys-mal"), \
         patch.object(sa, "update_ticket", return_value=True):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is not None
    assert result[0] == "Malini Aloysius"


def test_rhls_entitlement_skips_when_nobody_on_shift():
    sa = _cx_assigner()
    ticket = _cx_ticket("New RHLS Course Entitlement")
    with patch.object(sa, "peek_shift", return_value="None"):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is None


def test_audit_takes_priority_over_rhls_entitlement():
    """'Audit Request received' wins when both patterns appear in subject."""
    sa = _cx_assigner()
    ticket = _cx_ticket("Audit Request received - New RHLS Course Entitlement")
    with patch.object(sa, "peek_shift", return_value="Audit Person"), \
         patch.object(sa, "lookup_user_sys_id", return_value="sys-aud"), \
         patch.object(sa, "update_ticket", return_value=True):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is not None
    _, commit_config = result
    assert commit_config is sa.teams["gls-cx-apac-audit-apac"]


def test_audit_takes_priority_over_tua():
    """'Audit Request received' (slot 1) wins over TUA (slot 3) when both appear."""
    sa = _cx_assigner()
    ticket = _cx_ticket("Audit Request received - Daniel Joyner mentioned you in Pending TUAs")
    with patch.object(sa, "peek_shift", return_value="Audit Person"), \
         patch.object(sa, "lookup_user_sys_id", return_value="sys-aud"), \
         patch.object(sa, "update_ticket", return_value=True):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is not None
    _, commit_config = result
    assert commit_config is sa.teams["gls-cx-apac-audit-apac"]


# ---------------------------------------------------------------------------
# Feature 2 — TUA routing
# ---------------------------------------------------------------------------

def test_tua_ticket_routes_to_tua_schedule():
    sa = _cx_assigner()
    ticket = _cx_ticket("Akshit Kapoor mentioned you in Pending TUAs")
    with patch.object(sa, "peek_shift", return_value="Malini Aloysius") as mock_peek, \
         patch.object(sa, "lookup_user_sys_id", return_value="sys-mal"), \
         patch.object(sa, "update_ticket", return_value=True):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is not None
    assignee_name, commit_config = result
    assert assignee_name == "Malini Aloysius"
    assert commit_config is sa.teams["gls-cx-apac-tua-apac"]
    mock_peek.assert_called_once_with(sa.teams["gls-cx-apac-tua-apac"])


def test_tua_skips_when_nobody_on_shift():
    sa = _cx_assigner()
    ticket = _cx_ticket("Daniel Joyner mentioned you in Pending TUAs")
    with patch.object(sa, "peek_shift", return_value="None"):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is None


def test_rhls_entitlement_takes_priority_over_tua():
    """RHLS Entitlement check (slot 2) wins over TUA (slot 3) when both match."""
    sa = _cx_assigner()
    ticket = _cx_ticket("New RHLS Course Entitlement - mentioned you in Pending TUAs")
    with patch.object(sa, "peek_shift", return_value="Rajini Kumar"), \
         patch.object(sa, "lookup_user_sys_id", return_value="sys-raj"), \
         patch.object(sa, "update_ticket", return_value=True):
        result = sa.process_gls_cx_ticket(
            ticket, sa.teams["gls-cx-apac-anz"], "Zone Person", team_key="gls-cx-apac-anz"
        )
    assert result is not None
    _, commit_config = result
    assert commit_config is sa.teams["gls-cx-apac-rhls-entitlement-apac"]


# ---------------------------------------------------------------------------
# Feature 3 — LMS country in work note
# ---------------------------------------------------------------------------

def test_lms_work_note_includes_country_when_present():
    sa = ServiceNowAutoAssign(_config())
    entries = [{"fullName": "Arnold Fields", "userName": "FIELDSA_HHMI",
                "email": "fjeldsa@hhmi.org", "firstName": "Arnold",
                "lastName": "Fields", "country": "United States"}]
    note = sa._format_lms_work_note(entries)
    assert "Country: United States" in note


def test_lms_work_note_shows_blank_country_when_field_missing():
    sa = ServiceNowAutoAssign(_config())
    entries = [{"fullName": "Arnold Fields", "userName": "FIELDSA_HHMI"}]
    note = sa._format_lms_work_note(entries)
    assert "Country:" in note


def test_lms_work_note_shows_blank_country_when_field_is_empty_string():
    sa = ServiceNowAutoAssign(_config())
    entries = [{"fullName": "Arnold Fields", "country": ""}]
    note = sa._format_lms_work_note(entries)
    assert "Country:" in note


def test_lms_work_note_country_always_last():
    sa = ServiceNowAutoAssign(_config())
    entries = [{"fullName": "Arnold Fields", "country": "Spain"}]
    note = sa._format_lms_work_note(entries)
    lines = note.splitlines()
    country_idx = next(i for i, l in enumerate(lines) if "Country" in l)
    # Country is the final field line
    assert all("Country" not in l for l in lines[country_idx + 1:])
