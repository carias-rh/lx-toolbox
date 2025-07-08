# Team Configuration Guide

This document explains how to configure and extend the ServiceNow auto-assignment system for different teams.

## Overview

The ServiceNow auto-assignment system uses a flexible, team-based configuration approach that eliminates the need for separate template files per team. Instead, team-specific behavior is defined through Python dataclasses that can be easily extended.

## Architecture Benefits

### Before (Jinja2 Templates)
- Required separate `.j2` template files for each team
- Conditional logic scattered throughout templates
- Hard to maintain and extend
- Risk of code duplication

### After (Team Configuration Classes)
- Single codebase with team-specific configuration
- Clean separation of logic and configuration
- Easy to add new teams without code duplication
- Type-safe configuration with validation

## TeamConfig Class

```python
@dataclass
class TeamConfig:
    team_name: str                          # Display name for the team
    assignment_group_id: str                # ServiceNow assignment group sys_id
    category: str                           # Default ticket category
    subcategory: Optional[str] = None       # Optional subcategory
    issue_type: Optional[str] = None        # Optional issue type
    target_states: List[str] = None         # States to query for unassigned tickets
    auto_resolve_reporters: List[str] = None # Reporters whose tickets auto-resolve
    acknowledgment_template: str = ""       # Template for customer acknowledgment
    enable_round_robin: bool = False        # Enable round-robin assignment
    round_robin_api_url: Optional[str] = None # API URL for round-robin service
```

## Current Team Configurations

### T1 Team (RHT Learner Experience)

```python
teams["t1"] = TeamConfig(
    team_name="RHT Learner Experience",
    assignment_group_id="5afc8ba24f8cf6004db6022f0310c70a",
    category="RHLS Standard Support",
    acknowledgment_template="""Hi {customer_name},

Thanks for contacting Red Hat Online Learning support team.

We have received your request and working on it, will update you at the earliest.

Best Regards,
{assignee_name} 
Red Hat Training Technical Support""",
    enable_round_robin=True,
    round_robin_api_url=config.get("T1", "T1_FRONTEND_OPENSHIFT_ROUTE")
)
```

**Special Processing:**
- Handles empty description/email fields
- Converts `@iqlaserpress.net` emails to `lywillia@redhat.com`
- Skips acknowledgment for `[training-feedback]` tickets
- Supports round-robin assignment from external API

### T2 Team (RHT Learner Experience - T2)

```python
teams["t2"] = TeamConfig(
    team_name="RHT Learner Experience - T2",
    assignment_group_id="974cb3e01bc31c50c57c3224cc4bcbfe", 
    category="RHLS Basic Internal Support",
    subcategory="Course Content",
    issue_type="Other",
    acknowledgment_template="""Hi {customer_name},

Thanks for submitting your feedback to the Learner Experience Team.
        
We are reviewing your message and will get back to you as soon as possible.

Best Regards,
{assignee_name} 
{team_name}""",
    auto_resolve_reporters=[
        "gls-ftaylor", "lauber", "rht-jordisola", 
        # ... additional reporters
    ]
)
```

**Special Processing:**
- Extracts user information from ticket description
- Performs LMS API lookups for full names
- Auto-resolves tickets from specific reporters
- Extracts course/version information for project codes
- Creates formatted short descriptions

## Adding a New Team

### Step 1: Add Team Configuration

Edit `lx_toolbox/core/servicenow_handler.py` in the `_load_team_configurations()` method:

```python
def _load_team_configurations(self) -> Dict[str, TeamConfig]:
    teams = {}
    
    # ... existing teams ...
    
    # New team configuration
    teams["t3"] = TeamConfig(
        team_name="Your New Team Name",
        assignment_group_id="your_assignment_group_sys_id",
        category="Your Default Category",
        subcategory="Your Subcategory",  # Optional
        issue_type="Your Issue Type",    # Optional
        acknowledgment_template="""Hi {customer_name},

Your custom acknowledgment message here.

Best Regards,
{assignee_name}
{team_name}""",
        auto_resolve_reporters=["reporter1", "reporter2"],  # Optional
        enable_round_robin=False,  # Set to True if needed
        target_states=["1", "2", "-2"]  # Custom states if needed
    )
    
    return teams
```

### Step 2: Add Custom Processing (Optional)

If your team needs special processing logic, add a new method:

```python
def process_t3_ticket(self, ticket: Dict[str, Any], team_config: TeamConfig, assignee_name: str) -> bool:
    """Process a T3 team ticket with custom logic"""
    try:
        description = ticket.get('description', '')
        updates = {
            'state': TicketState.IN_PROGRESS.value,
            'category': team_config.category,
            'subcategory': team_config.subcategory,
            'issue': team_config.issue_type,
            'assignment_group': team_config.assignment_group_id,
            'assigned_to': assignee_name,
            'time_worked': '60'
        }
        
        # Add your custom processing logic here
        # For example, extract special fields from description
        if "Priority: High" in description:
            updates['priority'] = '1'  # Set high priority
            
        # Add acknowledgment
        customer_name = ticket.get('contact_source', 'Customer')
        ack_message = team_config.acknowledgment_template.format(
            customer_name=customer_name,
            assignee_name=assignee_name,
            team_name=team_config.team_name
        )
        updates['comments'] = ack_message
        
        return self.update_ticket(ticket['sys_id'], updates)
        
    except Exception as e:
        logger.error(f"Error processing T3 ticket {ticket.get('number', 'unknown')}: {e}")
        return False
```

### Step 3: Update Main Assignment Logic

In the `run_auto_assignment()` method, add your team handling:

```python
# Process each ticket
for ticket in tickets:
    try:
        success = False
        if team_key == "t1":
            success = self.process_t1_ticket(ticket, team_config, assignee_name)
        elif team_key == "t2":
            success = self.process_t2_ticket(ticket, team_config, assignee_name)
        elif team_key == "t3":  # Add your new team
            success = self.process_t3_ticket(ticket, team_config, assignee_name)
        else:
            # Generic processing for other teams
            success = self.process_generic_ticket(ticket, team_config, assignee_name)
```

### Step 4: Update CLI Options

In `lx_toolbox/main.py`, update the team choices:

```python
@click.argument('team', type=click.Choice(['t1', 't2', 't3']))  # Add t3
```

### Step 5: Add Configuration (If Needed)

If your team needs special configuration variables:

1. Add to `config/config.ini.example`:
   ```ini
   [T3]
   # T3 team specific configuration
   special_api_url = https://your-api.example.com
   ```

2. Add to `env.template`:
   ```bash
   # T3 Team Configuration
   T3_API_KEY=your_api_key
   ```

## Advanced Configuration

### Custom Target States

If your team uses different ticket states:

```python
teams["custom"] = TeamConfig(
    # ... other config ...
    target_states=["1", "2", "10", "11"],  # Custom state list
)
```

### Complex Acknowledgment Templates

Use more sophisticated templates with conditional logic:

```python
def get_acknowledgment_template(self, ticket_type: str) -> str:
    if ticket_type == "urgent":
        return """Hi {customer_name},
        
This is an urgent request and has been escalated.
Our team will contact you within 2 hours.

Best Regards,
{assignee_name}"""
    else:
        return self.default_template
```

### Integration with External APIs

Add methods to integrate with other systems:

```python
def get_external_assignee(self, team_config: TeamConfig) -> str:
    """Get assignee from external scheduling system"""
    try:
        response = requests.get(f"{team_config.external_api}/next-assignee")
        return response.json()["assignee"]
    except Exception:
        return self.config.get("General", "default_assignee")
```

## Best Practices

### 1. Use Environment Variables for Sensitive Data
```python
# Good
api_url = self.config.get("T3", "T3_API_URL")

# Bad  
api_url = "https://hardcoded-url.com"
```

### 2. Provide Fallback Values
```python
assignee = self.get_round_robin_assignee(team_config) or \
           self.config.get("General", "default_assignee") or \
           "fallback_user"
```

### 3. Add Comprehensive Error Handling
```python
try:
    # Team-specific processing
    result = self.custom_processing(ticket)
except SpecificException as e:
    logger.warning(f"Non-critical error: {e}")
    # Continue with generic processing
except Exception as e:
    logger.error(f"Critical error: {e}")
    return False
```

### 4. Log Important Actions
```python
logger.info(f"Assigned ticket {ticket['number']} to {assignee_name} (team: {team_key})")
logger.debug(f"Applied updates: {updates}")
```

## Testing New Configurations

### 1. Test Connection
```bash
./scripts/lx-tool snow test
```

### 2. List Tickets for New Team
```bash
./scripts/lx-tool snow list-tickets t3 --limit 5
```

### 3. Run Single Assignment (Dry Run)
```bash
./scripts/lx-tool snow assign t3 --assignee "test_user"
```

### 4. Monitor Logs
Check the logs for any errors or unexpected behavior during processing.

## Migration from Templates

When migrating from Jinja2 templates:

1. **Extract Configuration**: Move team-specific values to `TeamConfig`
2. **Convert Logic**: Transform Jinja2 conditionals to Python methods
3. **Preserve Behavior**: Ensure the new code produces identical results
4. **Test Thoroughly**: Compare old vs new behavior with real tickets
5. **Document Changes**: Update team documentation

## Troubleshooting

### Common Issues

**Team Not Found**
```
ValueError: Unknown team: t3
```
Solution: Ensure team key is added to `_load_team_configurations()`

**Missing Assignment Group**
```
Error updating ticket: Invalid assignment group
```
Solution: Verify `assignment_group_id` is correct in ServiceNow

**API Authentication Failed**
```
Error getting round robin assignee: 401 Unauthorized
```
Solution: Check API credentials and permissions

### Debug Mode

Enable debug logging to see detailed processing:

```python
logging.getLogger().setLevel(logging.DEBUG)
```

This will show:
- API requests and responses
- Field extractions and transformations
- Decision points in processing logic

## Future Enhancements

Potential improvements to the team configuration system:

1. **Dynamic Configuration**: Load team configs from external files or databases
2. **Workflow Engine**: Define complex assignment workflows per team  
3. **Machine Learning**: Automatic categorization and routing
4. **Metrics Dashboard**: Track team performance and assignment statistics
5. **A/B Testing**: Compare different assignment strategies 