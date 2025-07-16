# LX Toolbox

Automation tools for various work tasks including lab operations, ServiceNow, and Jira.

## Features

- **Lab Operations**: Create, start, stop, delete, and manage labs
- **QA Automation**: Run automated QA tests on lab exercises
- **User Impersonation**: Switch to different users for testing
- **Multi-Environment Support**: Works with ROL, ROL-Stage, and China environments
- **ServiceNow Auto-Assignment**: Automated ticket assignment with team-specific configurations
- **LMS Integration**: User name lookups via LMS API
- **Jira Integration** (Coming Soon)

## Prerequisites

- Python 3.8+
- Firefox or Chrome browser
- Geckodriver (for Firefox) or Chromedriver (for Chrome)

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd lx-toolbox
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up your credentials:
   ```bash
   # Copy the template
   cp env.template .env
   
   # Edit with your credentials
   vim .env  # or use your preferred editor
   ```

4. (Optional) Configure settings:
   ```bash
   cp config/config.ini.example config/config.ini
   # Edit config/config.ini if needed
   ```

## Usage

### Lab Commands

```bash
# Make the script executable (first time only)
chmod +x scripts/lx-tool

# Start a lab
./scripts/lx-tool lab start rh124-9.3

# Stop a lab
./scripts/lx-tool lab stop rh124-9.3

# Create a new lab
./scripts/lx-tool lab create rh124-9.3

# Delete a lab
./scripts/lx-tool lab delete rh124-9.3

# Recreate a lab (delete + create)
./scripts/lx-tool lab recreate rh124-9.3

# Impersonate a user
./scripts/lx-tool lab impersonate rh124-9.3 student01

# Run QA automation
./scripts/lx-tool lab qa rh124-9.3 ch01s02 -f commands.txt
```

### ServiceNow Commands

```bash
# Test ServiceNow and LMS connections
./scripts/lx-tool snow test

# List unassigned tickets for a team
./scripts/lx-tool snow list-tickets t1
./scripts/lx-tool snow list-tickets t2 --limit 20

# Run single auto-assignment cycle
./scripts/lx-tool snow assign t1
./scripts/lx-tool snow assign t2 --assignee "John Doe"

# Run continuous auto-assignment
./scripts/lx-tool snow assign t1 --continuous
./scripts/lx-tool snow assign t2 --continuous --interval 120
```

### Command Options

- `--env, -e` : Specify environment (rol, rol-stage, china)
- `--browser, -b` : Choose browser (firefox, chrome)
- `--headless` : Run in headless mode
- `--no-headless` : Run with browser visible

### Examples

```bash
# Start lab in ROL-stage with Chrome
./scripts/lx-tool lab start rh124-9.3 --env rol-stage --browser chrome

# Delete lab in headless mode
./scripts/lx-tool lab delete rh124-9.3 --headless

# Run QA with custom commands file
./scripts/lx-tool lab qa rh124-9.3 ch01s02 -f my-commands.txt

# Check your configuration
./scripts/lx-tool config

# Auto-assign T1 tickets continuously with 2-minute intervals
./scripts/lx-tool snow assign t1 --continuous --interval 120
```

## Configuration

See [CREDENTIALS.md](CREDENTIALS.md) for detailed information on setting up credentials.

### Quick Setup

1. Create a `.env` file with your credentials:
   ```
   RH_USERNAME=your_username
   RH_PIN=your_pin
   SNOW_INSTANCE_URL=https://redhat.service-now.com
   SNOW_API_USER=your_snow_user
   SNOW_API_PASSWORD=your_snow_password
   ```

2. Test your configuration:
   ```bash
   ./scripts/lx-tool config
   ./scripts/lx-tool snow test
   ```

## ServiceNow Auto-Assignment

The ServiceNow auto-assignment feature supports multiple teams with different configurations:

### Team Configurations

- **T1 Team (`t1`)**: RHT Learner Experience
  - Handles standard support requests
  - Supports round-robin assignment
  - Automatic acknowledgment messages
  - Handles special cases (iqlaserpress.net emails)

- **T2 Team (`t2`)**: RHT Learner Experience - T2  
  - Handles feedback tickets
  - Automatic user name lookup via LMS API
  - Auto-resolution for specific reporters
  - Course information extraction

### Adding New Teams

To add a new team, modify the `_load_team_configurations()` method in `lx_toolbox/core/servicenow_handler.py`:

```python
teams["new_team"] = TeamConfig(
    team_name="Your Team Name",
    assignment_group_id="your_group_sys_id", 
    category="Your Category",
    acknowledgment_template="Your template with {customer_name} and {assignee_name}",
    # ... other configuration options
)
```

## Project Structure

```
lx-toolbox/
├── lx_toolbox/          # Main Python package
│   ├── core/            # Core business logic
│   │   ├── lab_manager.py
│   │   ├── servicenow_handler.py
│   │   └── jira_handler.py (TODO)
│   ├── utils/           # Utility modules
│   │   ├── config_manager.py
│   │   └── helpers.py
│   └── main.py          # CLI entry point
├── config/              # Configuration files
│   ├── config.ini
│   └── config.ini.example
├── scripts/             # Wrapper scripts
│   └── lx-tool
├── tests/               # Test files (TODO)
├── playbooks/           # Legacy Ansible files (to be removed)
├── legacy/              # Legacy files
├── openshift/           # OpenShift deployment files
├── .env                 # Environment variables (credentials)
├── env.template         # Template for .env file
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

## Security Notes

- Never commit credentials to version control
- Use `.env` files for local development
- Add `.env` to your `.gitignore`
- Consider using a secret manager for production use

## Troubleshooting

If you encounter "Username or password/pin not configured":
1. Ensure `.env` file exists in the project root
2. Check variable names are correct (case-sensitive)
3. Verify no spaces around `=` in `.env` file
4. Run `./scripts/lx-tool config` to check configuration

For ServiceNow issues:
1. Test connections with `./scripts/lx-tool snow test`
2. Verify ServiceNow credentials and permissions
3. Check that assignment group IDs are correct

## Contributing

1. Create a feature branch
2. Make your changes
3. Add tests if applicable
4. Submit a pull request

## License

[Your License Here] 