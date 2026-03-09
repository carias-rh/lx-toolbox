#!/usr/bin/env python3
"""
Test script to fetch ServiceNow assignment group sys_ids by group name.
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from lx_toolbox.utils.config_manager import ConfigManager
from lx_toolbox.core.servicenow_autoassign import ServiceNowAutoAssign

CONFIG_PATH = project_root / "config" / "config.ini"
ENV_PATH = project_root / ".env"


TARGET_GROUPS = [
    # APAC
    "ANZ GLS Support",
    "ASEAN GLS Support",
    "GCG GLS Support",
    "India GLS Support",
    "Japan GLS Support",
    "KR GLS Support",
    "GLS CX Internal - APAC Bangalore",
    # LATAM
    "GLS Customer Experience - BR",
    "GLS Customer Experience - MX",
    "GLS Customer Experience - LATAM",
    "GLS CX Internal - LATAM Bangalore",
    # NA
    "GLS Customer Experience - NAMER",
    "GLS CX Internal - NAMER",
    # EMEA
    "GLS CX Internal - EMEA Bangalore",
    "Türkiye GLS Support",
    "IGC GLS Support",
    "GLS Customer Experience - EMEA",
]


def fetch_group_id(snow: ServiceNowAutoAssign, group_name: str) -> dict | None:
    """Fetch a ServiceNow assignment group by its exact name.

    Returns the group record dict or None if not found.
    """
    url = f"{snow.instance_url}/api/now/table/sys_user_group"
    params = {
        "sysparm_query": f"name={group_name}",
        "sysparm_fields": "sys_id,name,description",
        "sysparm_limit": "1",
    }
    response = snow.session.get(url, params=params)
    response.raise_for_status()
    results = response.json().get("result", [])
    return results[0] if results else None


def main():
    config = ConfigManager(config_file_path=str(CONFIG_PATH), env_file_path=str(ENV_PATH))
    snow = ServiceNowAutoAssign(config)

    if not snow.test_connection():
        print("❌ ServiceNow connection failed")
        return

    print("🔍 Fetching assignment group sys_ids by name")
    print("=" * 70)

    found = 0
    for name in TARGET_GROUPS:
        group = fetch_group_id(snow, name)
        if group:
            found += 1
            print(f"  ✅ {name}")
            print(f"     sys_id: {group['sys_id']}")
            if group.get("description"):
                print(f"     description: {group['description']}")
        else:
            print(f"  ❌ {name} — not found")

    print("=" * 70)
    print(f"📊 Found {found}/{len(TARGET_GROUPS)} groups")


if __name__ == "__main__":
    main()
