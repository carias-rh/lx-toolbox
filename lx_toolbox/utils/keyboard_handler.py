"""
Non-blocking keyboard input handler for interactive QA control.

Provides pause/resume and quit functionality during QA command execution.
Uses select/termios on Unix and msvcrt on Windows.
"""

import sys
import os
from contextlib import contextmanager

# Platform-specific imports
if os.name == 'nt':  # Windows
    import msvcrt
else:  # Unix/Linux/Mac
    import select
    import termios
    import tty


class KeyboardHandler:
    """
    Non-blocking keyboard input handler for interactive control.
    
    Supports:
    - 'p' to toggle pause/resume
    - 'q' to quit
    
    Usage:
        handler = KeyboardHandler()
        handler.start()
        try:
            while running:
                key = handler.check_keypress()
                if key == 'p':
                    # toggle pause
                elif key == 'q':
                    # quit
                # ... do work ...
        finally:
            handler.stop()
    """
    
    def __init__(self):
        self._old_settings = None
        self._is_active = False
    
    def start(self):
        """
        Initialize terminal for non-blocking input.
        Must be called before check_keypress().
        """
        if os.name != 'nt':  # Unix
            try:
                self._old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
                self._is_active = True
            except termios.error:
                # Not a terminal (e.g., running in a pipe)
                self._is_active = False
        else:  # Windows
            self._is_active = True
    
    def stop(self):
        """
        Restore terminal to original settings.
        Must be called when done with keyboard handling.
        """
        if os.name != 'nt' and self._old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)
            except termios.error:
                pass
        self._is_active = False
        self._old_settings = None
    
    def check_keypress(self, timeout: float = 0.0) -> str | None:
        """
        Check for a keypress without blocking.
        
        Args:
            timeout: Maximum time to wait for input (0 = non-blocking)
            
        Returns:
            The key pressed as a string, or None if no key was pressed.
        """
        if not self._is_active:
            return None
            
        if os.name == 'nt':  # Windows
            if msvcrt.kbhit():
                return msvcrt.getch().decode('utf-8', errors='ignore').lower()
            return None
        else:  # Unix
            try:
                # Check if input is available
                readable, _, _ = select.select([sys.stdin], [], [], timeout)
                if readable:
                    return sys.stdin.read(1).lower()
            except (select.error, ValueError):
                pass
            return None
    
    def wait_for_key(self, valid_keys: list[str] = None) -> str:
        """
        Block until a key is pressed.
        
        Args:
            valid_keys: Optional list of valid keys to wait for.
                       If None, returns on any keypress.
                       
        Returns:
            The key pressed as a string.
        """
        while True:
            key = self.check_keypress(timeout=0.1)
            if key is not None:
                if valid_keys is None or key in valid_keys:
                    return key
    
    @contextmanager
    def pause(self):
        """Temporarily restore normal terminal settings so input() echoes keys.

        Usage::

            answer = None
            with handler.pause():
                answer = input("Prompt: ")
        """
        if os.name != 'nt' and self._old_settings is not None and self._is_active:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)
            except termios.error:
                pass
            try:
                yield
            finally:
                try:
                    tty.setcbreak(sys.stdin.fileno())
                except termios.error:
                    pass
        else:
            yield

    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


def print_status(command_num: int, total_commands: int, is_paused: bool, command: str = None):
    """
    Print the QA status bar.
    
    Args:
        command_num: Current command number (1-indexed)
        total_commands: Total number of commands
        is_paused: Whether execution is paused
        command: Optional current command being executed
    """
    if is_paused:
        status = "\r\033[K[QA] PAUSED - Press [P] to resume, [Q] to quit"
    else:
        status = f"\r\033[K[QA] Command {command_num}/{total_commands} | RUNNING | [P]ause [Q]uit"
    
    # Print status without newline, flush to ensure it's displayed
    print(status, end='', flush=True)


def clear_status_line():
    """Clear the current status line."""
    print("\r\033[K", end='', flush=True)
