# LX Toolbox

Automation tools for various work tasks including lab operations, ServiceNow, and Jira.

## Features

- **Lab Operations**: Create, start, stop, delete, and manage labs
- **QA Automation**: Run automated QA tests on lab exercises
- **User Impersonation**: Switch to different users for testing
- **Multi-Environment Support**: Works with ROL, ROL-Stage, and China environments
- **ServiceNow Auto-Assignment**: Automated ticket assignment with team-specific configurations
- **LMS Integration**: User name lookups via LMS API
- **SNOW AI Processor**: LLM-powered ticket classification, analysis, and Jira ticket preparation using Ollama
- **Link Checker**: Validate external links in course content with PDF/JSON reports and Jira integration

## Prerequisites

- Python 3.8+
- Firefox or Chrome browser
- Geckodriver (for Firefox) or Chromedriver (for Chrome)
- Podman or Docker (for Link Checker - optional but recommended for faster link validation)

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

### Link Checker Commands

The Link Checker validates all external links in course content (References sections) and generates comprehensive reports.

**Basic Usage:**

```bash
# Check links in a specific course
./scripts/lx-tool lab check-links --course do280-4.18

# Check all available versions of a course
./scripts/lx-tool lab check-links --course rh124-9.3 --all-versions

# Check all courses in the catalog
./scripts/lx-tool lab check-links
```

**Key Features:**

- **Automatic link validation**: Checks all external links in course References sections
- **Screenshot capture**: Takes screenshots of each visited link (including error pages)
- **Comprehensive reports**: Generates both PDF and JSON reports
- **Retry mechanism**: Automatically retries failed links before final report
- **Multi-version support**: Can check all versions of a course
- **Jira integration**: Optionally creates prefilled Jira tickets for broken links
- **Fast validation**: Uses containerized `linkchecker` tool for quick checks when screenshots aren't needed

**Report Structure:**

- **PDF Report**: Includes course summary, broken links with hyperlinks, embedded screenshots, and detailed section-by-section breakdown
- **JSON Report**: Machine-readable format for alternative validation methods
- **Screenshots**: Organized by run timestamp, course name, version, and section name

**Command Options:**

- `--course, -c`: Specific course ID to check (e.g., `rh124-9.3`). Checks all courses if omitted
- `--env, -e`: Environment (rol, rol-stage, china). Default: rol
- `--browser, -b`: Browser to use (firefox, chrome). Default: firefox
- `--headless/--no-headless`: Run browser in headless mode. Default: no-headless
- `--output-dir, -d`: Directory to save reports. Default: current directory
- `--screenshots/--no-screenshots`: Take screenshots of visited links. Default: screenshots enabled
- `--screenshots-dir, -s`: Directory to save screenshots. Default: `./link_checker_screenshots`
- `--retry/--no-retry`: Retry failed links before generating report. Default: retry enabled
- `--all-versions/--single-version`: Check all available versions of the course. Default: single version
- `--create-jira/--no-jira`: Create prefilled Jira tickets for broken links. Default: disabled

**Examples:**

```bash
# Quick check without screenshots (faster)
./scripts/lx-tool lab check-links --course do280-4.18 --no-screenshots

# Check all versions with custom output directory
./scripts/lx-tool lab check-links --course rh124-9.3 --all-versions --output-dir ./reports

# Check all courses and create Jira tickets for broken links
./scripts/lx-tool lab check-links --create-jira

# Custom screenshots directory
./scripts/lx-tool lab check-links --course do280-4.18 --screenshots-dir ./my_screenshots

# Skip retry for faster execution
./scripts/lx-tool lab check-links --course do280-4.18 --no-retry

# Headless mode for automated runs
./scripts/lx-tool lab check-links --course do280-4.18 --headless
```

**How It Works:**

1. **Navigation**: Logs into ROL and navigates to the course(s)
2. **Section Discovery**: Expands the table of contents and collects all sections (excluding summaries, labs, quizzes, etc.)
3. **Link Extraction**: Extracts all external links from References sections
4. **Link Validation**: 
   - Uses containerized `linkchecker` for fast validation (when screenshots disabled)
   - Uses Selenium for validation with screenshots
   - Supports parallel checking for faster execution
5. **Retry Round**: Optionally retries failed links to catch transient errors
6. **Report Generation**: Creates PDF (with screenshots) and JSON reports
7. **Jira Integration**: Optionally creates prefilled Jira tickets with broken links, hyperlinks to course sections, and attached reports

**Screenshot Organization:**

Screenshots are organized in a hierarchical structure:
```
link_checker_screenshots/
└── 20251228_143022/          # Run timestamp
    └── do280/                 # Course name
        └── 4.18/             # Version
            └── Section_1.2/  # Section name
                └── screenshot_*.png
```

**Jira Ticket Features:**

When `--create-jira` is enabled, the tool creates prefilled Jira tickets with:
- Course information and version
- Table of broken links with clickable section hyperlinks
- Links to search for existing tickets (prevents duplicates)
- Attached PDF and JSON reports
- Opens in a new window to avoid interfering with link checking

**Excluded Sections:**

The following section types are automatically excluded from checking:
- Sections containing "summary" in the title
- Sections containing "lab:", "guided exercise:", "quiz:" in the title
- "Comprehensive Review" sections
- "Preface" sections

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

### Authentication and Login

**Important:** Login is **manual** and requires user interaction. The automation tools provide minimal assistance:
- **Autofills usernames** to reduce repetitive typing tasks
- Opens the browser and navigates to login pages

**2FA/OTP Policy:**
- Two-factor authentication (2FA) and OTP codes **cannot be automated** per company policy
- Users must manually enter their 2FA/OTP codes when prompted during login
- The automation will pause and wait for manual completion of the authentication process

### Quick Setup

1. Create a `.env` file with your credentials:
   ```
   RH_USERNAME=your_username
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

## SNOW AI Processor

The SNOW AI Processor uses a local LLM (Ollama) to analyze and classify ServiceNow feedback tickets, automatically preparing Jira tickets for content or environment issues.

### Prerequisites

- **Ollama**: Install from [ollama.ai](https://ollama.ai)
- **LLM Model**: Download a model (e.g., `ollama pull gemma3:12b`)

### Configuration

Add the following to your `.env` file:

```bash
# LLM Provider (default: ollama)
LLM_PROVIDER=ollama

# Ollama Configuration
OLLAMA_MODEL=gemma3:12b
OLLAMA_COMMAND=/usr/local/bin/ollama

# Your signature for responses
SIGNATURE_NAME=Your Name
```

### CLI Commands

```bash
# Process all tickets in your feedback queue
./scripts/lx-tool snowai

# Process specific tickets
./scripts/lx-tool snowai -t RITM0123456 -t RITM0123457

# Use Chrome instead of Firefox
./scripts/lx-tool snowai --browser chrome

# Run in a specific environment
./scripts/lx-tool snowai --env rol-stage
```

Opens a separate browser window per ticket with organized tabs for efficient processing.

### How It Works

The SNOW AI Processor follows this workflow for each ticket:

1. **Ticket Retrieval**: Fetches ticket details from ServiceNow (description, course, chapter, section, URL)

2. **Classification**: Uses the LLM to classify the ticket as:
   - **Content Issue**: Typos, incorrect instructions, missing steps, outdated content
   - **Environment Issue**: Lab script failures, stuck labs, platform limitations
   - **Manual Review**: UI suggestions, complaints, complex issues

3. **Language Detection & Translation**: Detects non-English feedback and translates it

4. **Guide Text Extraction**: Navigates to the course section in ROL and extracts the relevant content

5. **Analysis**: Uses the LLM to:
   - Compare student feedback with the guide text
   - Determine if the issue is valid
   - Suggest corrections
   - Generate a Jira-ready title

6. **Response Generation**: Crafts a professional response to the student

7. **Jira Preparation**: Opens a prefilled Jira ticket with:
   - Course and section information
   - Translated issue description
   - Suggested workaround
   - Component and version pre-selected

### Browser Window Layout

Each ticket gets its own browser window with 4 tabs:

| Tab | Content |
|-----|---------|
| **Tab 1** | ServiceNow ticket (zoomed for readability) |
| **Tab 2** | ROL course section (guide content) |
| **Tab 3** | Jira search (find similar existing tickets) |
| **Tab 4** | Jira create (prefilled new ticket form) |

This layout enables quick context switching and manual review before submitting tickets.

### Supported Issue Types

**Content Issues** (automatically creates Jira):
- Typos or grammatical errors
- Incorrect or outdated instructions
- Missing steps or commands
- Mismatches between video and guide
- Incomplete exercise information

**Environment Issues** (suggests debugging steps):
- Lab start/finish script failures
- Labs stuck in starting/stopping state
- Platform-specific limitations (e.g., vim visual mode)
- OpenShift lab first-boot delays

**Manual Review Required** (flags for human attention):
- UI improvement suggestions
- Platform complaints or praises
- Content not appearing issues
- Complex multi-faceted problems

### Example Output

```
RITM0123456: Student reports typo in chapter 3 | content_issue=True env_issue=False
✓ Opened windows/tabs. You can now work each ticket in its own window.
```

## Project Structure

```
lx-toolbox/
├── lx_toolbox/          # Main Python package
│   ├── core/            # Core business logic
│   │   ├── lab_manager.py
│   │   ├── link_checker.py
│   │   ├── servicenow_handler.py
│   │   ├── snow_ai_processor.py
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

## Troubleshooting

If you encounter "Username or password not configured":
1. Ensure `.env` file exists in the project root
2. Check variable names are correct (case-sensitive)
3. Verify no spaces around `=` in `.env` file
4. Run `./scripts/lx-tool config` to check configuration

For ServiceNow issues:
1. Test connections with `./scripts/lx-tool snow test`
2. Verify ServiceNow credentials and permissions
3. Check that assignment group IDs are correct

For SNOW AI Processor issues:
1. Verify Ollama is running: `ollama list`
2. Test the model directly: `ollama run gemma3:12b "Hello"`
3. Check `OLLAMA_COMMAND` path in `.env` is correct
4. Ensure the model specified in `OLLAMA_MODEL` is downloaded
5. For slow responses, consider a smaller model (e.g., `qwen3:8b`)

## Contributing

1. Create a feature branch
2. Make your changes
3. Add tests if applicable
4. Submit a pull request


