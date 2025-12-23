#!/usr/bin/env python3
"""
LX Toolbox CLI - Main entry point for lab automation tools.
"""

import click
import sys
import os
import logging
from pathlib import Path

from .utils.config_manager import ConfigManager
from .utils.helpers import reset_step_counter
from .core.lab_manager import LabManager
from .core.link_checker import LinkChecker
from .core.servicenow_handler import ServiceNowHandler
from .core.snow_ai_processor import SnowAIProcessor

# Initialize config manager with paths relative to the package
def get_config():
    """Get ConfigManager instance with proper paths."""
    # Find the project root (where config directory should be)
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent  # Go up from lx_toolbox/main.py to project root
    
    config_path = project_root / "config" / "config.ini"
    env_path = project_root / ".env"
    
    # Try config.ini first, fall back to example if not found
    if not config_path.exists():
        config_example_path = project_root / "config" / "config.ini.example"
        if config_example_path.exists():
            click.echo(f"Warning: config.ini not found, using example file: {config_example_path}")
            config_path = config_example_path
    
    return ConfigManager(
        config_file_path=str(config_path),
        env_file_path=str(env_path)
    )

def _setup_logging(log_level: str | None = None):
    """Initialize logging using CLI option or environment variable.

    Priority: CLI --log-level > LOG_LEVEL env var > INFO
    """
    if log_level is None:
        log_level = os.getenv('LOG_LEVEL', 'INFO')
    numeric_level = getattr(logging, str(log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True,
    )


@click.group()
@click.option('--log-level', type=click.Choice(['DEBUG','INFO','WARNING','ERROR','CRITICAL']), default=None, help='Set logging level (overrides LOG_LEVEL env var)')
@click.pass_context
def cli(ctx, log_level):
    """LX Toolbox - Automation tools for lab operations, ServiceNow, and Jira."""
    # Setup logging early
    _setup_logging(log_level)
    # Store config in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj['config'] = get_config()

@cli.group()
@click.pass_context
def lab(ctx):
    """Lab operations commands."""
    pass

@lab.command()
@click.argument('course_id')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def start(ctx, course_id, env, browser, headless):
    """Start a lab for the specified course."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Go to course
        lab_mgr.go_to_course(course_id=course_id, environment=environment)
        
        # Check lab status and start if needed
        primary_status, secondary_status = lab_mgr.check_lab_status()
        
        if primary_status == "CREATE":
            lab_mgr.create_lab(course_id=course_id)
            primary_status, _ = lab_mgr.check_lab_status()
        
        if secondary_status == "START":
            lab_mgr.start_lab(course_id=course_id)
        elif secondary_status in ["STOP", "STOPPING"]:
            click.echo(f"Lab {course_id} is already running or stopping.")
        
        # Increase autostop and lifespan
        lab_mgr.increase_autostop(course_id=course_id)
        lab_mgr.increase_lifespan(course_id=course_id)
        
        click.echo(f"✓ Lab {course_id} started successfully in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error starting lab: {e}", err=True)
        sys.exit(1)

@lab.command()
@click.argument('course_id')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def stop(ctx, course_id, env, browser, headless):
    """Stop a lab for the specified course."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Go to course and stop lab
        lab_mgr.go_to_course(course_id=course_id, environment=environment)
        lab_mgr.stop_lab(course_id=course_id)
        
        click.echo(f"✓ Lab {course_id} stopped successfully in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error stopping lab: {e}", err=True)
        sys.exit(1)

@lab.command()
@click.argument('course_id')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def create(ctx, course_id, env, browser, headless):
    """Create a lab for the specified course."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Go to course and create lab
        lab_mgr.go_to_course(course_id=course_id, environment=environment)
        lab_mgr.create_lab(course_id=course_id)
        
        # Increase lifespan for newly created lab
        lab_mgr.increase_lifespan(course_id=course_id)
        
        click.echo(f"✓ Lab {course_id} created successfully in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error creating lab: {e}", err=True)
        sys.exit(1)

@lab.command()
@click.argument('course_id')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def delete(ctx, course_id, env, browser, headless):
    """Delete a lab for the specified course."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Go to course and delete lab
        lab_mgr.go_to_course(course_id=course_id, environment=environment)
        lab_mgr.delete_lab(course_id=course_id)
        
        click.echo(f"✓ Lab {course_id} deleted successfully in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error deleting lab: {e}", err=True)
        sys.exit(1)

@lab.command()
@click.argument('course_id')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def recreate(ctx, course_id, env, browser, headless):
    """Recreate (delete and create) a lab for the specified course."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Recreate lab
        lab_mgr.recreate_lab(course_id=course_id, environment=environment)
        
        click.echo(f"✓ Lab {course_id} recreated successfully in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error recreating lab: {e}", err=True)
        sys.exit(1)

@lab.command()
@click.argument('course_id')
@click.argument('username')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def impersonate(ctx, course_id, username, env, browser, headless):
    """Impersonate a user for the specified course."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Impersonate user
        lab_mgr.impersonate_user(
            impersonate_username=username,
            current_course_id=course_id,
            environment=environment
        )
        
        click.echo(f"✓ Successfully impersonated {username} for course {course_id} in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error impersonating user: {e}", err=True)
        sys.exit(1)

@lab.command()
@click.argument('course_id')
@click.argument('chapter_section')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='chrome', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=True, help='Run browser in headless mode')
@click.option('--setup-style', '-s', default=None, help='Environment setup style (e.g., rgdacosta)')
@click.option('--commands-file', '-f', default=None, help='File containing commands to execute')
@click.pass_context
def qa(ctx, course_id, chapter_section, env, browser, headless, setup_style, commands_file):
    """Run QA automation for a specific course chapter/section."""
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    try:
        lab_mgr = LabManager(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        lab_mgr.login(environment=environment)
        
        # Go to course
        lab_mgr.go_to_course(course_id=course_id, environment=environment)
        
        # Check and ensure lab is running
        primary_status, _ = lab_mgr.check_lab_status()
        
        if primary_status == "CREATE":
            lab_mgr.create_lab(course_id=course_id)
            primary_status, _ = lab_mgr.check_lab_status()
        
        if primary_status == "START":
            lab_mgr.start_lab(course_id=course_id)
        
        # Increase autostop and lifespan
        lab_mgr.increase_autostop(course_id=course_id)
        lab_mgr.increase_lifespan(course_id=course_id)
        
        # Open workstation console
        lab_mgr.open_workstation_console(course_id=course_id, setup_environment_style=setup_style)
        
        # Get commands (from file or from course materials)
        if commands_file:
            with open(commands_file, 'r') as f:
                commands = f.read().splitlines()
            click.echo(f"Loaded {len(commands)} commands from {commands_file}")
        else:
            # This would need the get_exercise_commands to be implemented
            commands = lab_mgr.get_exercise_commands(course_id=course_id, chapter_section=chapter_section)
            if not commands:
                click.echo("Warning: No commands found for this exercise. Please provide a commands file with -f option.", err=True)
                return
        
        # Run QA
        lab_mgr.run_qa_on_exercise(course_id=course_id, chapter_section=chapter_section, commands=commands)
        
        click.echo(f"✓ QA completed for {course_id} - {chapter_section} in {environment}.")
        click.echo("Browser will remain open for interactive use. Close manually when done.")
        
    except Exception as e:
        click.echo(f"✗ Error running QA: {e}", err=True)
        sys.exit(1)

@lab.command('check-links')
@click.option('--course', '-c', default=None, help='Specific course ID to check (e.g., rh124-9.3). Checks all if omitted.')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=True, help='Run browser in headless mode')
@click.option('--output', '-o', default='text', type=click.Choice(['text', 'json']), help='Output format')
@click.option('--output-file', '-f', default=None, help='Save report to file')
@click.pass_context
def check_links(ctx, course, env, browser, headless, output, output_file):
    """Check links in course content (References sections).
    
    Examples:
    
        lx-tool lab check-links --course do0042l-4.20
        
        lx-tool lab check-links --headless --output json -f report.json
    """
    config = ctx.obj['config']
    environment = env or config.get("General", "default_lab_environment", "rol")
    
    click.echo(f"Link checker starting for environment: {environment}")
    if course:
        click.echo(f"Target course: {course}")
    else:
        click.echo("Target: All courses in catalog")
    click.echo()
    
    try:
        checker = LinkChecker(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        
        # Login
        checker.login(environment=environment)
        
        if course:
            checker.check_course_links(course, environment)
        else:
            checker.check_all_courses(environment)
        
        # Generate report
        report = checker.generate_report(output)
        
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report)
            click.echo(f"\n✓ Report saved to {output_file}")
        else:
            click.echo(report)
        
        # Summary
        total_broken = sum(r.broken_links for r in checker.reports)
        if total_broken > 0:
            click.echo(f"\n⚠ Found {total_broken} broken link(s)", err=True)
            sys.exit(1)
        else:
            click.echo("\n✓ All links valid")
            
    except KeyboardInterrupt:
        click.echo("\n\nInterrupted by user.")
        sys.exit(1)
    except Exception as e:
        click.echo(f"✗ Error checking links: {e}", err=True)
        sys.exit(1)
    finally:
        try:
            checker.close_browser()
        except Exception:
            pass

@cli.group()
@click.pass_context
def snow(ctx):
    """ServiceNow operations commands."""
    pass

@snow.command()
@click.argument('team')
@click.option('--assignee', '-a', default=None, help='Specific assignee (overrides round-robin)')
@click.option('--continuous', '-c', is_flag=True, help='Run continuously')
@click.option('--interval', '-i', default=60, help='Interval in seconds for continuous mode')
@click.pass_context
def assign(ctx, team, assignee, continuous, interval):
    """Auto-assign ServiceNow tickets for a team."""
    config = ctx.obj['config']
    
    try:
        snow_handler = ServiceNowHandler(config)
        
        # Test connection first
        if not snow_handler.test_connection():
            click.echo("✗ Failed to connect to ServiceNow. Check your credentials.", err=True)
            sys.exit(1)
        
        click.echo(f"✓ Connected to ServiceNow")
        
        if continuous:
            click.echo(f"Starting continuous assignment for user {assignee} on team {team} (interval: {interval}s)")
            click.echo("Press Ctrl+C to stop")
            snow_handler.run_continuous_assignment(team, assignee, interval)
        else:
            click.echo(f"Running single assignment cycle for user {assignee} on team {team}")
            stats = snow_handler.run_auto_assignment(team, assignee)
            click.echo(f"✓ Assignment complete: {stats}")
            
    except KeyboardInterrupt:
        click.echo("\n✓ Assignment stopped by user")
    except Exception as e:
        click.echo(f"✗ Error in ServiceNow assignment: {e}", err=True)
        sys.exit(1)

@snow.command()
@click.argument('team')
@click.option('--limit', '-l', default=10, help='Maximum number of tickets to show')
@click.pass_context
def list_tickets(ctx, team, limit):
    """List unassigned tickets for a team."""
    config = ctx.obj['config']
    
    try:
        snow_handler = ServiceNowHandler(config)
        
        if not snow_handler.test_connection():
            click.echo("✗ Failed to connect to ServiceNow", err=True)
            sys.exit(1)
            
        tickets = snow_handler.get_unassigned_tickets(team, limit)
        
        if not tickets:
            click.echo(f"No unassigned tickets found for team {team}")
            return
            
        click.echo(f"\nUnassigned tickets for team {team}:")
        click.echo("=" * 80)
        
        for ticket in tickets:
            click.echo(f"Number: {ticket.get('number', 'N/A')}")
            click.echo(f"Description: {ticket.get('short_description', 'N/A')}")
            click.echo(f"Contact: {ticket.get('contact_source', 'N/A')}")
            click.echo(f"State: {ticket.get('state', 'N/A')}")
            click.echo("-" * 40)
            
    except Exception as e:
        click.echo(f"✗ Error listing tickets: {e}", err=True)
        sys.exit(1)

@snow.command()
@click.pass_context
def test(ctx):
    """Test ServiceNow and LMS connections."""
    config = ctx.obj['config']
    
    try:
        snow_handler = ServiceNowHandler(config)
        
        # Test ServiceNow connection
        if snow_handler.test_connection():
            click.echo("✓ ServiceNow connection successful")
        else:
            click.echo("✗ ServiceNow connection failed")
            
        # Test LMS token
        token = snow_handler.get_lms_token()
        if token:
            click.echo("✓ LMS token obtained successfully")
            
            # Test name lookup
            test_name = snow_handler.lookup_user_name("carias")
            click.echo(f"✓ LMS name lookup test: 'carias' -> '{test_name}'")
        else:
            click.echo("✗ LMS token failed (credentials may not be configured)")
            
    except Exception as e:
        click.echo(f"✗ Error testing connections: {e}", err=True)
        sys.exit(1)

@cli.group()
@click.pass_context
def jira(ctx):
    """Jira operations commands."""
    click.echo("Jira commands not yet implemented. Coming soon!")
    # TODO: Implement Jira commands when JiraHandler is ready

@cli.group()
@click.pass_context
def snowai(ctx):
    """SNOW AI processing commands (ROL navigation via LabManager)."""
    pass

@snowai.command()
@click.argument('course_id')
@click.argument('course_url')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.option('--description', '-d', default='', help='Student feedback description to classify/analyze')
@click.pass_context
def analyze(ctx, course_id, course_url, env, browser, headless, description):
    """Analyze a feedback against a course page using the new framework."""
    config = ctx.obj['config']
    try:
        processor = SnowAIProcessor(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()

        # Ensure login and start lab adjustments similarly to original flow
        processor.start_lab_for_course(course_id=course_id, environment=env)

        # Fetch section info and guide text
        section, guide_text = processor.fetch_section_and_guide_text(course_url)

        click.echo(f"Section: {section}")
        click.echo(f"Guide text length: {len(guide_text)} characters")

        if not description:
            click.echo("No description provided for analysis (-d). Skipping LLM analysis.")
            return

        classification = processor.classify_ticket_llm(description)
        click.echo(f"Classification: {json.dumps(classification, ensure_ascii=False)}")

        if classification.get('language') and classification.get('language') != 'en':
            translated = processor.translate_text(description, classification.get('language'))
        else:
            translated = description

        analysis = {}
        if classification.get('is_content_issue_ticket'):
            analysis = processor.analyze_content_issue(description, guide_text)
        elif classification.get('is_environment_issue'):
            analysis = processor.analyze_environment_issue(description)

        click.echo(f"Analysis: {json.dumps(analysis, ensure_ascii=False)}")

    except Exception as e:
        click.echo(f"✗ Error in SNOW AI analyze: {e}", err=True)
        raise
    finally:
        try:
            processor.close()
        except Exception:
            pass

@snowai.command()
@click.option('-t', '--ticket', 'tickets', multiple=True, help='SNOW ticket number (repeatable). If omitted, uses user queue.')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def run(ctx, tickets, env, browser, headless):
    """Process SNOW tickets by number, or default to user's feedback queue when none provided."""
    config = ctx.obj['config']
    try:
        processor = SnowAIProcessor(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        results = processor.run(list(tickets) if tickets else None, environment=env)
        # Print concise results
        for r in results:
            click.echo(f"{r['snow']['snow_id']}: {r['classification'].get('summary','')} | content_issue={r['classification'].get('is_content_issue_ticket')} env_issue={r['classification'].get('is_environment_issue')}")
    except Exception as e:
        click.echo(f"✗ Error running SNOW AI queue processor: {e}", err=True)
        raise
    finally:
        try:
            processor.close()
        except Exception:
            pass

@snowai.command(name='run-windowed')
@click.option('-t', '--ticket', 'tickets', multiple=True, help='SNOW ticket number (repeatable). If omitted, uses user queue.')
@click.option('--env', '-e', default='rol', help='Lab environment (rol, rol-stage, china)')
@click.option('--browser', '-b', default='firefox', help='Browser to use (firefox, chrome)')
@click.option('--headless/--no-headless', default=False, help='Run browser in headless mode')
@click.pass_context
def run_windowed(ctx, tickets, env, browser, headless):
    """Open one window per ticket. For each window: SNOW ticket, ROL section, Jira search, Jira create."""
    config = ctx.obj['config']
    try:
        processor = SnowAIProcessor(config=config, browser_name=browser, is_headless=headless)
        reset_step_counter()
        processor.run_windowed(list(tickets) if tickets else None, environment=env)
        click.echo("✓ Opened windows/tabs. You can now work each ticket in its own window.")
    except Exception as e:
        click.echo(f"✗ Error running windowed flow: {e}", err=True)
        raise
    finally:
        # Intentionally do not close the browser; the user will work in the windows
        pass

@cli.command()
@click.pass_context
def config(ctx):
    """Show current configuration settings."""
    config = ctx.obj['config']
    
    click.echo("Current Configuration:")
    click.echo("=" * 50)
    
    # General settings
    click.echo("\n[General]")
    click.echo(f"Default Selenium Driver: {config.get('General', 'default_selenium_driver', 'firefox')}")
    click.echo(f"Default Lab Environment: {config.get('General', 'default_lab_environment', 'rol')}")
    click.echo(f"Debug Mode: {config.get('General', 'debug_mode', False)}")
    
    # Lab environments
    click.echo("\n[Lab Environments]")
    for env in ['rol', 'rol-stage', 'china']:
        url = config.get_lab_base_url(env)
        if url:
            click.echo(f"{env}: {url}")
    
    # Credentials (just show if they're set, not the values)
    click.echo("\n[Credentials Status]")
    cred_keys = [
        'RH_USERNAME', 'RH_PIN', 'GITHUB_USERNAME', 'GITHUB_PASSWORD',
        'CHINA_USERNAME', 'CHINA_PASSWORD', 'SNOW_API_USER', 'JIRA_API_USER'
    ]
    for key in cred_keys:
        value = config.get('Credentials', key)
        status = "✓ Set" if value else "✗ Not set"
        click.echo(f"{key}: {status}")

def main():
    """Main entry point."""
    cli()

if __name__ == '__main__':
    main() 