#!/usr/bin/env python3
"""
LX Toolbox CLI - Main entry point for lab automation tools.
"""

import click
import sys
import os
from pathlib import Path

from .utils.config_manager import ConfigManager
from .utils.helpers import reset_step_counter
from .core.lab_manager import LabManager

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

@click.group()
@click.pass_context
def cli(ctx):
    """LX Toolbox - Automation tools for lab operations, ServiceNow, and Jira."""
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
        
        if primary_status == "START":
            lab_mgr.start_lab(course_id=course_id)
        elif primary_status in ["STOP", "STOPPING"]:
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

@cli.group()
@click.pass_context
def snow(ctx):
    """ServiceNow operations commands."""
    click.echo("ServiceNow commands not yet implemented. Coming soon!")
    # TODO: Implement ServiceNow commands when ServiceNowHandler is ready

@cli.group()
@click.pass_context
def jira(ctx):
    """Jira operations commands."""
    click.echo("Jira commands not yet implemented. Coming soon!")
    # TODO: Implement Jira commands when JiraHandler is ready

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