import json
import logging
import uuid
from datetime import datetime, timezone, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, redirect, render_template, request, url_for
from zoneinfo import ZoneInfo

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "data" / "state.json"

TZ_ALIASES: Dict[str, str] = {
    "UTC": "UTC",
    "GMT": "Etc/GMT",
    "BST": "Europe/London",
    "CET": "Europe/Berlin",
    "CEST": "Europe/Berlin",
    "EET": "Europe/Bucharest",
    "EEST": "Europe/Bucharest",
    "WET": "Europe/Lisbon",
    "WEST": "Europe/Lisbon",
    "IST": "Asia/Kolkata",
    "PKT": "Asia/Karachi",
    "JST": "Asia/Tokyo",
    "KST": "Asia/Seoul",
    "CST_ASIA": "Asia/Shanghai",
    "SGT": "Asia/Singapore",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    "NZST": "Pacific/Auckland",
    "NZDT": "Pacific/Auckland",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "BRT": "America/Sao_Paulo",
    "ART": "America/Argentina/Buenos_Aires",
    "CLT": "America/Santiago",
    "COT": "America/Bogota",
    "PET": "America/Lima",
    "MXT": "America/Mexico_City",
}


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def ensure_data_file() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        initial_state = {"groups": [], "members": [], "group_priorities": [], "rr": {}}
        DATA_FILE.write_text(json.dumps(initial_state, indent=2))


def load_state() -> Dict:
    ensure_data_file()
    return json.loads(DATA_FILE.read_text())


def save_state(state: Dict) -> None:
    DATA_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def canonicalize_timezone_name(tz_name: str) -> str:
    name = (tz_name or "").strip()
    if not name:
        raise ValueError("Timezone required")
    try:
        ZoneInfo(name)
        return name
    except Exception:
        pass
    alias = name.upper()
    if alias in TZ_ALIASES:
        ZoneInfo(TZ_ALIASES[alias])
        return TZ_ALIASES[alias]
    raise ValueError(
        f"Unknown timezone '{tz_name}'. Use IANA (e.g. 'Europe/Berlin') "
        f"or a supported abbreviation: {', '.join(sorted(TZ_ALIASES.keys()))}"
    )


def _parse_time_of_day(hhmm: str) -> time:
    hhmm = (hhmm or "").strip()
    if not hhmm:
        raise ValueError("Time value required")
    parts = hhmm.split(":")
    if len(parts) != 2:
        raise ValueError("Time must be HH:MM")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Time must be HH:MM (00:00-23:59)")
    return time(hour=h, minute=m)


def get_member_map(state: Dict) -> Dict[str, Dict]:
    return {m["id"]: m for m in state.get("members", [])}


def get_group_map(state: Dict) -> Dict[str, Dict]:
    return {g["id"]: g for g in state.get("groups", [])}


def get_priorities_for_group(state: Dict, group_id: str) -> List[Dict]:
    """Return priority entries for *group_id*, sorted ascending by level."""
    prios = [p for p in state.get("group_priorities", []) if p["group_id"] == group_id]
    prios.sort(key=lambda p: p["priority"])
    return prios


def resolve_group_id(state: Dict, *, group_id: str = None, assignment_group_id: str = None) -> Optional[str]:
    """Resolve a group from either its slug or a SNOW assignment_group sys_id."""
    if group_id:
        if any(g["id"] == group_id for g in state.get("groups", [])):
            return group_id
    if assignment_group_id:
        for g in state.get("groups", []):
            if assignment_group_id in g.get("assignment_group_ids", []):
                return g["id"]
    return None


# ---------------------------------------------------------------------------
# Availability & routing
# ---------------------------------------------------------------------------

def is_member_available(member: Dict, now_utc: datetime) -> bool:
    """True when *member* is within their configured working hours right now."""
    tz_name = member.get("timezone", "UTC")
    try:
        tz = ZoneInfo(canonicalize_timezone_name(tz_name))
    except Exception:
        return False

    now_local = now_utc.astimezone(tz)

    working_days = member.get("working_days", [0, 1, 2, 3, 4])
    if now_local.weekday() not in working_days:
        return False

    try:
        start = _parse_time_of_day(member.get("working_hours_start", "09:00"))
        end = _parse_time_of_day(member.get("working_hours_end", "17:30"))
    except Exception:
        return False

    cur = now_local.time()
    if start <= end:
        return start <= cur < end
    # Overnight window (e.g. 22:00–06:00)
    return cur >= start or cur < end


def get_assignee_for_group(
    state: Dict, group_id: str, now_utc: datetime = None, *, persist_rr: bool = True,
) -> Tuple[Optional[Dict], Optional[int], bool]:
    """Walk the priority chain for *group_id* and return the best available member.

    Returns ``(member_dict, priority_level, is_round_robin)``.
    When *persist_rr* is True the round-robin index is saved to state (used by
    the ``/api/shift`` endpoint).  Set to False for read-only status views.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    member_map = get_member_map(state)
    priorities = get_priorities_for_group(state, group_id)

    for prio_entry in priorities:
        level = prio_entry["priority"]
        member_ids = prio_entry.get("member_ids", [])

        available = [
            member_map[mid]
            for mid in member_ids
            if mid in member_map and is_member_available(member_map[mid], now_utc)
        ]
        if not available:
            continue

        if len(available) == 1:
            return available[0], level, False

        # Round-robin among available members at this priority
        rr_key = f"{group_id}|{level}"
        rr_map = state.get("rr", {})
        last_member_id = rr_map.get(rr_key)

        next_idx = 0
        if last_member_id:
            for i, m in enumerate(available):
                if m["id"] == last_member_id:
                    next_idx = (i + 1) % len(available)
                    break

        selected = available[next_idx]

        if persist_rr:
            rr_map[rr_key] = selected["id"]
            state["rr"] = rr_map
            save_state(state)

        return selected, level, True

    return None, None, False


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/shift", methods=["GET"])
def api_shift():
    """Return the current assignee for a group.

    Query params (at least one required):
      - ``group``: group slug (e.g. ``anz``, ``japan``)
      - ``assignment_group_id``: SNOW assignment_group sys_id
    """
    state = load_state()
    gid = resolve_group_id(
        state,
        group_id=request.args.get("group"),
        assignment_group_id=request.args.get("assignment_group_id"),
    )

    if not gid:
        return jsonify({"id": None, "name": None, "on_shift": False, "round_robin": False}), 404

    member, level, is_rr = get_assignee_for_group(state, gid)

    if not member:
        return jsonify({
            "id": None, "name": None, "on_shift": False,
            "round_robin": False, "group": gid,
        })

    return jsonify({
        "id": member["id"], "name": member["name"],
        "on_shift": True, "round_robin": is_rr,
        "group": gid, "priority": level,
    })


@app.route("/api/status", methods=["GET"])
def api_status():
    """Read-only overview of every group's availability (no round-robin side-effects)."""
    state = load_state()
    now_utc = datetime.now(timezone.utc)
    member_map = get_member_map(state)

    groups_out = []
    for group in state.get("groups", []):
        prios_out = []
        for prio in get_priorities_for_group(state, group["id"]):
            members_info = []
            for mid in prio.get("member_ids", []):
                m = member_map.get(mid)
                if m:
                    members_info.append({
                        "id": m["id"], "name": m["name"],
                        "available": is_member_available(m, now_utc),
                    })
            prios_out.append({"priority": prio["priority"], "members": members_info})

        assignee, lvl, is_rr = get_assignee_for_group(state, group["id"], now_utc, persist_rr=False)
        groups_out.append({
            "id": group["id"], "name": group["name"],
            "priorities": prios_out,
            "current_assignee": assignee["name"] if assignee else None,
            "current_priority": lvl,
        })

    members_out = []
    for m in state.get("members", []):
        members_out.append({
            "id": m["id"], "name": m["name"],
            "timezone": m.get("timezone", "UTC"),
            "available": is_member_available(m, now_utc),
            "working_hours": f"{m.get('working_hours_start', '09:00')}-{m.get('working_hours_end', '17:30')}",
        })

    return jsonify({
        "timestamp_utc": now_utc.isoformat(),
        "groups": groups_out,
        "members": members_out,
    })


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    state = load_state()
    now_utc = datetime.now(timezone.utc)
    member_map = get_member_map(state)

    member_availability = {m["id"]: is_member_available(m, now_utc) for m in state.get("members", [])}

    group_assignees = {}
    for g in state.get("groups", []):
        assignee, lvl, is_rr = get_assignee_for_group(state, g["id"], now_utc, persist_rr=False)
        group_assignees[g["id"]] = {
            "member": assignee, "priority": lvl, "round_robin": is_rr,
        }

    return render_template(
        "index.html",
        groups=state.get("groups", []),
        members=state.get("members", []),
        group_priorities=state.get("group_priorities", []),
        member_map=member_map,
        member_availability=member_availability,
        group_assignees=group_assignees,
        now_utc=now_utc,
    )


# ---------------------------------------------------------------------------
# CRUD — Members
# ---------------------------------------------------------------------------

@app.route("/members/add", methods=["POST"])
def add_member():
    state = load_state()
    name = request.form.get("name", "").strip()
    tz = request.form.get("timezone", "UTC").strip()
    start = request.form.get("working_hours_start", "09:00").strip()
    end = request.form.get("working_hours_end", "17:30").strip()
    days = request.form.getlist("working_days")

    if not name:
        return "Name required", 400
    try:
        canonical_tz = canonicalize_timezone_name(tz)
    except Exception as e:
        return f"Invalid timezone: {e}", 400
    try:
        _parse_time_of_day(start)
        _parse_time_of_day(end)
    except Exception as e:
        return f"Invalid time: {e}", 400

    days_int = [int(d) for d in days] if days else [0, 1, 2, 3, 4]

    new_member = {
        "id": str(uuid.uuid4()),
        "name": name,
        "timezone": canonical_tz,
        "working_hours_start": start,
        "working_hours_end": end,
        "working_days": days_int,
    }
    state.setdefault("members", []).append(new_member)
    state["members"].sort(key=lambda m: m["name"].lower())
    save_state(state)
    logging.info("Added member: %s (%s %s-%s)", name, canonical_tz, start, end)
    return redirect(url_for("index"))


@app.route("/members/delete/<member_id>", methods=["POST"])
def delete_member(member_id: str):
    state = load_state()
    state["members"] = [m for m in state.get("members", []) if m["id"] != member_id]
    for prio in state.get("group_priorities", []):
        if member_id in prio.get("member_ids", []):
            prio["member_ids"].remove(member_id)
    state["group_priorities"] = [p for p in state.get("group_priorities", []) if p.get("member_ids")]
    save_state(state)
    logging.info("Deleted member: %s", member_id)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# CRUD — Groups
# ---------------------------------------------------------------------------

@app.route("/groups/add", methods=["POST"])
def add_group():
    state = load_state()
    gid = request.form.get("id", "").strip().lower().replace(" ", "-")
    name = request.form.get("name", "").strip()
    snow_queue = request.form.get("snow_queue", "").strip()
    agids_raw = request.form.get("assignment_group_ids", "").strip()

    if not gid or not name:
        return "ID and Name are required", 400
    if any(g["id"] == gid for g in state.get("groups", [])):
        return f"Group '{gid}' already exists", 400

    new_group: Dict = {"id": gid, "name": name}
    if snow_queue:
        new_group["snow_queue"] = snow_queue
    if agids_raw:
        new_group["assignment_group_ids"] = [a.strip() for a in agids_raw.split(",") if a.strip()]

    state.setdefault("groups", []).append(new_group)
    save_state(state)
    logging.info("Added group: %s (%s)", gid, name)
    return redirect(url_for("index"))


@app.route("/groups/delete/<group_id>", methods=["POST"])
def delete_group(group_id: str):
    state = load_state()
    state["groups"] = [g for g in state.get("groups", []) if g["id"] != group_id]
    state["group_priorities"] = [p for p in state.get("group_priorities", []) if p["group_id"] != group_id]
    save_state(state)
    logging.info("Deleted group: %s", group_id)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# CRUD — Priority assignments
# ---------------------------------------------------------------------------

@app.route("/priorities/add", methods=["POST"])
def add_priority():
    state = load_state()
    gid = request.form.get("group_id", "").strip()
    member_id = request.form.get("member_id", "").strip()
    try:
        level = int(request.form.get("priority", "1"))
    except ValueError:
        return "Priority must be an integer", 400

    if not gid or not member_id:
        return "group_id and member_id are required", 400

    existing = next(
        (p for p in state.get("group_priorities", []) if p["group_id"] == gid and p["priority"] == level),
        None,
    )
    if existing:
        if member_id not in existing["member_ids"]:
            existing["member_ids"].append(member_id)
    else:
        state.setdefault("group_priorities", []).append({
            "group_id": gid, "priority": level, "member_ids": [member_id],
        })
    save_state(state)
    logging.info("Added priority: group=%s level=%d member=%s", gid, level, member_id)
    return redirect(url_for("index"))


@app.route("/priorities/remove", methods=["POST"])
def remove_priority():
    state = load_state()
    gid = request.form.get("group_id", "").strip()
    member_id = request.form.get("member_id", "").strip()
    try:
        level = int(request.form.get("priority", "0"))
    except ValueError:
        return "Invalid priority", 400

    for p in state.get("group_priorities", []):
        if p["group_id"] == gid and p["priority"] == level:
            if member_id in p.get("member_ids", []):
                p["member_ids"].remove(member_id)
            break
    state["group_priorities"] = [p for p in state.get("group_priorities", []) if p.get("member_ids")]
    save_state(state)
    logging.info("Removed priority: group=%s level=%d member=%s", gid, level, member_id)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
