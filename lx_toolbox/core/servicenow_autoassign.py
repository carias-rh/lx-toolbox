import os
import re
import time
import json
import logging
import requests
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from ..utils.config_manager import ConfigManager
from ..utils.helpers import step_logger
from .servicenow_constants import (
    DEFAULT_TARGET_STATES,
    TicketState,
    rht_task_table_url,
)

# Configure logging with environment fallback only if no handlers exist yet
if not logging.getLogger().hasHandlers():
    _env_log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    _numeric_level = getattr(logging, _env_log_level, logging.INFO)
    logging.basicConfig(level=_numeric_level, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

_SURNAME_PARTICLES = frozenset({
    "da", "das", "de", "del", "della", "di", "do", "dos",
    "van", "von", "ten", "ter", "le", "la", "du",
})


def _name_tokens(value: str) -> List[str]:
    return [token for token in re.split(r"[^\w'-]+", value.strip().lower()) if token]


def _first_surname(full_name: str) -> str:
    """Given name plus optional particle: 'Jordi Sola Alaball' → 'Sola', 'Ricardo Da Costa' → 'Da Costa'."""
    tokens = _name_tokens(full_name)
    if len(tokens) < 2:
        return tokens[0] if tokens else ""
    if tokens[1] in _SURNAME_PARTICLES and len(tokens) >= 3:
        return f"{tokens[1]} {tokens[2]}"
    return tokens[1]


def _contains_consecutive(haystack: List[str], needle: List[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    span = len(needle)
    return any(haystack[i:i + span] == needle for i in range(len(haystack) - span + 1))


def _extract_auto_resolve_reporter(description: str) -> Optional[str]:
    """Reporter from a Jira-notification ticket: 'Reporter:' line or 'Hello {name},' greeting."""
    if not description:
        return None
    reporter_match = re.search(r"Reporter:\s*(.+)", description)
    if reporter_match:
        return reporter_match.group(1).strip()
    hello_match = re.search(r"Hello\s+(.+?)\s*,", description, flags=re.IGNORECASE)
    if hello_match:
        return hello_match.group(1).strip()
    return None

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
            self.target_states = list(DEFAULT_TARGET_STATES)
        if self.auto_resolve_reporters is None:
            self.auto_resolve_reporters = []

    def get_primary_assignment_group_id(self) -> Optional[str]:
        """Return the first assignment group id as the primary one for updates."""
        return self.assignment_group_id[0] if self.assignment_group_id else None

    def matches_auto_resolve_reporter(self, reporter: str) -> bool:
        """True if reporter is a configured auto-resolve name or shares its first surname."""
        reporter_tokens = _name_tokens(reporter)
        if not reporter_tokens:
            return False
        reporter_norm = " ".join(reporter_tokens)
        for name in self.auto_resolve_reporters:
            name_tokens = _name_tokens(name)
            if not name_tokens:
                continue
            if reporter_norm == " ".join(name_tokens):
                return True
            surname_tokens = _name_tokens(_first_surname(name))
            if len("".join(surname_tokens)) < 2:
                continue
            if _contains_consecutive(reporter_tokens, surname_tokens):
                return True
        return False

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
            category="RHLS Standard External Support_t2",
            subcategory="Course Content_rses",
            issue_type="Other",
            frontend_shift_manager_url=self.config.get("T2", "FRONTEND_OPENSHIFT_ROUTE"),
            acknowledgment_template=(
                self.config.get("T2", "ACKNOWLEDGMENT_TEMPLATE")
                or "Hi {customer_name},\n\nThanks for submitting your feedback to the Learner Experience Team.\n\nWe are reviewing your message and will get back to you as soon as possible.\n\nBest Regards,\n{assignee_name}\n{team_name}"
            ),
            auto_resolve_reporters=[
                "Wasim Raja", "Chetan Tiwary", 
                "Samik Sanyal", "Shashi Singh",
                "Carlos Arias", 
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

        Groups that carry an ``email_aliases`` list are schedule-only groups
        used to route tickets whose description contains one of those aliases
        to a sub-region-specific schedule (e.g. EMEA sub-regions that share
        the same ServiceNow assignment group but have different priorities).
        """
        self._gls_cx_team_zone: Dict[str, str] = {}
        self._email_alias_to_team_key: Dict[str, str] = {}
        self._schedule_only_team_keys: set = set()

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

            aliases = group.get("email_aliases", [])
            if aliases:
                for alias in aliases:
                    self._email_alias_to_team_key[alias.strip().lower()] = team_key
                self._schedule_only_team_keys.add(team_key)

            if slug.startswith("audit-"):
                self._schedule_only_team_keys.add(team_key)

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

    def lookup_user_details(self, username: str = None, email: str = None) -> List[Dict[str, Any]]:
        """Look up user details from LMS API, returning all matching records.

        Returns a list of user dicts with fields like fullName, firstName,
        lastName, username, email, etc.  Returns an empty list on failure.
        """
        identifier = (email or username or "").strip()
        if not identifier:
            return []

        token = self.get_lms_token()
        if not token:
            return []

        headers = {'Authorization': f'Bearer {token}', 'Accept': '*/*'}
        base_url = "https://training-lms.redhat.com/ws/user"

        if email:
            url = f"{base_url}/search?q={email}"
        else:
            url = f"{base_url}?username={username}"

        try:
            logger.debug(f"LMS lookup_user_details: requesting {url}")
            response = requests.get(url, headers=headers)
            logger.debug(f"LMS lookup_user_details: status={response.status_code} for {identifier}")

            if response.status_code == 401:
                logger.debug(f"LMS lookup_user_details: token expired for {identifier}, refreshing")
                self._lms_token = None
                token = self.get_lms_token()
                if token:
                    headers = {'Authorization': f'Bearer {token}', 'Accept': '*/*'}
                    response = requests.get(url, headers=headers)
                    logger.debug(f"LMS lookup_user_details: retry status={response.status_code}")
                else:
                    logger.warning(f"LMS lookup_user_details: could not refresh token for {identifier}")
                    return []

            if response.status_code != 200 and username:
                retry_url = f"{base_url}?username=internal_{username}"
                logger.debug(f"LMS lookup_user_details: retrying with internal_ prefix: {retry_url}")
                response = requests.get(retry_url, headers=headers)
                logger.debug(f"LMS lookup_user_details: internal_ retry status={response.status_code}")

            response.raise_for_status()
            data = response.json()
            logger.debug(f"LMS lookup_user_details: raw response for {identifier}: {json.dumps(data, indent=2)}")

            if email:
                entries = data.get('items', [])
                logger.debug(f"LMS lookup_user_details: {len(entries)} entries found for email {email}")
                for i, entry in enumerate(entries):
                    logger.debug(f"  entry[{i}]: {json.dumps(entry, indent=2)}")
                return entries
            else:
                user = data.get('user')
                if user:
                    logger.debug(f"LMS lookup_user_details: user record for {username}: {json.dumps(user, indent=2)}")
                else:
                    logger.debug(f"LMS lookup_user_details: no user record found for {username}")
                return [user] if user else []

        except Exception as e:
            logger.error(f"Error looking up user details for {identifier}: {e}")
            return []

    def _format_lms_work_note(self, lms_entries: List[Dict[str, Any]]) -> str:
        """Format LMS user details into a ServiceNow work note."""
        field_labels = [
            ('fullName', 'Full Name'),
            ('userName', 'RHN ID'),
            ('email', 'Email'),
            ('firstName', 'First Name'),
            ('lastName', 'Last Name'),
        ]
        lines = [f"[LMS Lookup Results] ({len(lms_entries)} entries found)"]
        for idx, entry in enumerate(lms_entries, start=1):
            if len(lms_entries) > 1:
                lines.append(f"--- Entry {idx} ---")
            for key, label in field_labels:
                value = entry.get(key)
                if value:
                    lines.append(f"  {label}: {value}")
        return "\n".join(lines)

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
        
        url = rht_task_table_url(self.instance_url)
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json().get("result", [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching tickets for team {team_key}: {e}")
            return []

    def update_ticket(self, ticket_sys_id: str, updates: Dict[str, Any]) -> bool:
        """Update a ticket with the provided data"""
        url = f"{rht_task_table_url(self.instance_url)}/{ticket_sys_id}"
        
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


    def process_gls_cx_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str, team_key: str = None):
        """Process a GLS Customer Experience ticket: ACK + assign.

        Special routing (checked in order, mutually exclusive):

        1. **Audit** – short_description contains "Audit Request received" →
           assignee comes from the zone-level ``audit-<zone>`` schedule.
        2. **Email alias** – description contains a known
           ``training-*@redhat.com`` alias → assignee comes from the
           matching sub-region schedule.
        3. **Standard** – use the pre-resolved *assignee_name* from the
           group's own schedule.

        When a special-routing match is found but nobody is on shift for
        that schedule the ticket is skipped (returns None) so it stays
        unassigned until someone comes on shift.
        Returns the assignee name on success.
        """
        try:
            short_desc = ticket.get('short_description', '')

            commit_config = team_config
            if 'Audit Request received' in short_desc and team_key:
                audit_assignee = self._resolve_audit_assignee(team_key)
                if audit_assignee and audit_assignee != "None":
                    logger.info(f"Audit ticket {ticket.get('number')} routed to audit assignee: {audit_assignee}")
                    assignee_name = audit_assignee
                    zone_id = self._gls_cx_team_zone.get(team_key)
                    audit_team_key = f"gls-cx-{zone_id}-audit-{zone_id}" if zone_id else None
                    if audit_team_key and audit_team_key in self.teams:
                        commit_config = self.teams[audit_team_key]
                else:
                    logger.debug(f"Audit ticket {ticket.get('number')} – no audit person on shift, skipping")
                    return None

            elif team_key and self._email_alias_to_team_key:
                description = ticket.get('description', '')
                alias_assignee, matched_alias = self._resolve_email_alias_assignee(description)
                if matched_alias:
                    if alias_assignee and alias_assignee != "None":
                        logger.info(f"Ticket {ticket.get('number')} matched alias '{matched_alias}' routed to {alias_assignee}")
                        assignee_name = alias_assignee
                        alias_team_key = self._email_alias_to_team_key.get(matched_alias)
                        if alias_team_key and alias_team_key in self.teams:
                            commit_config = self.teams[alias_team_key]
                    else:
                        logger.debug(f"Ticket {ticket.get('number')} matched alias '{matched_alias}' but no one on sub-region shift, skipping")
                        return None

            if assignee_name == "None":
                return None

            assignee_sys_id = self.lookup_user_sys_id(assignee_name, team_config.team_name)
            if not assignee_sys_id:
                logger.error(f"Could not find sys_id for assignee: {assignee_name}")
                return None

            customer_name = ""
            lms_work_note = ""
            email = ticket.get('u_email_from_address', '').strip()
            if email:
                lms_entries = self.lookup_user_details(email=email)
                if lms_entries:
                    first_match = lms_entries[0]
                    full_name = first_match.get('fullName')
                    if not full_name:
                        first = first_match.get('firstName', '').capitalize()
                        last = first_match.get('lastName', '').capitalize()
                        full_name = f"{first} {last}".strip()
                    if full_name and full_name != email:
                        customer_name = full_name
                    lms_work_note = self._format_lms_work_note(lms_entries)
                if not customer_name:
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
            if lms_work_note:
                updates['work_notes'] = lms_work_note
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
                return None
            time.sleep(1)

            # Phase 2: Assign
            if not self.update_ticket(ticket['sys_id'], {'assigned_to': assignee_sys_id}):
                logger.error(f"Failed to assign ticket {ticket['number']} to {assignee_name}")
                return None
            # Log here so the name matches audit/alias resolution; run_auto_assignment's
            # assignee_name is only the default zone shift and is wrong for those paths.
            logger.info(f"[{team_config.team_name}] Assigned ticket {ticket['number']} to {assignee_name}")
            return assignee_name, commit_config
        except Exception as e:
            logger.error(f"Error processing GLS CX ticket {ticket.get('number', 'unknown')}: {e}")
            return None

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


    def _auto_resolve_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, reporter: str) -> bool:
        logger.info(f"Auto-resolving ticket {ticket['number']} for reporter {reporter}")
        updates = {
            'state': TicketState.RESOLVED.value,
            'category': team_config.category,
            'subcategory': team_config.subcategory,
            'issue': team_config.issue_type,
            'time_worked': '60'
        }
        return self.update_ticket(ticket['sys_id'], updates)

    def auto_resolve_tickets_by_reporter(self, team_key: str) -> int:
        """Auto-resolve Jira-notification tickets whose reporter is on the team allow-list."""
        team_config = self.teams.get(team_key)
        if not team_config or not team_config.auto_resolve_reporters:
            return 0

        # Parentheses around ^OR are rejected by this table ACL. ^NQ ORs two full AND queries.
        state_active = f"stateIN{','.join(team_config.target_states)}^active=true"
        assignment_group_id = team_config.get_primary_assignment_group_id()
        group_filter = f"assignment_group={assignment_group_id}^" if assignment_group_id else ""
        sysparm_query = (
            f"{group_filter}{state_active}^short_descriptionLIKEnew Jira"
            f"^NQ{group_filter}{state_active}^short_descriptionLIKEWeve received your request"
            "^ORDERBYDESCsys_created_on"
        )

        params = {
            "sysparm_query": sysparm_query,
            "sysparm_fields": "sys_id,number,description,short_description",
            "sysparm_limit": "100"
        }

        url = rht_task_table_url(self.instance_url)

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            tickets = response.json().get("result", [])

            resolved_count = 0
            for ticket in tickets:
                reporter = _extract_auto_resolve_reporter(ticket.get('description', ''))
                if reporter and team_config.matches_auto_resolve_reporter(reporter):
                    if self._auto_resolve_ticket(ticket, team_config, reporter):
                        resolved_count += 1
                elif reporter:
                    logger.debug(
                        f"Skipping auto-resolve for {ticket.get('number')} reporter={reporter!r}"
                    )

            return resolved_count

        except Exception as e:
            logger.error(f"Error auto-resolving tickets: {e}")
            return 0


    def peek_shift(self, team_config: TeamConfig) -> Optional[str]:
        """Who should receive the next item. Does not consume a Round-Robin turn."""
        url = f"{team_config.frontend_shift_manager_url}/api/shift"
        params = {}
        if team_config.frontend_group_param:
            params["group"] = team_config.frontend_group_param
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            name = data.get("name")
            if name and name != "None":
                logger.debug(
                    f"[{team_config.team_name}] Peek assignee: {name} "
                    f"| group={params.get('group', '-')}"
                )
                return name
            logger.debug(f"[{team_config.team_name}] Peek: nobody on shift")
            return "None"
        except Exception as e:
            logger.error(f"[{team_config.team_name}] Shift peek failed: {e}")
            return None

    def commit_shift(self, team_config: TeamConfig, assigned_name: str) -> Optional[str]:
        """Record who actually received the work. Returns who is next."""
        if not assigned_name or assigned_name == "None":
            return None
        url = f"{team_config.frontend_shift_manager_url}/api/shift/commit"
        params = {}
        if team_config.frontend_group_param:
            params["group"] = team_config.frontend_group_param
        try:
            response = requests.post(url, params=params, json={"assigned_name": assigned_name})
            response.raise_for_status()
            next_name = response.json().get("name")
            logger.info(
                f"[{team_config.team_name}] Committed {assigned_name} "
                f"| next={next_name or '-'} | group={params.get('group', '-')}"
            )
            return next_name
        except Exception as e:
            logger.error(f"[{team_config.team_name}] Shift commit failed: {e}")
            return None

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

        return self.peek_shift(audit_config)

    def _resolve_email_alias_assignee(self, description: str):
        """Match a ``training-*@redhat.com`` alias in the ticket description to a sub-region schedule.

        Scans *description* for any known email alias and queries the matching
        sub-region's frontend schedule for the current on-shift person.

        Returns ``(assignee_name, matched_alias)`` when an alias is found, or
        ``(None, None)`` when no alias matches.
        """
        if not self._email_alias_to_team_key:
            return None, None

        desc_lower = description.lower()
        for alias, team_key in self._email_alias_to_team_key.items():
            if alias in desc_lower:
                config = self.teams.get(team_key)
                if config:
                    assignee = self.peek_shift(config)
                    return assignee, alias
                return None, alias
        return None, None

    def run_auto_assignment(self, team_key: str, assignee_name: str = None) -> Dict[str, int]:
        """Run auto-assignment for a specific team"""
        team_config = self.teams.get(team_key)
        if not team_config:
            raise ValueError(f"Unknown team: {team_key}")

        tname = team_config.team_name
        stats = {"assigned": 0, "resolved": 0, "skipped": 0, "errors": 0}
        use_peek_commit = bool(team_config.frontend_shift_manager_url)

        #if team_config.auto_resolve_reporters:
        #    stats["resolved"] = self.auto_resolve_tickets_by_reporter(team_key)

        tickets = self.get_unassigned_tickets(team_key)
        if not tickets:
            logger.debug(f"[{tname}] No unassigned tickets | assignee={assignee_name}")
            return stats

        if use_peek_commit:
            assignee_name = self.peek_shift(team_config)

        if not assignee_name and team_config.frontend_shift_manager_url:
            logger.info(f"[{tname}] No assignee available — skipping assignment cycle")
            return stats
        elif not assignee_name and not team_config.frontend_shift_manager_url:
            assignee_name = "only-ack"
            logger.debug(f"[{tname}] No frontend configured — ACK-only mode")

        for ticket in tickets:
            try:
                success = False
                committed_name = assignee_name
                cx_commit_config = team_config
                if "t1" in team_key:
                    if assignee_name == "None":
                        logger.debug("No one is on shift, stopping ticket processing")
                        break
                    success = self.process_t1_ticket(ticket, team_config, assignee_name)

                elif "t2" in team_key:
                    reporter = _extract_auto_resolve_reporter(ticket.get('description', ''))
                    if reporter and team_config.matches_auto_resolve_reporter(reporter):
                        if self._auto_resolve_ticket(ticket, team_config, reporter):
                            stats["resolved"] += 1
                        else:
                            stats["errors"] += 1
                        continue
                    if assignee_name == "None":
                        logger.debug("No one is on shift, stopping ticket processing")
                        break
                    success = self.process_t2_ticket(ticket, team_config, assignee_name)
                elif "gls-rhls-engagement" in team_key:
                    success = self.process_gls_rhls_engagement_ticket(ticket, team_config, assignee_name)
                elif "gls-cx" in team_key:
                    if assignee_name == "None":
                        short_desc = ticket.get('short_description', '')
                        desc = ticket.get('description', '').lower()
                        is_audit = 'Audit Request received' in short_desc
                        is_alias = any(a in desc for a in self._email_alias_to_team_key)
                        if not is_audit and not is_alias:
                            stats["skipped"] += 1
                            continue
                    cx_result = self.process_gls_cx_ticket(ticket, team_config, assignee_name, team_key=team_key)
                    if not cx_result:
                        stats["skipped"] += 1
                        continue
                    success = True
                    committed_name, cx_commit_config = cx_result
                elif "exam" in team_key:
                    if assignee_name == "None":
                        logger.debug("No one is on shift, stopping ticket processing")
                        break
                    success = self.process_exam_readiness_ticket(ticket, team_config, assignee_name)
                else:
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
                    if "gls-cx" not in team_key:
                        logger.info(f"[{tname}] Assigned ticket {ticket['number']} to {committed_name}")
                    if use_peek_commit:
                        commit_config = cx_commit_config if "gls-cx" in team_key else team_config
                        next_name = self.commit_shift(commit_config, committed_name)
                        if commit_config is team_config and next_name and next_name != "None":
                            assignee_name = next_name
                else:
                    stats["errors"] += 1

            except Exception as e:
                logger.error(f"Error processing ticket {ticket.get('number', 'unknown')}: {e}")
                stats["errors"] += 1

        if stats["skipped"]:
            logger.debug(
                f"[{tname}] {stats['skipped']} ticket(s) skipped (no eligible assignee on shift)"
            )
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
        sub = sorted(k for k in self.teams if k.startswith(prefix) and k not in self._schedule_only_team_keys)
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