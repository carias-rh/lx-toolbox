"""Auto-Assign peek/commit HTTP contract (issue #32)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from lx_toolbox.core.servicenow_autoassign import ServiceNowAutoAssign


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


def _feedback(number="RHT0001"):
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
         patch.object(assigner, "get_unassigned_tickets", return_value=[_feedback()]), \
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
         patch.object(assigner, "get_unassigned_tickets", return_value=[_feedback()]), \
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
         patch.object(assigner, "get_unassigned_tickets", return_value=[_feedback("RHT1"), _feedback("RHT2")]), \
         patch.object(assigner, "process_t2_ticket", return_value=True) as process, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post", side_effect=[first_commit, second_commit]):
        assigner.run_auto_assignment("t2")
    names = [call[0][2] for call in process.call_args_list]
    assert names == ["Samik Sanyal", "Shashi Singh"]


def test_t1_empty_queue_still_calls_shift_frontend(assigner):
    peek = _shift_response("Abdul Patel", round_robin=False)
    with patch.object(assigner, "get_unassigned_tickets", return_value=[]), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek) as mock_get, \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post") as mock_post:
        assigner.run_auto_assignment("t1")
    mock_get.assert_called()
    mock_post.assert_not_called()


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
    with patch.object(assigner, "get_unassigned_tickets", return_value=[_feedback()]), \
         patch.object(assigner, "process_gls_cx_ticket", return_value=("Audit Person", audit)), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.get", return_value=peek), \
         patch("lx_toolbox.core.servicenow_autoassign.requests.post", return_value=commit) as mock_post:
        stats = assigner.run_auto_assignment("gls-cx-apac-anz")
    assert stats["assigned"] == 1
    assert mock_post.call_args[1]["json"] == {"assigned_name": "Audit Person"}
    assert mock_post.call_args[1]["params"] == {"group": "audit-apac"}
