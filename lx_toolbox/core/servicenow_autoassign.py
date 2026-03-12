import os
import re
import time
import json
import logging
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

from ..utils.config_manager import ConfigManager
from ..utils.helpers import step_logger

# Configure logging with environment fallback only if no handlers exist yet
if not logging.getLogger().hasHandlers():
    _env_log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    _numeric_level = getattr(logging, _env_log_level, logging.INFO)
    logging.basicConfig(level=_numeric_level, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

class TicketState(Enum):
    NEW = "1"
    IN_PROGRESS = "2"
    PENDING_CUSTOMER = "-2"
    CUSTOMER_RESPONDED = "14"
    RESOLVED = "-6"
    CLOSED_AS_DUPLICATE = "12"
    CLOSED_CANCELLED = "8"
    REOPENED = "13"
    EXAMINER_TRAINING = "19"
    WAITING_ON_ENGINEERING = "15"
    WAITING_ON_PLATFORM = "16"
    WAITING_ON_CURRICULUM = "17"
    WAITING_ON_IT = "18"
    CLOSED = "7"  # If still needed, otherwise remove if not used elsewhere

@dataclass
class TeamConfig:
    """Configuration for a specific team's auto-assignment behavior"""
    team_name: str
    assignment_group_id: List[str]
    category: Optional[str] = None
    subcategory: Optional[str] = None
    issue_type: Optional[str] = None
    target_states: List[str] = None
    auto_resolve_reporters: List[str] = None
    acknowledgment_template: str = ""
    frontend_shift_manager_url: Optional[str] = None
    frontend_group_param: Optional[str] = None
    
    def __post_init__(self):
        # Normalize assignment_group_id to a list of strings
        if isinstance(self.assignment_group_id, str):
            self.assignment_group_id = [self.assignment_group_id]
        elif self.assignment_group_id is None:
            self.assignment_group_id = []
        if self.target_states is None:
            self.target_states = ["1", "2", "-2", "14", "13", "15", "16", "17", "18"]
        if self.auto_resolve_reporters is None:
            self.auto_resolve_reporters = []

    def get_primary_assignment_group_id(self) -> Optional[str]:
        """Return the first assignment group id as the primary one for updates."""
        return self.assignment_group_id[0] if self.assignment_group_id else None

class ServiceNowAutoAssign:
    def __init__(self, config: ConfigManager):
        self.config = config
        self.logger = step_logger
        
        # Initialize ServiceNow connection
        self.instance_url = config.get("ServiceNow", "SNOW_INSTANCE_URL")
        self.username = config.get("ServiceNow", "SNOW_API_USER") 
        self.password = config.get("ServiceNow", "SNOW_API_PASSWORD")
        
        if not all([self.instance_url, self.username, self.password]):
            raise ValueError("ServiceNow credentials not properly configured")
            
        self.session = requests.Session()
        self.session.auth = (self.username, self.password)
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        
        # Initialize LMS connection for name lookups
        self.lms_username = config.get("LMS", "LMS_USERNAME")
        self.lms_client_id = config.get("LMS", "LMS_CLIENT_ID") 
        self.lms_client_secret = config.get("LMS", "LMS_CLIENT_SECRET")
        self._lms_token = None
        
        # Load team configurations
        self.teams = self._load_team_configurations()
        
        # Cache for team members to avoid repeated API calls
        self._team_members_cache = {}
        
    def _load_team_configurations(self) -> Dict[str, TeamConfig]:
        """Load team configurations.

        Static teams read their assignment_group_id from config/env
        (falling back to legacy hardcoded values).
        GLS CX teams are built dynamically from SNOW_GROUPS_CONFIG.
        """
        teams = {}

        # -- static teams ------------------------------------------------

        teams["t1"] = TeamConfig(
            team_name="RHT Learner Experience",
            assignment_group_id=(
                self.config.get("T1", "ASSIGNMENT_GROUP_ID")
                or "5afc8ba24f8cf6004db6022f0310c70a"
            ),
            category="RHLS Standard Support",
            frontend_shift_manager_url=self.config.get("T1", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=(
                self.config.get("T1", "ACKNOWLEDGMENT_TEMPLATE")
                or "Hi {customer_name},\n\nThanks for contacting Red Hat Online Learning support team.\n\nWe have received your request and working on it, will update you at the earliest.\n\nBest Regards,\n{assignee_name}\nRed Hat Training Technical Support"
            ),
        )

        teams["t2"] = TeamConfig(
            team_name="RHT Learner Experience - T2",
            assignment_group_id=(
                self.config.get("T2", "ASSIGNMENT_GROUP_ID")
                or "974cb3e01bc31c50c57c3224cc4bcbfe"
            ),
            category="RHLS Basic External Support",
            subcategory="Course Content",
            issue_type="Other",
            frontend_shift_manager_url=self.config.get("T2", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=(
                self.config.get("T2", "ACKNOWLEDGMENT_TEMPLATE")
                or "Hi {customer_name},\n\nThanks for submitting your feedback to the Learner Experience Team.\n\nWe are reviewing your message and will get back to you as soon as possible.\n\nBest Regards,\n{assignee_name}\n{team_name}"
            ),
            auto_resolve_reporters=[
                "gls-ftaylor", "lauber", "rht-jordisola", "rht-zgutterman", "abhkuma@redhat.com",
                "lxncastill", "rh-ee-smaity", "nehsingh@redhat.com", "rht-bchardim", "yuvaraj-rhls",
                "rht-pagomez", "vig@redhat.com", "rhn-gls-rtaniguchi", "abpatel@redhat.com",
                "rh-ee-tshi", "rht-sbonnevi", "rht-psolarvi", "wraja@redhat.com", "chetan-rhls",
                "rdacosta1@redhat.com", "ssanyal@redhat.com", "rh-ee-jingyuwa", "vsing@redhat.com",
                "shasingh01", "rh-ee-jyague", "rhn-gps-jdandrea", "carias@redhat.com",
                "rht-anhernan", "rht-eparenti", "amarirom@redhat.com", "rh-ee-mtahmeed",
                "rhn-engineering-daobrien", "nehsingh@redhat.com", "yassingh@redhat.com"
            ],
        )

        _gls_engagement_ack = (
            self.config.get("GLS_RHLS_ENGAGEMENT", "ACKNOWLEDGMENT_TEMPLATE")
            or "Hi {customer_name},\n\nThank you for contacting the RHLS Support Team.\n\nWe've received your request and shall get back to you at the earliest.\n\nBest Regards,\nRed Hat Training Support\n\nPlease note: If your request was submitted over the weekend, we will review it on the next working day."
        )

        teams["gls-rhls-engagement-apac"] = TeamConfig(
            team_name="GLS RHLS Engagement - APAC",
            assignment_group_id=(
                self.config.get("GLS_RHLS_ENGAGEMENT_APAC", "ASSIGNMENT_GROUP_ID")
                or "9fddf7032b24ea50ec2ef42f4e91bf84"
            ),
            frontend_shift_manager_url=self.config.get("GLS_RHLS_ENGAGEMENT_APAC", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=_gls_engagement_ack,
        )

        teams["gls-rhls-engagement-emea"] = TeamConfig(
            team_name="GLS RHLS Engagement - EMEA",
            assignment_group_id=(
                self.config.get("GLS_RHLS_ENGAGEMENT_EMEA", "ASSIGNMENT_GROUP_ID")
                or "f53b635147b46a90b45f42fc416d4387"
            ),
            frontend_shift_manager_url=self.config.get("GLS_RHLS_ENGAGEMENT_EMEA", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=_gls_engagement_ack,
        )

        _na_agids_raw = self.config.get("GLS_RHLS_ENGAGEMENT_NA", "ASSIGNMENT_GROUP_ID")
        teams["gls-rhls-engagement-na"] = TeamConfig(
            team_name="GLS RHLS Engagement - NA",
            assignment_group_id=(
                [a.strip() for a in _na_agids_raw.split(",") if a.strip()]
                if _na_agids_raw
                else ["43aa77114770aa90b45f42fc416d43cf", "c8a0b31d47786a90b45f42fc416d43dc", "79323f5d47b86a90b45f42fc416d43f4"]
            ),
            frontend_shift_manager_url=self.config.get("GLS_RHLS_ENGAGEMENT_NA", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=_gls_engagement_ack,
        )

        teams["remote-exam-readiness-support"] = TeamConfig(
            team_name="Remote Exam - Readiness Support",
            assignment_group_id=(
                self.config.get("REMOTE_EXAM_READINESS_SUPPORT", "ASSIGNMENT_GROUP_ID")
                or "962d0c0d1b740a504cec766dcc4bcb7a"
            ),
            frontend_shift_manager_url=self.config.get("REMOTE_EXAM_READINESS_SUPPORT", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=(
                self.config.get("REMOTE_EXAM_READINESS_SUPPORT", "ACKNOWLEDGMENT_TEMPLATE")
                or "Hi {customer_name},\n\nThank you for contacting the Remote Exam - Readiness Support Team.\n\nWe've received your request and shall get back to you at the earliest.\n\nBest Regards,\n{assignee_name}\n{team_name}"
            ),
        )

        # -- GLS CX dynamic teams from SNOW_GROUPS_CONFIG -----------------

        self._load_gls_cx_teams(teams)

        logger.debug(f"Loaded teams: {list(teams.keys())}")
        return teams

    def _load_gls_cx_teams(self, teams: Dict[str, "TeamConfig"]) -> None:
        """Build GLS CX sub-team configs from the SNOW_GROUPS_CONFIG env var.

        The env var is a JSON object:
        {
          "zones": [{"id": "apac", "name": "APAC"}, ...],
          "groups": [
            {"id": "anz", "name": "ANZ", "zone_id": "apac",
             "assignment_group_ids": ["<sys_id>"]},
            ...
          ]
        }

        Groups whose id starts with ``audit-`` are schedule-only groups used
        to route "Audit Request received" tickets to a zone-specific subset
        of contacts with their own priority order.
        """
        self._gls_cx_team_zone: Dict[str, str] = {}

        raw = self.config.get("SNOW", "GROUPS_CONFIG") or ""
        if not raw:
            return

        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("SNOW_GROUPS_CONFIG is not valid JSON – skipping GLS CX team loading")
            return

        frontend_url = self.config.get("FRONTEND", "OPENSHIFT_ROUTE")

        _gls_cx_ack = (
            self.config.get("GLS_CX", "ACKNOWLEDGMENT_TEMPLATE")
            or "Hi {customer_name},\n\nThanks for contacting Red Hat Customer Experience team.\n\nWe have received your request and working on it, will update you at the earliest.\n\nBest Regards,\n{assignee_name}\nRed Hat Customer Experience Team"
        )

        for group in cfg.get("groups", []):
            slug = group.get("id")
            agids = group.get("assignment_group_ids", [])
            if not slug or not agids:
                continue
            zone_id = group.get("zone_id", "")
            team_key = f"gls-cx-{zone_id}-{slug}" if zone_id else f"gls-cx-{slug}"
            display_name = group.get("name", slug.upper())
            teams[team_key] = TeamConfig(
                team_name=f"GLS CX - {display_name}",
                assignment_group_id=agids,
                frontend_shift_manager_url=frontend_url,
                frontend_group_param=slug,
                acknowledgment_template=_gls_cx_ack,
            )
            self._gls_cx_team_zone[team_key] = zone_id

    def _get_team_members(self, team_key: str) -> List[str]:
        """Get team member sys_ids, using cache to avoid repeated API calls"""
        if team_key in self._team_members_cache:
            return self._team_members_cache[team_key]
            
        if team_key not in self.teams:
            logger.warning(f"Unknown team: {team_key}")
            return []
            
        team_config = self.teams[team_key]
        assignment_group_ids = team_config.assignment_group_id
        
        try:
            group_url = f"{self.instance_url}/api/now/table/sys_user_grmember"
            if not assignment_group_ids:
                return []
            if len(assignment_group_ids) == 1:
                group_query = f"group={assignment_group_ids[0]}"
            else:
                group_query = f"groupIN{','.join(assignment_group_ids)}"
            group_params = {
                "sysparm_query": group_query,
                "sysparm_fields": "user",
                "sysparm_limit": "200"
            }
            
            group_response = self.session.get(group_url, params=group_params)
            group_response.raise_for_status()
            group_members = group_response.json().get("result", [])
            team_member_sys_ids = [
                member.get('user', {}).get('value') if isinstance(member.get('user'), dict) else member.get('user') 
                for member in group_members if member.get('user')
            ]
            
            # Cache the result
            self._team_members_cache[team_key] = team_member_sys_ids
            logger.debug(f"Cached {len(team_member_sys_ids)} members for team {team_key}")
            
            return team_member_sys_ids
            
        except Exception as e:
            logger.error(f"Error getting team members for {team_key}: {e}")
            # Cache empty list to avoid repeated failed calls
            self._team_members_cache[team_key] = []
            return []

    def preload_team_data(self, team_keys: List[str] = None) -> None:
        """Preload team member data for specified teams or all teams"""
        if team_keys is None:
            team_keys = list(self.teams.keys())
            
        logger.info(f"Preloading team data for: {team_keys}")
        for team_key in team_keys:
            if team_key in self.teams:
                self._get_team_members(team_key)
        logger.info("Team data preloading complete")

    def clear_team_cache(self, team_key: str = None) -> None:
        """Clear cached team data for a specific team or all teams"""
        if team_key:
            if team_key in self._team_members_cache:
                del self._team_members_cache[team_key]
                logger.debug(f"Cleared cache for team {team_key}")
        else:
            self._team_members_cache.clear()
            logger.debug("Cleared all team cache")

    def test_connection(self) -> bool:
        """Test the connection to ServiceNow"""
        try:
            response = self.session.get(f"{self.instance_url}/api/now/table/sys_user?sysparm_limit=1")
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"ServiceNow connection test failed: {str(e)}")
            return False

    def get_lms_token(self) -> Optional[str]:
        """Get LMS API token for user name lookups"""
        if self._lms_token:
            return self._lms_token
            
        if not all([self.lms_username, self.lms_client_id, self.lms_client_secret]):
            logger.warning("LMS credentials not configured, name lookups will be limited")
            return None
            
        token_url = "https://training-lms.redhat.com/auth/oauth2/token"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'username': self.lms_username,
            'client_id': self.lms_client_id,
            'client_secret': self.lms_client_secret,
            'grant_type': 'client_credentials'
        }
        
        try:
            response = requests.post(token_url, headers=headers, data=data)
            response.raise_for_status()
            token_response = response.json()
            self._lms_token = token_response.get('access_token')
            return self._lms_token
        except Exception as e:
            logger.error(f"Error getting LMS token: {e}")
            return None

    def lookup_user_name(self, username: str = None, email: str = None) -> str:
        """Look up full name from LMS API, auto-refreshing token if expired.
        Search by username or email; at least one must be provided.
        """
        identifier = (email or username or "").strip()
        if not identifier:
            return ""

        token = self.get_lms_token()
        if not token:
            return identifier

        headers = {'Authorization': f'Bearer {token}', 'Accept': '*/*'}
        base_url = "https://training-lms.redhat.com/ws/user"

        if email:
            url = f"{base_url}/search?q={email}"
        else:
            url = f"{base_url}?username={username}"

        try:
            response = requests.get(url, headers=headers)

            # If token expired (401), refresh and retry once
            if response.status_code == 401:
                logger.debug(f"Token expired for {identifier}, refreshing...")
                self._lms_token = None
                token = self.get_lms_token()
                if token:
                    headers = {'Authorization': f'Bearer {token}', 'Accept': '*/*'}
                    response = requests.get(url, headers=headers)
                else:
                    logger.warning(f"Could not refresh token for {identifier}")
                    return identifier

            # If still not 200, try with internal_ prefix for redhat users
            if response.status_code != 200 and username:
                response = requests.get(f"{base_url}?username=internal_{username}", headers=headers)

            response.raise_for_status()
            data = response.json()
            logger.debug(f"LMS API raw response for {identifier}: {json.dumps(data, indent=2)}")

            if email:
                users = data.get('items', [])
                logger.debug(f"LMS search returned {len(users)} results for email {email}")
                for i, u in enumerate(users):
                    logger.debug(f"  result[{i}]: username={u.get('username')}, fullName={u.get('fullName')}, "
                                 f"firstName={u.get('firstName')}, lastName={u.get('lastName')}, email={u.get('email')}")
                if users:
                    user_data = users[0]
                else:
                    return identifier
            else:
                user_data = data.get('user', {})

            full_name = user_data.get('fullName')
            logger.debug(f"LMS user_data for {identifier}: fullName={full_name}, "
                         f"firstName={user_data.get('firstName')}, lastName={user_data.get('lastName')}")
            if not full_name:
                first_name = user_data.get('firstName', '').capitalize()
                last_name = user_data.get('lastName', '').capitalize()
                full_name = f"{first_name} {last_name}".strip()

            logger.debug(f"LMS resolved {identifier} -> {full_name}")
            return full_name or identifier

        except Exception as e:
            logger.error(f"Error looking up user {identifier}: {e}")
            return identifier

    def lookup_user_sys_id(self, display_name: str, team_key: str = None) -> Optional[str]:
        """Look up a user's sys_id by their display name, using team filtering only when there are multiple matches"""
        try:
            if display_name != "None":
                
                # First try exact name match
                params = {
                    "sysparm_query": f"name={display_name}",
                    "sysparm_fields": "sys_id,name,user_name",
                    "sysparm_limit": "10"
                }
                
                url = f"{self.instance_url}/api/now/table/sys_user"
                response = self.session.get(url, params=params)
                response.raise_for_status()
                
                users = response.json().get("result", [])
                
                # If only one exact match, return it immediately
                if len(users) == 1:
                    user = users[0]
                    logger.debug(f"Found unique exact match: '{display_name}' -> sys_id: {user.get('sys_id')} (username: {user.get('user_name')})")
                    return user.get('sys_id')
                
                # If multiple matches and team filtering is available, filter by team
                if len(users) > 1 and team_key and team_key in self.teams:
                    logger.debug(f"Multiple matches found for '{display_name}', filtering by team {team_key}")
                    
                    # Get cached team members
                    team_member_sys_ids = self._get_team_members(team_key)
                    
                    if team_member_sys_ids:
                        # Filter users by team membership
                        team_users = [user for user in users if user.get('sys_id') in team_member_sys_ids]
                        
                        if len(team_users) == 1:
                            user = team_users[0]
                            logger.debug(f"Found unique team match: '{display_name}' -> sys_id: {user.get('sys_id')} (username: {user.get('user_name')})")
                            return user.get('sys_id')
                        elif len(team_users) > 1:
                            # Multiple team matches, take the first one
                            user = team_users[0]
                            logger.warning(f"Multiple team matches for '{display_name}', using first: sys_id: {user.get('sys_id')} (username: {user.get('user_name')})")
                            return user.get('sys_id')
                        else:
                            logger.warning(f"No team matches found for '{display_name}' in team {team_key}")
                    else:
                        logger.warning(f"No team members found for team {team_key}")
                        # Fall back to first match if no team members
                        if users:
                            user = users[0]
                            logger.debug(f"No team data available, using first match: '{display_name}' -> sys_id: {user.get('sys_id')} (username: {user.get('user_name')})")
                            return user.get('sys_id')
                
                # If exact match found but no team filtering needed/available
                elif len(users) > 1:
                    user = users[0]
                    logger.debug(f"Multiple matches found, using first: '{display_name}' -> sys_id: {user.get('sys_id')} (username: {user.get('user_name')})")
                    return user.get('sys_id')
                
                # No exact match, try broader search
                if ' ' in display_name:
                    # For full names like "Carlos Arias", search by first and last name
                    name_parts = display_name.split()
                    first_name = name_parts[0]
                    last_name = name_parts[-1]
                    
                    params = {
                        "sysparm_query": f"first_name={first_name}^last_name={last_name}",
                        "sysparm_fields": "sys_id,name,first_name,last_name,user_name",
                        "sysparm_limit": "10"
                    }
                else:
                    # For single names, try username or partial name match
                    params = {
                        "sysparm_query": f"user_name={display_name}^ORnameSTARTSWITH{display_name}^ORfirst_nameLIKE{display_name}^ORlast_nameLIKE{display_name}",
                        "sysparm_fields": "sys_id,name,first_name,last_name,user_name",
                        "sysparm_limit": "10"
                    }
                
                response = self.session.get(url, params=params)
                response.raise_for_status()
                users = response.json().get("result", [])
                
                # Look for best matches
                for user in users:
                    user_full_name = user.get('name', '')
                    user_first = user.get('first_name', '').lower()
                    user_last = user.get('last_name', '').lower()
                    user_username = user.get('user_name', '').lower()
                    
                    # For full names, check if first+last matches
                    if ' ' in display_name:
                        name_parts = display_name.lower().split()
                        if len(name_parts) >= 2:
                            search_first = name_parts[0]
                            search_last = name_parts[-1]
                            
                            if user_first == search_first and user_last == search_last:
                                logger.debug(f"Found first+last name match: '{display_name}' -> '{user_full_name}' (sys_id: {user.get('sys_id')})")
                                return user.get('sys_id')
                    
                    # Check for username match
                    if display_name.lower() == user_username:
                        logger.debug(f"Found username match: '{display_name}' -> '{user_full_name}' (sys_id: {user.get('sys_id')})")
                        return user.get('sys_id')
                    
                    # Check for partial name matches
                    display_lower = display_name.lower()
                    if (display_lower in user_full_name.lower() or 
                        user_full_name.lower() in display_lower):
                        logger.debug(f"Found partial name match: '{display_name}' -> '{user_full_name}' (sys_id: {user.get('sys_id')})")
                        return user.get('sys_id')
                
                logger.warning(f"No user found for display name: '{display_name}'{' in team ' + team_key if team_key else ''}")
                return None
                
        except Exception as e:
            logger.error(f"Error looking up user sys_id for '{display_name}': {e}")
            return None

    def get_unassigned_tickets(self, team_key: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get unassigned tickets for a specific team"""
        team_config = self.teams.get(team_key)
        if not team_config:
            raise ValueError(f"Unknown team: {team_key}")
            
        # Build assignment group filter (supports multiple groups)
        if not team_config.assignment_group_id:
            assignment_group_filter = ""
        elif len(team_config.assignment_group_id) == 1:
            assignment_group_filter = f"assignment_group={team_config.assignment_group_id[0]}"
        else:
            assignment_group_filter = f"assignment_groupIN{','.join(team_config.assignment_group_id)}"

        query_parts = [
            "assigned_toISEMPTY",
            assignment_group_filter,
            f"stateIN{','.join(team_config.target_states)}",
            "active=true"
        ]
        
        params = {
            "sysparm_query": "^".join(query_parts),
            "sysparm_display_value": "true",
            "sysparm_fields": "sys_id,number,short_description,description,contact_source,state,u_email_from_address",
            "sysparm_limit": str(limit)
        }
        
        url = f"{self.instance_url}/api/now/table/x_redha_red_hat_tr_x_red_hat_training"
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json().get("result", [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching tickets for team {team_key}: {e}")
            return []

    def update_ticket(self, ticket_sys_id: str, updates: Dict[str, Any]) -> bool:
        """Update a ticket with the provided data"""
        url = f"{self.instance_url}/api/now/table/x_redha_red_hat_tr_x_red_hat_training/{ticket_sys_id}"
        
        try:
            logger.debug(f"Updating ticket {ticket_sys_id} with data: {updates}")
            response = self.session.patch(url, json=updates)
            response.raise_for_status()
            logger.debug(f"Successfully updated ticket {ticket_sys_id}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error updating ticket {ticket_sys_id}: {e}")
            if hasattr(e.response, 'text'):
                logger.error(f"Response text: {e.response.text}")
            return False



    def process_t1_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str) -> bool:
        """Process a T1 team ticket in two steps: ACK/categorization, then assignment."""
        try:
            # Look up assignee sys_id
            assignee_sys_id = self.lookup_user_sys_id(assignee_name, "t1")
            if not assignee_sys_id:
                logger.error(f"Could not find sys_id for assignee: {assignee_name}")
                return False
            
            # Extract customer info
            contact_source = ticket.get('contact_source', '')
            if contact_source:
                name_parts = contact_source.split()
                customer_name = f"{name_parts[0]} {name_parts[1] if len(name_parts) > 1 else ''}".strip()
            else:
                customer_name = ""
            
            # PHASE 1: Update ticket with categorization and ACK (but no assignment yet)
            phase1_updates = {
                'state': TicketState.IN_PROGRESS.value,
                'category': team_config.category,
                'time_worked': '60'
            }
            primary_group_id = team_config.get_primary_assignment_group_id()
            if primary_group_id:
                phase1_updates['assignment_group'] = primary_group_id
            
            # Handle empty fields
            if not ticket.get('short_description'):
                phase1_updates['short_description'] = "RH Academy"
            if not ticket.get('u_email_from_address'):
                phase1_updates['u_email_from_address'] = "fix_this_non_sense_email@manually.com"
                
            # Handle iqlaserpress.net emails  
            email = ticket.get('u_email_from_address', '')
            if '@iqlaserpress.net' in email:
                phase1_updates['u_email_from_address'] = "lywillia@redhat.com"
                phase1_updates['contact_source'] = "Lynda Williams"
                customer_name = "Lynda Williams"
            
            # Add acknowledgment comment if not a Jira ticket
            short_desc = ticket.get('short_description', '')
            if '[training-feedback]' not in short_desc:
                ack_message = team_config.acknowledgment_template.format(
                    customer_name=customer_name,
                    assignee_name=assignee_name
                )
                phase1_updates['comments'] = ack_message
            
            # Execute Phase 1
            if not self.update_ticket(ticket['sys_id'], phase1_updates):
                logger.error(f"Failed to update ticket {ticket['number']} with categorization and ACK")
                return False
            time.sleep(1)

            # PHASE 2: Assign to the specific user
            phase2_updates = {'assigned_to': assignee_sys_id}
            if not self.update_ticket(ticket['sys_id'], phase2_updates):
                logger.error(f"Failed to assign ticket {ticket['number']} to {assignee_name}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error processing T1 ticket {ticket.get('number', 'unknown')}: {e}")
            return False

    def process_t2_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str) -> bool:
        """Process a T2 team ticket in two steps: ACK/categorization, then assignment."""
        try:
            assignee_sys_id = self.lookup_user_sys_id(assignee_name, "t2")
            if not assignee_sys_id:
                logger.error(f"Could not find sys_id for assignee: {assignee_name}")
                return False
            description = ticket.get('description', '')
            # PHASE 1: Categorization and ACK
            phase1_updates = {
                'state': TicketState.IN_PROGRESS.value,
                'category': team_config.category,
                'subcategory': team_config.subcategory,
                'issue': team_config.issue_type,
                'time_worked': '60'
            }
            primary_group_id = team_config.get_primary_assignment_group_id()
            if primary_group_id:
                phase1_updates['assignment_group'] = primary_group_id
            
            # Extract and process user information from description
            customer_name = ""
            try:
                # Extract username
                username_match = re.search(r"User Name:\s*(.+)", description)
                if username_match:
                    username = username_match.group(1).strip()
                    full_name = self.lookup_user_name(username)
                    phase1_updates['contact_source'] = full_name
                    customer_name = full_name
                
                # Extract email
                email_match = re.search(r"User Email:\s*(.+)", description)
                if email_match:
                    email_line = email_match.group(1)
                    email = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", email_line)
                    if email:
                        phase1_updates['u_email_from_address'] = email.group(1)
                
                # Extract course and create short description
                course_match = re.search(r"Course:\s*(.+)", description)
                version_match = re.search(r"Version:\s*(.+)", description)
                desc_match = re.search(r"Description:\s*(.+)", description)
                
                if course_match and version_match and desc_match:
                    course_full = course_match.group(1).strip()
                    # Take only the first word (course code), ignore lesson info after space
                    course = course_full.split()[0] if course_full else course_full
                    version = version_match.group(1).strip()
                    summary = desc_match.group(1).strip()
                    
                    short_desc = f"{course.upper().replace(' ', '')}-{version} Feedback: {summary[:200]}..."
                    phase1_updates['short_description'] = short_desc
                    phase1_updates['project_code'] = course.upper().replace(' ', '')
                    
            except Exception as e:
                logger.debug(f"Error extracting T2 ticket info: {e}")
            ack_message = team_config.acknowledgment_template.format(
                customer_name=customer_name,
                assignee_name=assignee_name,
                team_name=team_config.team_name
            )
            phase1_updates['comments'] = ack_message
            if not self.update_ticket(ticket['sys_id'], phase1_updates):
                logger.error(f"Failed to update ticket {ticket['number']} with categorization and ACK")
                return False
            time.sleep(1)
            # PHASE 2: Assignment
            phase2_updates = {'assigned_to': assignee_sys_id}
            if not self.update_ticket(ticket['sys_id'], phase2_updates):
                logger.error(f"Failed to assign ticket {ticket['number']} to {assignee_name}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error processing T2 ticket {ticket.get('number', 'unknown')}: {e}")
            return False


    def process_gls_rhls_engagement_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str) -> bool:
        """Process a GLS RHLS Engagement ticket with ACK only."""
        try:
#            # Look up assignee sys_id
#            assignee_sys_id = self.lookup_user_sys_id(assignee_name, team_config.team_name)
#            if not assignee_sys_id:
#                logger.error(f"Could not find sys_id for assignee: {assignee_name}")
#                return False

            # Extract customer info
            contact_source = ticket.get('contact_source', '')
            if contact_source:
                name_parts = contact_source.split()
                customer_name = f"{name_parts[0]} {name_parts[1] if len(name_parts) > 1 else ''}".strip()
            else:
                customer_name = ""

            # ACK
            updates = {
                'state': TicketState.IN_PROGRESS.value,
            }
            primary_group_id = team_config.get_primary_assignment_group_id()
            if primary_group_id:
                updates['assignment_group'] = primary_group_id

            ack_message = team_config.acknowledgment_template.format(
                customer_name=customer_name,
            )
            updates['comments'] = ack_message
            if not self.update_ticket(ticket['sys_id'], updates):
                logger.error(f"Failed to update ticket {ticket['number']} with categorization and ACK")
                return False
            return True
        except Exception as e:
            logger.error(f"Error processing GLS RHLS Engagement ticket {ticket.get('number', 'unknown')}: {e}")
            return False


    def process_gls_cx_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str, team_key: str = None) -> bool:
        """Process a GLS Customer Experience ticket: ACK + assign.

        If the ticket's short_description contains "Audit Request received",
        the assignee is resolved from the zone-level audit schedule instead
        of the standard group schedule.  When no audit person is on shift
        the ticket is skipped (returns False) so it stays unassigned.
        """
        try:
            short_desc = ticket.get('short_description', '')
            if 'Audit Request received' in short_desc and team_key:
                audit_assignee = self._resolve_audit_assignee(team_key)
                if audit_assignee and audit_assignee != "None":
                    logger.info(f"Audit ticket {ticket.get('number')} routed to audit assignee: {audit_assignee}")
                    assignee_name = audit_assignee
                else:
                    logger.info(f"Audit ticket {ticket.get('number')} – no audit person on shift, skipping")
                    return False

            assignee_sys_id = self.lookup_user_sys_id(assignee_name, team_config.team_name)
            if not assignee_sys_id:
                logger.error(f"Could not find sys_id for assignee: {assignee_name}")
                return False

            customer_name = ""
            email = ticket.get('u_email_from_address', '').strip()
            if email:
                full_name = self.lookup_user_name(email=email)
                if full_name and full_name != email:
                    customer_name = full_name
                else:
                    contact_source = ticket.get('contact_source', '')
                    if contact_source:
                        name_parts = contact_source.split()
                        customer_name = f"{name_parts[0]} {name_parts[1] if len(name_parts) > 1 else ''}".strip()
            else:
                contact_source = ticket.get('contact_source', '')
                if contact_source:
                    name_parts = contact_source.split()
                    customer_name = f"{name_parts[0]} {name_parts[1] if len(name_parts) > 1 else ''}".strip()

            # Phase 1: ACK (keep ticket in NEW state)
            updates = {}
            if customer_name:
                updates['contact_source'] = customer_name
            primary_group_id = team_config.get_primary_assignment_group_id()
            if primary_group_id:
                updates['assignment_group'] = primary_group_id
            ack_message = team_config.acknowledgment_template.format(
                customer_name=customer_name,
                assignee_name=assignee_name,
                team_name=team_config.team_name,
            )
            updates['comments'] = ack_message
            if not self.update_ticket(ticket['sys_id'], updates):
                logger.error(f"Failed to ACK ticket {ticket['number']}")
                return False
            time.sleep(1)

            # Phase 2: Assign
            if not self.update_ticket(ticket['sys_id'], {'assigned_to': assignee_sys_id}):
                logger.error(f"Failed to assign ticket {ticket['number']} to {assignee_name}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error processing GLS CX ticket {ticket.get('number', 'unknown')}: {e}")
            return False

    def process_exam_readiness_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str) -> bool:
        """Process a Remote Exam - Readiness Support ticket with ACK only."""
        try:
            # Look up assignee sys_id
            assignee_sys_id = self.lookup_user_sys_id(assignee_name, "exam-readiness")
            if not assignee_sys_id:
                logger.error(f"Could not find sys_id for assignee: {assignee_name}")
                return False

            # Extract customer info
            contact_source = ticket.get('contact_source', '')
            if contact_source:
                name_parts = contact_source.split()
                customer_name = f"{name_parts[0]} {name_parts[1] if len(name_parts) > 1 else ''}".strip()
            else:
                customer_name = ""

            # PHASE 1: ACK
            updates = {
                'state': TicketState.IN_PROGRESS.value,
            }
            primary_group_id = team_config.get_primary_assignment_group_id()
            if primary_group_id:
                updates['assignment_group'] = primary_group_id
                
            ack_message = team_config.acknowledgment_template.format(
                customer_name=customer_name,
                assignee_name=assignee_name,
                team_name=team_config.team_name
            )
            updates['comments'] = ack_message
            # Execute Phase 1
            if not self.update_ticket(ticket['sys_id'], updates):
                logger.error(f"Failed to update ticket {ticket['number']} with ACK")
                return False
            time.sleep(1)

            # PHASE 2: Assign to the specific user
            phase2_updates = {'assigned_to': assignee_sys_id}
            if not self.update_ticket(ticket['sys_id'], phase2_updates):
                logger.error(f"Failed to assign ticket {ticket['number']} to {assignee_name}")
                return False
            return True

        except Exception as e:
            logger.error(f"Error processing Exam Readiness ticket {ticket.get('number', 'unknown')}: {e}")
            return False


    def auto_resolve_tickets_by_reporter(self, team_key: str) -> int:
        """Auto-resolve tickets for specific reporters (mainly for T2 team)"""
        team_config = self.teams.get(team_key)
        if not team_config or not team_config.auto_resolve_reporters:
            return 0
            
        # Query for tickets with notifications from Jira
        query_parts = [
            f"stateIN1,2,-2,14,13,15,16,17,18",
            "active=true",
            "short_descriptionLIKEnew Jira"
        ]
        
        params = {
            "sysparm_query": "^".join(query_parts),
            "sysparm_fields": "sys_id,number,description",
            "sysparm_limit": "100"
        }
        
        url = f"{self.instance_url}/api/now/table/x_redha_red_hat_tr_x_red_hat_training"
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            tickets = response.json().get("result", [])
            
            resolved_count = 0
            for ticket in tickets:
                description = ticket.get('description', '')
                reporter_match = re.search(r"Reporter:\s*(.+)", description)
                
                if reporter_match:
                    reporter = reporter_match.group(1).strip()
                    if reporter in team_config.auto_resolve_reporters:
                        logger.info(f"Auto-resolving ticket {ticket['number']} for reporter {reporter}")
                        
                        updates = {
                            'state': TicketState.RESOLVED.value,
                            'category': team_config.category,
                            'subcategory': team_config.subcategory,
                            'issue': team_config.issue_type,
                            'time_worked': '60'
                        }
                        
                        if self.update_ticket(ticket['sys_id'], updates):
                            resolved_count += 1
                            
            return resolved_count
            
        except Exception as e:
            logger.error(f"Error auto-resolving tickets: {e}")
            return 0


    def who_is_on_shift(self, team_config: TeamConfig) -> Optional[str]:
        is_round_robin_enabled = False
        # Extra query params forwarded to /api/shift and /api/round_robin
        # (used by the gls-cx frontend to resolve the right sub-group)
        group_params = {}
        if team_config.frontend_group_param:
            group_params["group"] = team_config.frontend_group_param
        try:
            if team_config.frontend_shift_manager_url != None:
                # Check if round-robin is enabled
                try:
                    round_robin_status_response = requests.get(f"{team_config.frontend_shift_manager_url}/api/round_robin_status")
                    round_robin_status_response.raise_for_status()
                    round_robin_status = round_robin_status_response.json()
                    is_round_robin_enabled = round_robin_status.get("round_robin_enabled", False)
                except Exception as e:
                    pass
            
            if is_round_robin_enabled:
                # Get next assignee from round-robin
                round_robin_response = requests.get(f"{team_config.frontend_shift_manager_url}/api/round_robin", params=group_params)
                round_robin_response.raise_for_status()
                round_robin_data = round_robin_response.json()
                assignee_name = round_robin_data.get("name")
                logger.debug(f"Round-robin enabled, got assignee: {assignee_name}")
                return assignee_name
            else:
                # Get assignee from shift endpoint
                shift_response = requests.get(f"{team_config.frontend_shift_manager_url}/api/shift", params=group_params)
                shift_response.raise_for_status()
                shift_data = shift_response.json()
                shift_name = shift_data.get("name")
                
                if shift_name and shift_name != "None":
                    assignee_name = shift_name
                    logger.debug(f"Frontend shift assignment, got assignee: {assignee_name}")
                    return assignee_name
                else:
                    return "None"
        except Exception as e:
            pass


    def _resolve_audit_assignee(self, team_key: str) -> Optional[str]:
        """Find the audit-eligible person currently on shift for the zone of the given team.

        Uses the ``audit-<zone>`` group schedules configured in the frontend.
        Returns the assignee name, ``"None"`` if nobody is on audit shift,
        or ``None`` if no audit config exists for the zone.
        """
        zone_id = self._gls_cx_team_zone.get(team_key)
        if not zone_id:
            logger.warning(f"Cannot determine zone for team '{team_key}' – audit routing unavailable")
            return None

        audit_team_key = f"gls-cx-{zone_id}-audit-{zone_id}"
        audit_config = self.teams.get(audit_team_key)
        if not audit_config:
            logger.warning(f"No audit team config found for zone '{zone_id}' (expected key: {audit_team_key})")
            return None

        return self.who_is_on_shift(audit_config)

    def run_auto_assignment(self, team_key: str, assignee_name: str = None) -> Dict[str, int]:
        """Run auto-assignment for a specific team"""
        team_config = self.teams.get(team_key)
        if not team_config:
            raise ValueError(f"Unknown team: {team_key}")
            
        stats = {"assigned": 0, "resolved": 0, "errors": 0}
        
        # Auto-resolve Jira tickets raised by known Redhat employees (T2 team triage)
        if team_config.auto_resolve_reporters:
            stats["resolved"] = self.auto_resolve_tickets_by_reporter(team_key)
            
        # Get assignee name using frontend APIs
        if team_config.frontend_shift_manager_url != None:
            assignee_name = self.who_is_on_shift(team_config)                

        if not assignee_name and team_config.frontend_shift_manager_url:
            logger.debug(f"No assignee available for team {team_key}")
            return stats
        elif not assignee_name and not team_config.frontend_shift_manager_url:
            assignee_name = "only-ack"            
            logger.debug(f"No assignee available for team {team_key}, using Carlos Arias")

        # Get unassigned tickets
        tickets = self.get_unassigned_tickets(team_key)
        logger.debug(f"Found {len(tickets)} unassigned tickets for team {team_key}")
        
        # Process each ticket
        for ticket in tickets:
            try:
                success = False
                if "t1" in team_key:
                    if assignee_name == "None":
                        logger.debug("No one is on shift, stopping ticket processing")
                        break                
                    success = self.process_t1_ticket(ticket, team_config, assignee_name)

                elif "t2" in team_key:
                    if assignee_name == "None":
                        logger.debug("No one is on shift, stopping ticket processing")
                        break
                    success = self.process_t2_ticket(ticket, team_config, assignee_name)
                elif  "gls-rhls-engagement" in team_key:
#                       commented out for now to allow for manual assignment
#                        if assignee_name == "None":
#                            logger.debug("No one is on shift, stopping ticket processing")
#                            break
                    success = self.process_gls_rhls_engagement_ticket(ticket, team_config, assignee_name)
                elif "gls-cx" in team_key:
                    if assignee_name == "None":
                        if 'Audit Request received' not in ticket.get('short_description', ''):
                            continue
                    success = self.process_gls_cx_ticket(ticket, team_config, assignee_name, team_key=team_key)
                elif "exam" in team_key:
                    if assignee_name == "None":
                        logger.debug("No one is on shift, stopping ticket processing")
                        break
                    success = self.process_exam_readiness_ticket(ticket, team_config, assignee_name)
                else:
                    # Generic processing for other teams
                    assignee_sys_id = self.lookup_user_sys_id(assignee_name, team_key)
                    if not assignee_sys_id:
                        logger.error(f"Could not find sys_id for assignee: {assignee_name}")
                        success = False
                    else:
                        updates = {
                            'state': TicketState.IN_PROGRESS.value,
                            'category': team_config.category,
                            'assigned_to': assignee_sys_id,
                            'time_worked': '60'
                        }
                        primary_group_id = team_config.get_primary_assignment_group_id()
                        if primary_group_id:
                            updates['assignment_group'] = primary_group_id
                        success = self.update_ticket(ticket['sys_id'], updates)
                
                if success:
                    stats["assigned"] += 1
                    logger.info(f"Assigned ticket {ticket['number']} to {assignee_name}")
                else:
                    stats["errors"] += 1
                    
            except Exception as e:
                logger.error(f"Error processing ticket {ticket.get('number', 'unknown')}: {e}")
                stats["errors"] += 1
                
        return stats

    def _resolve_team_keys(self, team_key: str) -> List[str]:
        """Resolve *team_key* to a list of concrete team keys.

        If *team_key* matches a registered team directly, return it as-is.
        Otherwise treat it as a prefix and return all teams whose key starts
        with ``team_key + "-"``.  This lets a single worker handle a family
        of sub-teams (e.g. ``gls-cx-apac`` expands to ``gls-cx-apac-anz``,
        ``gls-cx-apac-asean``, etc.).
        """
        if team_key in self.teams:
            return [team_key]
        prefix = team_key + "-"
        sub = sorted(k for k in self.teams if k.startswith(prefix) and "-audit-" not in k)
        return sub if sub else [team_key]

    def run_continuous_assignment(self, team_key: str, assignee_name: str = None, interval_seconds: int = 60):
        """Run continuous auto-assignment for a team (or team group)"""
        sub_teams = self._resolve_team_keys(team_key)
        logger.info(f"Starting continuous auto-assignment for: {sub_teams}")
        
        # Preload team data once at the start for efficient lookups
        self.preload_team_data(sub_teams)
        
        while True:
            for sub_key in sub_teams:
                try:
                    stats = self.run_auto_assignment(sub_key, assignee_name)
                    if stats["assigned"] > 0 or stats["resolved"] > 0:
                        logger.debug(f"Assignment cycle complete for {sub_key}: {stats}")
                except Exception as e:
                    logger.error(f"Error in assignment cycle for {sub_key}: {e}")
                
            time.sleep(interval_seconds) 