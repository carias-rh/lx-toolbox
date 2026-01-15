#!/usr/bin/env python3
"""
Course Resolver - Resolves short course names to full course IDs.

Examples:
    "199" → "rh199-9.3"
    "do180" → "do180-4.18"
    "do180ea" → "do180ea-4.14"
    "do180-4.14" → "do180-4.14" (exact match)
"""

import os
import re
from pathlib import Path
from typing import Optional


def get_courses_list_path() -> Path:
    """Get the path to courses-list.txt file."""
    # Try multiple locations
    possible_paths = [
        # Relative to this module (in lx_toolbox/utils/)
        Path(__file__).parent.parent.parent / "courses-list.txt",
        # System-wide installation
        Path("/usr/share/lx-toolbox/courses-list.txt"),
        # User config directory
        Path.home() / ".config" / "lx-toolbox" / "courses-list.txt",
        # Current directory
        Path("courses-list.txt"),
    ]
    
    for path in possible_paths:
        if path.exists():
            return path
    
    raise FileNotFoundError(
        "courses-list.txt not found. Expected locations:\n" +
        "\n".join(f"  - {p}" for p in possible_paths)
    )


def load_courses_list(courses_file: Optional[Path] = None) -> list[str]:
    """Load the list of available courses."""
    if courses_file is None:
        courses_file = get_courses_list_path()
    
    with open(courses_file, 'r') as f:
        courses = [line.strip().strip('"') for line in f if line.strip()]
    
    return courses


def parse_version(version_str: str) -> tuple[int, int]:
    """
    Parse a version string like "9.3" or "4.14" into (major, minor) tuple.
    Handles formats like "1.22", "4.12.2", etc.
    """
    parts = version_str.split('.')
    try:
        major = int(parts[0]) if parts else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return (0, 0)


def resolve_course(short_name: str, courses_file: Optional[Path] = None) -> str:
    """
    Resolve a short course name to the full course ID with latest version.
    
    Args:
        short_name: Short course identifier (e.g., "199", "do180", "do180ea", "do180-4.14")
        courses_file: Optional path to courses-list.txt
        
    Returns:
        Full course ID (e.g., "rh199-9.3", "do180-4.18")
        
    Raises:
        ValueError: If course is not found
        
    Examples:
        >>> resolve_course("199")
        'rh199-9.3'
        >>> resolve_course("do180")
        'do180-4.18'
        >>> resolve_course("do180ea")
        'do180ea-4.14'
        >>> resolve_course("do180-4.14")
        'do180-4.14'
    """
    courses = load_courses_list(courses_file)
    short_name = short_name.lower().strip()
    
    # Check if it's already a full course ID (contains a dash with version)
    if '-' in short_name:
        # Might be a full course ID like "do180-4.14"
        if short_name in courses:
            return short_name
        
        # Split into base and version parts (e.g., "180-4.14" → base="180", version="4.14")
        parts = short_name.rsplit('-', 1)
        if len(parts) == 2:
            base_part, version_part = parts
            
            # Determine if user wants EA version
            want_ea = base_part.endswith('ea')
            base_name = base_part.rstrip('ea') if want_ea else base_part
            
            # Handle numeric-only base (e.g., "180" → "do180")
            if base_name.isdigit():
                # Common prefixes to try
                prefixes = ['rh', 'do', 'ad', 'ai', 'cl', 'cs', 'jb', 'er', 'hol', 'tl', 'ws', 'bfx', 'ceph']
                for prefix in prefixes:
                    search_pattern = f"{prefix}{base_name}"
                    # Look for exact match with the specified version
                    if want_ea:
                        full_match = f"{search_pattern}ea-{version_part}"
                    else:
                        full_match = f"{search_pattern}-{version_part}"
                    
                    # Check exact version match first
                    if full_match in courses:
                        return full_match
                    
                    # Check if any version of this course exists
                    matching = [c for c in courses if c.startswith(search_pattern + '-') or c.startswith(search_pattern + 'ea-')]
                    if matching:
                        # Found the right prefix, but exact version doesn't exist - fall back to latest
                        # Filter by EA preference
                        if want_ea:
                            filtered = [c for c in matching if c.startswith(search_pattern + 'ea-')]
                        else:
                            filtered = [c for c in matching if not c.startswith(search_pattern + 'ea-')]
                        
                        if filtered:
                            # Sort by version and return latest
                            matching_courses = []
                            for course in filtered:
                                if '-' not in course:
                                    continue
                                course_name, course_version = course.rsplit('-', 1)
                                matching_courses.append((course, course_version))
                            
                            if matching_courses:
                                matching_courses.sort(key=lambda x: parse_version(x[1]), reverse=True)
                                return matching_courses[0][0]
                        # If we found the prefix but no matching versions (EA/non-EA), continue to next prefix
                else:
                    # No prefix found that matches any version
                    raise ValueError(f"Course '{short_name}' not found. No course found matching numeric base '{base_name}'.")
            else:
                # Non-numeric base - try direct match first
                if want_ea:
                    search_base = f"{base_name}ea"
                else:
                    search_base = base_name
                
                # Look for exact version match
                exact_match = f"{search_base}-{version_part}"
                if exact_match in courses:
                    return exact_match
                
                # Check if base exists with other versions - fall back to latest
                matching = [c for c in courses if c.startswith(search_base + '-')]
                if matching:
                    # Exact version not found, but course exists - return latest version
                    matching_courses = []
                    for course in matching:
                        if '-' not in course:
                            continue
                        course_name, course_version = course.rsplit('-', 1)
                        matching_courses.append((course, course_version))
                    
                    if matching_courses:
                        matching_courses.sort(key=lambda x: parse_version(x[1]), reverse=True)
                        return matching_courses[0][0]
                
                # If direct match failed, try extracting numeric digits (first 3 digits)
                # This handles cases like "d180" → extract "180"
                numeric_match = re.search(r'\d{3,}', base_name)
                if numeric_match:
                    extracted_number = numeric_match.group()
                    # Try resolving with the extracted number
                    try:
                        # Recursively resolve using the numeric part
                        numeric_input = f"{extracted_number}-{version_part}"
                        return resolve_course(numeric_input, courses_file)
                    except ValueError:
                        # If that fails, try without version (get latest)
                        try:
                            return resolve_course(extracted_number, courses_file)
                        except ValueError:
                            pass
                
                raise ValueError(f"Course '{short_name}' not found in courses list.")
        # If split failed or no version part, continue with general resolution
    
    # General resolution (no version specified, or version not found)
    # Determine if user wants EA version
    want_ea = short_name.endswith('ea')
    base_name = short_name.rstrip('ea') if want_ea else short_name
    
    # Handle numeric-only input (e.g., "199" → "rh199")
    if base_name.isdigit():
        # Common prefixes to try
        prefixes = ['rh', 'do', 'ad', 'ai', 'cl', 'cs', 'jb', 'er', 'hol', 'tl', 'ws', 'bfx', 'ceph']
        for prefix in prefixes:
            search_pattern = f"{prefix}{base_name}"
            matching = [c for c in courses if c.startswith(search_pattern + '-') or c.startswith(search_pattern + 'ea-')]
            if matching:
                base_name = search_pattern
                break
        else:
            # No prefix found, try direct match
            pass
    
    # Build search pattern
    if want_ea:
        search_base = f"{base_name}ea"
    else:
        search_base = base_name
    
    # Find all matching courses
    matching_courses = []
    for course in courses:
        # Course format: name-version (e.g., "do180-4.18", "do180ea-4.14")
        if '-' not in course:
            continue
            
        course_name, course_version = course.rsplit('-', 1)
        
        if want_ea:
            # Looking for EA version: must match exactly "nameea"
            if course_name.lower() == search_base:
                matching_courses.append((course, course_version))
        else:
            # Looking for non-EA version: must match name but NOT end with 'ea'
            if course_name.lower() == search_base and not course_name.lower().endswith('ea'):
                matching_courses.append((course, course_version))
    
    if not matching_courses:
        # Try extracting numeric digits from base_name (handles cases like "d180" → "180")
        numeric_match = re.search(r'\d{3,}', base_name)
        if numeric_match:
            extracted_number = numeric_match.group()
            # Recursively resolve using the extracted number
            try:
                return resolve_course(extracted_number, courses_file)
            except ValueError:
                pass
        
        raise ValueError(f"Course '{short_name}' not found in courses list")
    
    # Find the latest version
    # Sort by (major_version, minor_version) descending
    def version_key(item):
        course, version = item
        return parse_version(version)
    
    matching_courses.sort(key=version_key, reverse=True)
    
    # Return the course with highest version
    return matching_courses[0][0]


def resolve_course_safe(short_name: str, courses_file: Optional[Path] = None) -> tuple[str, Optional[str]]:
    """
    Safe version of resolve_course that returns (resolved_name, error_message).
    
    Returns:
        Tuple of (resolved_course_id, None) on success
        Tuple of (original_name, error_message) on failure
    """
    try:
        resolved = resolve_course(short_name, courses_file)
        return (resolved, None)
    except (ValueError, FileNotFoundError) as e:
        return (short_name, str(e))


def list_course_versions(short_name: str, courses_file: Optional[Path] = None) -> list[str]:
    """
    List all available versions of a course.
    
    Args:
        short_name: Course base name (e.g., "do180", "rh199")
        
    Returns:
        List of all matching course IDs sorted by version (latest first)
    """
    courses = load_courses_list(courses_file)
    short_name = short_name.lower().strip()
    
    # Handle numeric-only input
    if short_name.isdigit():
        prefixes = ['rh', 'do', 'ad', 'ai', 'cl', 'cs', 'jb', 'er', 'hol', 'tl', 'ws', 'bfx', 'ceph']
        for prefix in prefixes:
            search_pattern = f"{prefix}{short_name}"
            matching = [c for c in courses if c.startswith(search_pattern + '-') or c.startswith(search_pattern + 'ea-')]
            if matching:
                short_name = search_pattern
                break
    
    # Find all versions (including EA)
    matching = []
    for course in courses:
        if '-' not in course:
            continue
        course_name, version = course.rsplit('-', 1)
        # Match base name or base name + ea
        base_without_ea = course_name.rstrip('ea').lower() if course_name.lower().endswith('ea') else course_name.lower()
        if base_without_ea == short_name.rstrip('ea'):
            matching.append((course, version, course_name.lower().endswith('ea')))
    
    # Sort: non-EA first, then by version descending
    def sort_key(item):
        course, version, is_ea = item
        major, minor = parse_version(version)
        return (is_ea, -major, -minor)
    
    matching.sort(key=sort_key)
    return [item[0] for item in matching]


if __name__ == '__main__':
    # Simple CLI for testing
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: course_resolver.py <course_name> [--list]")
        print("\nExamples:")
        print("  course_resolver.py 199        → rh199-9.3")
        print("  course_resolver.py do180      → do180-4.18")
        print("  course_resolver.py do180ea    → do180ea-4.14")
        print("  course_resolver.py do180 --list  → list all versions")
        sys.exit(1)
    
    course = sys.argv[1]
    
    if '--list' in sys.argv:
        try:
            versions = list_course_versions(course)
            print(f"Available versions for '{course}':")
            for v in versions:
                print(f"  {v}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            resolved = resolve_course(course)
            print(resolved)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

